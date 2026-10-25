import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models.source import SourceKind
from app.services.links import InvalidLinkError, is_public_address, normalize_link

client = TestClient(app)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL123",
        "https://youtu.be/dQw4w9WgXcQ?si=share-token",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_every_youtube_form_normalises_to_one_canonical_link(url: str) -> None:
    link = normalize_link(url)
    assert link.kind is SourceKind.youtube
    assert link.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert link.details == {"video_id": "dQw4w9WgXcQ"}


@pytest.mark.parametrize(
    "url",
    ["https://www.youtube.com/@channel", "https://www.youtube.com/playlist?list=PL1", "https://youtu.be/short"],
)
def test_youtube_links_that_are_not_one_video_are_refused(url: str) -> None:
    with pytest.raises(InvalidLinkError, match="single YouTube video"):
        normalize_link(url)


def test_web_links_lose_only_what_cannot_change_the_document() -> None:
    link = normalize_link("HTTPS://Example.COM:443/Guide/?page=2#section-3")
    assert link.kind is SourceKind.web
    assert link.url == "https://example.com/Guide?page=2"
    assert normalize_link("http://example.com:8080/a/").url == "http://example.com:8080/a"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.com/file", "Only http and https"),
        ("https://user:secret@example.com/", "embedded credentials"),
        ("http://localhost:8000/admin", "private or local"),
        ("http://127.0.0.1/", "private or local"),
        ("http://10.1.2.3/", "private or local"),
        ("http://192.168.0.10/router", "private or local"),
        ("http://169.254.169.254/latest/meta-data", "private or local"),
        ("http://[::1]/", "private or local"),
        ("http://printer.local/", "private or local"),
    ],
)
def test_unsafe_links_are_refused(url: str, message: str) -> None:
    with pytest.raises(InvalidLinkError, match=message):
        normalize_link(url)


def test_private_hosts_can_be_allowed_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", "true")
    get_settings.cache_clear()
    try:
        assert normalize_link("http://localhost:8000/notes").url == "http://localhost:8000/notes"
    finally:
        get_settings.cache_clear()


def test_address_policy() -> None:
    assert is_public_address("93.184.216.34")
    assert not is_public_address("10.0.0.1")
    assert not is_public_address("::ffff:127.0.0.1")
    assert not is_public_address("fe80::1%eth0")
    assert not is_public_address("100.64.0.1")


def test_the_api_treats_two_forms_of_one_video_as_a_duplicate() -> None:
    owner = {"X-User-Id": "youtube-dedupe"}
    first = client.post("/api/sources/url", json={"url": "https://youtu.be/aBcDeFgHiJk"}, headers=owner)
    assert first.status_code == 202
    second = client.post("/api/sources/url", json={"url": "https://www.youtube.com/watch?v=aBcDeFgHiJk&t=1"}, headers=owner)
    assert second.status_code == 409
    source = client.get(f"/api/sources/{first.json()['source_id']}", headers=owner).json()
    assert source["kind"] == "youtube"
    assert source["details"]["video_id"] == "aBcDeFgHiJk"


def test_the_api_reports_why_a_link_was_refused() -> None:
    response = client.post("/api/sources/url", json={"url": "http://127.0.0.1/secret"})
    assert response.status_code == 422
    assert "private" in response.json()["detail"]
