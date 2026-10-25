"""Password hashing and signed bearer tokens, using only the standard library.

PBKDF2-HMAC-SHA256 with a per-user random salt is used rather than pulling in bcrypt or argon2, so
the project stays installable without a compiler. The iteration count is stored inside the hash
string, which is what lets it be raised later without invalidating existing passwords.

Tokens are HMAC-signed payloads with an explicit expiry rather than JWTs: the same guarantees for
this use, no dependency, and no algorithm-confusion footgun.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

PBKDF2_ITERATIONS = 480_000
_ALGORITHM = "pbkdf2_sha256"


class InvalidToken(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{_ALGORITHM}${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    # Constant time, so a wrong password cannot be narrowed down by response timing.
    return hmac.compare_digest(digest, _unb64(expected))


def create_token(subject: str, *, secret: str, expires_in: int) -> str:
    payload = {"sub": subject, "exp": int(time.time()) + expires_in, "jti": secrets.token_hex(8)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_b64(_sign(body, secret))}"


def read_token(token: str, *, secret: str) -> str:
    """Return the subject of a valid, unexpired token, or raise `InvalidToken`."""
    try:
        body, signature = token.split(".")
    except ValueError as error:
        raise InvalidToken("Malformed token") from error
    if not hmac.compare_digest(_unb64(signature), _sign(body, secret)):
        raise InvalidToken("Bad token signature")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError) as error:
        raise InvalidToken("Unreadable token payload") from error
    if payload.get("exp", 0) < time.time():
        raise InvalidToken("Token has expired")
    subject = payload.get("sub")
    if not subject:
        raise InvalidToken("Token has no subject")
    return str(subject)


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def _sign(body: str, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
