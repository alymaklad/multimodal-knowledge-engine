from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_saved_search_lifecycle_and_user_export() -> None:
    collection = client.post("/api/collections", json={"name": "Saved search"}).json()
    saved = client.post("/api/saved-searches", json={"collection_id": collection["id"], "name": "Find RAG", "query": "RAG"})
    assert saved.status_code == 201
    listed = client.get("/api/saved-searches")
    assert any(item["id"] == saved.json()["id"] for item in listed.json())
    assert client.post(f"/api/saved-searches/{saved.json()['id']}/run").status_code == 200
    assert client.delete(f"/api/saved-searches/{saved.json()['id']}").status_code == 204
    backup = client.get("/api/system/export")
    assert backup.status_code == 200
    assert "collections" in backup.json()
