"""Architecture sections 54 (model benchmarking) and 55 (silent-video ablation)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

OWNER = {"X-User-Id": "benchmark-owner"}

# The section 55 experiment: silent workout Sources whose sets and reps exist only as on-screen text.
SILENT_CASES = [
    {"exercise": "Bulgarian Split Squat", "sets": 3, "reps": "10 each leg"},
    {"exercise": "Barbell Back Squat", "sets": 4, "reps": "8-10"},
    {"exercise": "Romanian Deadlift", "sets": 3, "reps": "12"},
]


def _case(ground_truth: dict, category: str = "silent_workout") -> int:
    response = client.post(
        "/api/evaluations/cases",
        json={"category": category, "ground_truth": ground_truth},
        headers=OWNER,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _run(label: str, configuration: dict) -> int:
    response = client.post(
        "/api/evaluations/runs",
        json={"label": label, "configuration": configuration},
        headers=OWNER,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _submit(run_id: int, predictions: list[tuple[int, dict]], latency: float = 100.0) -> None:
    payload = [
        {"case_id": case_id, "prediction": prediction, "latency_ms": latency, "cost_usd": 0.01}
        for case_id, prediction in predictions
    ]
    response = client.post(f"/api/evaluations/runs/{run_id}/predictions", json=payload, headers=OWNER)
    assert response.status_code == 200, response.text


def test_a_run_records_accuracy_latency_and_cost() -> None:
    case_id = _case({"exercise": "Barbell Back Squat", "sets": 4})
    run_id = _run("opus-with-ocr", {"vision_model": "claude-opus-5", "uses_ocr": True})
    _submit(run_id, [(case_id, {"exercise": "Barbell Back Squat", "sets": 3})], latency=250.0)

    summary = client.get(f"/api/evaluations/runs/{run_id}", headers=OWNER).json()
    assert summary["scored_cases"] == 1
    assert summary["exact_field_accuracy"] == 0.5  # exercise matched, sets did not
    assert summary["average_latency_ms"] == 250.0
    assert summary["total_cost_usd"] == 0.01
    assert summary["by_category"][0]["category"] == "silent_workout"


def test_resubmitting_a_case_replaces_rather_than_duplicates_it() -> None:
    case_id = _case({"exercise": "Front Squat", "sets": 5})
    run_id = _run("resubmit-run", {"vision_model": "small"})
    _submit(run_id, [(case_id, {"exercise": "Wrong", "sets": 1})])
    _submit(run_id, [(case_id, {"exercise": "Front Squat", "sets": 5})])

    summary = client.get(f"/api/evaluations/runs/{run_id}", headers=OWNER).json()
    assert summary["scored_cases"] == 1
    assert summary["exact_field_accuracy"] == 1.0


def test_duplicate_run_labels_are_rejected() -> None:
    _run("unique-label", {})
    response = client.post(
        "/api/evaluations/runs", json={"label": "unique-label", "configuration": {}}, headers=OWNER
    )
    assert response.status_code == 409


def test_benchmarking_ranks_models_by_accuracy() -> None:
    """The section 54 table: several configurations, ordered by how well they extract."""
    cases = [_case(truth, category="benchmark_workout") for truth in SILENT_CASES]

    strong = _run("strong-vlm", {"vision_model": "claude-opus-5"})
    _submit(strong, list(zip(cases, SILENT_CASES)), latency=900.0)  # all three exactly right

    weak = _run("weak-vlm", {"vision_model": "tiny-model"})
    _submit(
        weak,
        [
            (cases[0], SILENT_CASES[0]),
            (cases[1], {"exercise": "Wrong", "sets": 1, "reps": "5"}),
            (cases[2], {"exercise": "Wrong", "sets": 1, "reps": "5"}),
        ],
        latency=120.0,
    )

    comparison = client.get(
        "/api/evaluations/runs/compare",
        params={"run_ids": f"{strong},{weak}", "category": "benchmark_workout"},
        headers=OWNER,
    ).json()

    assert [run["label"] for run in comparison["runs"]] == ["strong-vlm", "weak-vlm"]
    assert comparison["runs"][0]["exact_field_accuracy"] == 1.0
    assert comparison["runs"][1]["exact_field_accuracy"] < 1.0
    # Latency is reported alongside accuracy, so the cheaper model's tradeoff is visible.
    assert comparison["runs"][0]["average_latency_ms"] == 900.0
    assert comparison["runs"][1]["average_latency_ms"] == 120.0


def test_the_ablation_answers_how_much_ocr_contributes() -> None:
    """Section 55: same silent cases, OCR on versus off, over the shared subset."""
    cases = [_case(truth, category="ablation_silent") for truth in SILENT_CASES]

    without_ocr = _run("vlm-only", {"vision_model": "claude-opus-5", "uses_ocr": False})
    # Without on-screen text the movement is recognisable but sets and reps are not.
    _submit(
        without_ocr,
        [(case_id, {"exercise": truth["exercise"]}) for case_id, truth in zip(cases, SILENT_CASES)],
    )

    with_ocr = _run("vlm-plus-ocr", {"vision_model": "claude-opus-5", "uses_ocr": True})
    _submit(with_ocr, list(zip(cases, SILENT_CASES)))

    comparison = client.get(
        "/api/evaluations/runs/compare",
        params={"run_ids": f"{without_ocr},{with_ocr}", "category": "ablation_silent"},
        headers=OWNER,
    ).json()

    delta = comparison["delta"]
    assert delta["shared_cases"] == 3
    assert delta["baseline_label"] == "vlm-only"
    assert delta["candidate_label"] == "vlm-plus-ocr"
    assert delta["baseline_accuracy"] < delta["candidate_accuracy"]
    assert delta["accuracy_delta"] > 0
    assert delta["improved_cases"] == 3
    assert delta["regressed_cases"] == 0
    # The delta names the setting responsible for it.
    assert delta["changed_settings"] == {"uses_ocr": [False, True]}


def test_comparison_uses_only_cases_both_runs_scored() -> None:
    """Otherwise a difference in coverage would be reported as a difference in quality."""
    shared = _case({"exercise": "Lunge", "sets": 3}, category="coverage")
    extra = _case({"exercise": "Plank", "sets": 2}, category="coverage")

    broad = _run("broad-coverage", {"uses_ocr": True})
    _submit(broad, [(shared, {"exercise": "Lunge", "sets": 3}), (extra, {"exercise": "Plank", "sets": 2})])

    narrow = _run("narrow-coverage", {"uses_ocr": False})
    _submit(narrow, [(shared, {"exercise": "Lunge", "sets": 3})])

    comparison = client.get(
        "/api/evaluations/runs/compare",
        params={"run_ids": f"{broad},{narrow}", "category": "coverage"},
        headers=OWNER,
    ).json()

    assert comparison["shared_cases"] == 1
    # Both runs are perfect on the one case they share, so the delta must be zero even though
    # one run covered twice as many cases.
    assert comparison["delta"]["accuracy_delta"] == 0.0
    assert comparison["delta"]["unchanged_cases"] == 1


def test_a_regression_is_reported_as_a_regression() -> None:
    cases = [_case(truth, category="regression") for truth in SILENT_CASES[:2]]
    good = _run("good-run", {"version": "v1"})
    _submit(good, list(zip(cases, SILENT_CASES[:2])))
    bad = _run("bad-run", {"version": "v2"})
    _submit(bad, [(cases[0], SILENT_CASES[0]), (cases[1], {"exercise": "Wrong"})])

    delta = client.get(
        "/api/evaluations/runs/compare",
        params={"run_ids": f"{good},{bad}", "category": "regression"},
        headers=OWNER,
    ).json()["delta"]

    assert delta["accuracy_delta"] < 0
    assert delta["regressed_cases"] == 1
    assert delta["improved_cases"] == 0
    assert delta["changed_settings"] == {"version": ["v1", "v2"]}


def test_comparison_requires_at_least_two_runs() -> None:
    run_id = _run("lonely-run", {})
    response = client.get(
        "/api/evaluations/runs/compare", params={"run_ids": str(run_id)}, headers=OWNER
    )
    assert response.status_code == 422


def test_benchmark_runs_are_owner_scoped() -> None:
    run_id = _run("private-run", {})
    stranger = {"X-User-Id": "not-the-owner"}
    assert client.get(f"/api/evaluations/runs/{run_id}", headers=stranger).status_code == 404
    assert client.post(
        f"/api/evaluations/runs/{run_id}/predictions",
        json=[{"case_id": 1, "prediction": {}}],
        headers=stranger,
    ).status_code == 404
    assert client.delete(f"/api/evaluations/runs/{run_id}", headers=stranger).status_code == 404
    assert client.get("/api/evaluations/runs", headers=stranger).json() == []


def test_a_prediction_for_someone_elses_case_is_refused() -> None:
    foreign_case = client.post(
        "/api/evaluations/cases",
        json={"category": "general", "ground_truth": {"a": 1}},
        headers={"X-User-Id": "another-owner"},
    ).json()["id"]
    run_id = _run("cross-owner-run", {})
    response = client.post(
        f"/api/evaluations/runs/{run_id}/predictions",
        json=[{"case_id": foreign_case, "prediction": {"a": 1}}],
        headers=OWNER,
    )
    assert response.status_code == 404


def test_deleting_a_run_removes_its_predictions() -> None:
    case_id = _case({"exercise": "Step Up", "sets": 3})
    run_id = _run("temporary-run", {})
    _submit(run_id, [(case_id, {"exercise": "Step Up", "sets": 3})])
    assert client.delete(f"/api/evaluations/runs/{run_id}", headers=OWNER).status_code == 204
    assert client.get(f"/api/evaluations/runs/{run_id}", headers=OWNER).status_code == 404
