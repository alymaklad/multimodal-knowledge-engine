from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _add_url(url: str, collection_id: int | None = None, headers: dict | None = None) -> int:
    payload = {"url": url} if collection_id is None else {"url": url, "collection_id": collection_id}
    response = client.post("/api/sources/url", json=payload, headers=headers or {})
    assert response.status_code == 202, response.text
    return response.json()["source_id"]


def _member_ids(collection_id: int, headers: dict | None = None) -> set[int]:
    listing = client.get("/api/sources", params={"collection_id": collection_id}, headers=headers or {})
    assert listing.status_code == 200
    return {item["id"] for item in listing.json()}


def test_collection_and_source_ingestion_flow(monkeypatch) -> None:
    collection = client.post("/api/collections", json={"name": "Reading list"})
    assert collection.status_code == 201
    collection_id = collection.json()["id"]

    created = client.post(
        "/api/sources/url",
        json={"collection_id": collection_id, "url": "https://example.com/articles/flow"},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "pending"

    job = client.get(f"/api/jobs/source/{created.json()['source_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "queued"
    events = client.get(f"/api/jobs/{job.json()['id']}/events")
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "queued"

    monkeypatch.setattr("app.api.jobs.dispatch_media_preparation", lambda _job_id: "task-123")
    dispatch = client.post(f"/api/jobs/{job.json()['id']}/dispatch")
    assert dispatch.status_code == 422

    source = client.get(f"/api/sources/{created.json()['source_id']}")
    assert source.status_code == 200
    assert source.json()["kind"] == "web"
    assert source.json()["title"] == "https://example.com/articles/flow"

    assert _member_ids(collection_id) == {created.json()["source_id"]}
    assert client.get(f"/api/collections/{collection_id}").json()["source_count"] == 1


def test_url_kind_is_detected_from_the_host() -> None:
    youtube = _add_url("https://www.youtube.com/watch?v=kind-detection")
    article = _add_url("https://example.com/kind-detection")
    assert client.get(f"/api/sources/{youtube}").json()["kind"] == "youtube"
    assert client.get(f"/api/sources/{article}").json()["kind"] == "web"


def test_duplicate_links_are_rejected_across_the_whole_library() -> None:
    first = client.post("/api/collections", json={"name": "First"}).json()["id"]
    second = client.post("/api/collections", json={"name": "Second"}).json()["id"]
    source_id = _add_url("https://example.com/unique-link", first)

    again = client.post("/api/sources/url", json={"collection_id": second, "url": "https://example.com/unique-link"})
    assert again.status_code == 409
    # The conflict names the existing source, so a client can add it to the second collection instead.
    assert again.json()["detail"]["source_id"] == source_id
    invalid = client.post("/api/sources/url", json={"url": "not a url"})
    assert invalid.status_code == 422


def test_one_source_can_belong_to_several_collections() -> None:
    first = client.post("/api/collections", json={"name": "Shared A"}).json()["id"]
    second = client.post("/api/collections", json={"name": "Shared B"}).json()["id"]
    source_id = _add_url("https://example.com/shared-source")

    assert _member_ids(first) == set()
    added = client.post(f"/api/collections/{first}/sources", json={"source_ids": [source_id]})
    assert added.status_code == 200
    assert added.json()["added"] == [source_id]
    # Adding again is a no-op rather than an error.
    assert client.post(f"/api/collections/{first}/sources", json={"source_ids": [source_id]}).json()["added"] == []
    client.post(f"/api/collections/{second}/sources", json={"source_ids": [source_id]})

    assert source_id in _member_ids(first) and source_id in _member_ids(second)

    assert client.delete(f"/api/collections/{first}/sources/{source_id}").status_code == 204
    assert client.delete(f"/api/collections/{first}/sources/{source_id}").status_code == 404
    assert _member_ids(first) == set()
    assert source_id in _member_ids(second)
    assert client.get(f"/api/sources/{source_id}").status_code == 200


def test_deleting_a_collection_keeps_its_sources_in_the_library() -> None:
    collection_id = client.post("/api/collections", json={"name": "Disposable"}).json()["id"]
    source_id = _add_url("https://example.com/survives-collection-delete", collection_id)
    assert client.delete(f"/api/collections/{collection_id}").status_code == 204
    assert client.get(f"/api/sources/{source_id}").status_code == 200


def test_deleting_a_source_removes_it_from_every_collection() -> None:
    first = client.post("/api/collections", json={"name": "Holds it A"}).json()["id"]
    second = client.post("/api/collections", json={"name": "Holds it B"}).json()["id"]
    source_id = _add_url("https://example.com/deleted-everywhere", first)
    client.post(f"/api/collections/{second}/sources", json={"source_ids": [source_id]})

    assert client.delete(f"/api/sources/{source_id}").status_code == 204
    assert _member_ids(first) == set() and _member_ids(second) == set()


def test_another_users_source_cannot_be_added_to_my_collection() -> None:
    mine = {"X-User-Id": "collector-a"}
    theirs = {"X-User-Id": "collector-b"}
    collection_id = client.post("/api/collections", json={"name": "Mine"}, headers=mine).json()["id"]
    foreign = _add_url("https://example.com/foreign-source", headers=theirs)

    response = client.post(f"/api/collections/{collection_id}/sources", json={"source_ids": [foreign]}, headers=mine)
    assert response.status_code == 404
    assert response.json()["detail"]["source_ids"] == [foreign]
    assert client.get(f"/api/sources/{foreign}", headers=mine).status_code == 404
    assert foreign not in {item["id"] for item in client.get("/api/sources", headers=mine).json()}


def test_cloning_a_collection_shares_its_sources() -> None:
    collection_id = client.post("/api/collections", json={"name": "Original"}).json()["id"]
    source_id = _add_url("https://example.com/cloned-membership", collection_id)
    clone = client.post(f"/api/collections/{collection_id}/clone", params={"name": "Copy"})
    assert clone.status_code == 201
    assert clone.json()["source_count"] == 1
    assert _member_ids(clone.json()["id"]) == {source_id}


def test_resources_are_isolated_by_development_user_identity() -> None:
    owner_headers = {"X-User-Id": "owner-a"}
    collection = client.post("/api/collections", json={"name": "Private"}, headers=owner_headers).json()
    denied = client.get(f"/api/collections/{collection['id']}", headers={"X-User-Id": "owner-b"})
    assert denied.status_code == 404
    visible = client.get(f"/api/collections/{collection['id']}", headers=owner_headers)
    assert visible.status_code == 200
    metrics = client.get("/api/system/metrics", headers=owner_headers)
    assert metrics.status_code == 200
    assert metrics.json()["collections"] >= 1


def test_operational_and_collection_lifecycle_endpoints() -> None:
    collection = client.post("/api/collections", json={"name": "Before"}).json()
    updated = client.patch(f"/api/collections/{collection['id']}", json={"name": "After", "description": "Updated"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "After"
    assert client.get("/ready").status_code == 200
    capabilities = client.get("/api/capabilities")
    assert capabilities.status_code == 200
    assert "local_video_upload" in capabilities.json()["ingestion"]
    traced = client.get("/health", headers={"X-Request-Id": "test-request"})
    assert traced.headers["X-Request-Id"] == "test-request"
    assert client.delete(f"/api/collections/{collection['id']}").status_code == 204


def test_metadata_export_and_user_job_listing() -> None:
    collection = client.post("/api/collections", json={"name": "Exportable"}).json()
    source_id = _add_url("https://example.com/exportable", collection["id"])
    updated = client.patch(
        f"/api/sources/{source_id}", json={"title": "Exportable article", "author": "@writer", "description": "A summary"}
    )
    assert updated.status_code == 200
    assert updated.json()["author"] == "@writer"
    # Fields the client did not send are left alone.
    assert client.patch(f"/api/sources/{source_id}", json={"author": "Writer"}).json()["title"] == "Exportable article"
    exported = client.get(f"/api/collections/{collection['id']}/export")
    assert exported.status_code == 200
    assert exported.json()["sources"][0]["description"] == "A summary"
    assert exported.json()["sources"][0]["title"] == "Exportable article"
    jobs = client.get("/api/jobs?status_filter=queued")
    assert jobs.status_code == 200
    assert any(job["source_id"] == source_id for job in jobs.json())


def test_uploaded_video_creates_a_queued_source() -> None:
    collection = client.post("/api/collections", json={"name": "Uploaded media"}).json()
    response = client.post(
        "/api/sources/upload",
        data={"collection_id": str(collection["id"])},
        files={"media": ("demo.mp4", b"not-a-real-video-yet", "video/mp4")},
    )
    assert response.status_code == 202
    source = client.get(f"/api/sources/{response.json()['source_id']}").json()
    assert source["kind"] == "video"
    assert source["title"] == "demo"
    assert source["original_filename"] == "demo.mp4"
    assert source["origin_url"] is None
    assert source["status"] == "pending"
    assert _member_ids(collection["id"]) == {source["id"]}

    duplicate = client.post(
        "/api/sources/upload",
        files={"media": ("renamed.mp4", b"not-a-real-video-yet", "video/mp4")},
    )
    assert duplicate.status_code == 409

    reprocessed = client.post(f"/api/sources/{source['id']}/reprocess")
    assert reprocessed.status_code == 202
    job = client.get(f"/api/jobs/source/{source['id']}").json()
    assert job["current_stage"] == "ingestion"


def test_upload_without_a_collection_lands_in_the_library_only() -> None:
    response = client.post(
        "/api/sources/upload", files={"media": ("library-only.mp4", b"library-only-bytes", "video/mp4")}
    )
    assert response.status_code == 202
    library = {item["id"] for item in client.get("/api/sources", params={"kind": "video"}).json()}
    assert response.json()["source_id"] in library
