from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_the_single_page_client_is_served() -> None:
    response = client.get("/app")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Multimodal Knowledge Engine" in body
    assert "/app/static/app.js" in body
    assert "/app/static/style.css" in body


def test_static_assets_are_served() -> None:
    script = client.get("/app/static/app.js")
    assert script.status_code == 200
    # The client must talk to the documented API, not a private rendering path.
    assert "/api/search" in script.text
    assert client.get("/app/static/style.css").status_code == 200


def test_collection_listing_carries_source_counts_without_a_request_per_collection() -> None:
    owner = {"X-User-Id": "counting-owner"}
    empty = client.post("/api/collections", json={"name": "Empty one"}, headers=owner).json()["id"]
    filled = client.post("/api/collections", json={"name": "Filled one"}, headers=owner).json()["id"]
    for suffix in ("CNT1", "CNT2"):
        client.post(
            "/api/sources/url",
            json={"collection_id": filled, "url": f"https://example.com/source/{suffix}/"},
            headers=owner,
        )

    listing = {item["id"]: item for item in client.get("/api/collections", headers=owner).json()}
    assert listing[empty]["source_count"] == 0
    assert listing[filled]["source_count"] == 2
