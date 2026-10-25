from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_evaluation_cases_are_scored_and_summarized() -> None:
    created = client.post(
        "/api/evaluations/cases",
        json={"category": "workout", "ground_truth": {"exercise": "Squat", "sets": 3}},
    )
    assert created.status_code == 201
    scored = client.post(f"/api/evaluations/cases/{created.json()['id']}/score", json={"prediction": {"exercise": "Squat", "sets": 4}})
    assert scored.status_code == 200
    assert scored.json()["scores"]["exact_field_accuracy"] == 0.5
    summary = client.get("/api/evaluations/summary")
    assert summary.status_code == 200
    assert summary.json()["scored_cases"] >= 1
