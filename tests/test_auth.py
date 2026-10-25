import time

import pytest
from fastapi.testclient import TestClient

from app.core.passwords import InvalidToken, create_token, hash_password, read_token, verify_password
from app.core.rate_limit import RateLimiter
from app.main import app

client = TestClient(app)

STRONG_PASSWORD = "correct-horse-battery-staple"


def _register(email: str, password: str = STRONG_PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def test_password_hashing_round_trips_and_salts_each_hash() -> None:
    first = hash_password(STRONG_PASSWORD)
    second = hash_password(STRONG_PASSWORD)
    assert first != second  # a per-user salt means identical passwords hash differently
    assert STRONG_PASSWORD not in first
    assert verify_password(STRONG_PASSWORD, first)
    assert not verify_password("wrong-password-entirely", first)


def test_verifying_against_a_corrupt_hash_fails_rather_than_raising() -> None:
    assert verify_password(STRONG_PASSWORD, "not-a-real-hash") is False
    assert verify_password(STRONG_PASSWORD, "bcrypt$1$abc$def") is False


def test_tokens_round_trip_and_reject_tampering() -> None:
    token = create_token("user:7", secret="secret-one", expires_in=60)
    assert read_token(token, secret="secret-one") == "user:7"

    with pytest.raises(InvalidToken, match="signature"):
        read_token(token, secret="a-different-secret")

    # A payload swapped in behind a signature the attacker cannot recompute must not verify.
    _, signature = token.split(".")
    forged_body = create_token("user:9", secret="attacker-secret", expires_in=60).split(".")[0]
    forged = f"{forged_body}.{signature}"
    with pytest.raises(InvalidToken):
        read_token(forged, secret="secret-one")

    with pytest.raises(InvalidToken, match="Malformed"):
        read_token("garbage", secret="secret-one")


def test_an_expired_token_is_rejected() -> None:
    token = create_token("user:7", secret="secret-one", expires_in=-1)
    with pytest.raises(InvalidToken, match="expired"):
        read_token(token, secret="secret-one")


def test_registration_returns_a_usable_token() -> None:
    response = _register("alice@example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user_id"].startswith("user:")

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_registration_rejects_a_duplicate_email() -> None:
    _register("dupe@example.com")
    assert _register("dupe@example.com").status_code == 409


def test_registration_rejects_a_weak_password_and_a_bad_email() -> None:
    assert client.post("/api/auth/register", json={"email": "b@c.io", "password": "short"}).status_code == 422
    assert client.post(
        "/api/auth/register", json={"email": "not-an-email", "password": STRONG_PASSWORD}
    ).status_code == 422


def test_login_succeeds_and_wrong_credentials_are_indistinguishable() -> None:
    _register("bob@example.com")
    assert client.post(
        "/api/auth/login", json={"email": "bob@example.com", "password": STRONG_PASSWORD}
    ).status_code == 200

    wrong_password = client.post(
        "/api/auth/login", json={"email": "bob@example.com", "password": "definitely-wrong-here"}
    )
    unknown_account = client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": STRONG_PASSWORD}
    )
    assert wrong_password.status_code == unknown_account.status_code == 401
    # Identical replies, so the endpoint cannot be used to enumerate registered addresses.
    assert wrong_password.json() == unknown_account.json()


def test_me_requires_a_token() -> None:
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/api/auth/me", headers={"X-User-Id": "local-user"}).status_code == 401


def test_a_bearer_token_scopes_collections_to_its_own_account() -> None:
    alice = _register("scoped-alice@example.com").json()["access_token"]
    bob = _register("scoped-bob@example.com").json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice}"}
    bob_headers = {"Authorization": f"Bearer {bob}"}

    collection_id = client.post(
        "/api/collections", json={"name": "Alice private"}, headers=alice_headers
    ).json()["id"]

    assert client.get(f"/api/collections/{collection_id}", headers=alice_headers).status_code == 200
    assert client.get(f"/api/collections/{collection_id}", headers=bob_headers).status_code == 404
    # The development header must not reach an authenticated account's data either.
    assert client.get(f"/api/collections/{collection_id}", headers={"X-User-Id": "local-user"}).status_code == 404


def test_the_development_header_still_works_while_auth_is_optional() -> None:
    response = client.post("/api/collections", json={"name": "Dev header"}, headers={"X-User-Id": "dev-person"})
    assert response.status_code == 201
    assert client.get(
        f"/api/collections/{response.json()['id']}", headers={"X-User-Id": "dev-person"}
    ).status_code == 200


def test_auth_required_mode_ignores_the_development_header(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SECRET_KEY", "a-fixed-test-secret")
    get_settings.cache_clear()
    try:
        assert client.get("/api/collections", headers={"X-User-Id": "local-user"}).status_code == 401
        assert client.get("/api/collections").status_code == 401

        token = client.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": STRONG_PASSWORD}
        ).json()["access_token"]
        assert client.get("/api/collections", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    finally:
        get_settings.cache_clear()


def test_auth_required_without_a_secret_key_fails_loudly(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            get_settings().resolved_secret_key
    finally:
        get_settings.cache_clear()


def test_rate_limiter_allows_up_to_the_limit_then_refuses() -> None:
    limiter = RateLimiter(limit_per_minute=3)
    now = time.monotonic()
    assert [limiter.check("caller", now=now)[0] for _ in range(3)] == [True, True, True]

    allowed, remaining, retry_after = limiter.check("caller", now=now)
    assert allowed is False
    assert remaining == 0
    assert retry_after >= 1

    # A different caller has its own window.
    assert limiter.check("someone-else", now=now)[0] is True
    # And the original caller recovers once the window rolls over.
    assert limiter.check("caller", now=now + 61)[0] is True


def test_rate_limiter_is_a_no_op_when_disabled() -> None:
    limiter = RateLimiter(limit_per_minute=0)
    assert limiter.enabled is False
    assert all(limiter.check("caller")[0] for _ in range(100))
