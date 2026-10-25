from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_export_csv_and_evaluation_case_lifecycle() -> None:
    collection = client.post("/api/collections", json={"name": "Portable"}).json()
    csv_export = client.get(f"/api/collections/{collection['id']}/export.csv")
    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")
    case = client.post("/api/evaluations/cases", json={"category": "ai", "ground_truth": {"topic": "RAG"}}).json()
    filtered = client.get("/api/evaluations/cases?category=ai")
    assert any(item["id"] == case["id"] for item in filtered.json())
    assert client.delete(f"/api/evaluations/cases/{case['id']}").status_code == 204
