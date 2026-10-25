from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_collection_and_reel_ingestion_flow(monkeypatch) -> None:
    collection = client.post("/api/collections", json={"name": "Workout"})
    assert collection.status_code == 201
    collection_id = collection.json()["id"]

    created = client.post(
        "/api/reels",
        json={"collection_id": collection_id, "instagram_url": "https://www.instagram.com/reel/ABC123/"},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "pending"

    job = client.get(f"/api/jobs/reel/{created.json()['reel_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "queued"
    events = client.get(f"/api/jobs/{job.json()['id']}/events")
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "queued"

    monkeypatch.setattr("app.api.jobs.dispatch_media_preparation", lambda _job_id: "task-123")
    dispatch = client.post(f"/api/jobs/{job.json()['id']}/dispatch")
    assert dispatch.status_code == 422

    reel = client.get(f"/api/reels/{created.json()['reel_id']}")
    assert reel.status_code == 200
    assert reel.json()["collection_id"] == collection_id

    collection_reels = client.get(f"/api/reels/collection/{collection_id}")
    assert collection_reels.status_code == 200
    assert len(collection_reels.json()) == 1


def test_reel_ingestion_rejects_duplicate_and_invalid_url() -> None:
    collection = client.post("/api/collections", json={"name": "AI"}).json()
    payload = {"collection_id": collection["id"], "instagram_url": "https://instagram.com/reel/unique/"}
    assert client.post("/api/reels", json=payload).status_code == 202
    assert client.post("/api/reels", json=payload).status_code == 409
    invalid = client.post("/api/reels", json={"collection_id": collection["id"], "instagram_url": "https://example.com/video"})
    assert invalid.status_code == 422


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
    created = client.post(
        "/api/reels", json={"collection_id": collection["id"], "instagram_url": "https://instagram.com/reel/exportable/"}
    ).json()
    updated = client.patch(f"/api/reels/{created['reel_id']}", json={"creator_username": "@creator", "caption": "A caption"})
    assert updated.status_code == 200
    assert updated.json()["creator_username"] == "@creator"
    exported = client.get(f"/api/collections/{collection['id']}/export")
    assert exported.status_code == 200
    assert exported.json()["reels"][0]["caption"] == "A caption"
    jobs = client.get("/api/jobs?status_filter=queued")
    assert jobs.status_code == 200
    assert any(job["reel_id"] == created["reel_id"] for job in jobs.json())


def test_uploaded_video_creates_a_queued_reel() -> None:
    collection = client.post("/api/collections", json={"name": "Uploaded media"}).json()
    response = client.post(
        "/api/reels/upload",
        data={"collection_id": str(collection["id"])},
        files={"media": ("demo.mp4", b"not-a-real-video-yet", "video/mp4")},
    )
    assert response.status_code == 202
    reel = client.get(f"/api/reels/{response.json()['reel_id']}").json()
    assert reel["instagram_url"].startswith("upload://")
    assert reel["status"] == "pending"

    duplicate = client.post(
        "/api/reels/upload",
        data={"collection_id": str(collection["id"])},
        files={"media": ("renamed.mp4", b"not-a-real-video-yet", "video/mp4")},
    )
    assert duplicate.status_code == 409

    reprocessed = client.post(f"/api/reels/{reel['id']}/reprocess")
    assert reprocessed.status_code == 202
    job = client.get(f"/api/jobs/reel/{reel['id']}").json()
    assert job["current_stage"] == "ingestion"
