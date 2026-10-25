"""Model benchmarking and ablation comparison (architecture sections 54 and 55).

Two questions this answers that the single-prediction evaluation could not:

1. Which configuration extracts most accurately, and at what latency and cost (section 54)?
2. How much does one evidence channel actually contribute (section 55)? Running the same cases with
   `uses_ocr` on and off and diffing the two runs is what turns "OCR seems useful" into a number.

Comparisons are computed over the **intersection** of cases both runs scored. Comparing a run that
covered 50 cases against one that covered 5 would otherwise report a difference in coverage as a
difference in quality.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.benchmark import BenchmarkPrediction, BenchmarkRun
from app.models.evaluation import EvaluationCase
from app.schemas.benchmark import (
    AblationDelta,
    BenchmarkComparison,
    BenchmarkRunSummary,
    CategoryAccuracy,
)


def score_prediction(case: EvaluationCase, prediction: dict[str, object]) -> dict[str, object]:
    """Exact per-field agreement with the annotated ground truth."""
    expected = set(case.ground_truth)
    matched = sum(prediction.get(key) == case.ground_truth[key] for key in expected)
    return {
        "matched_fields": matched,
        "total_fields": len(expected),
        "exact_field_accuracy": matched / len(expected) if expected else 1.0,
    }


def summarize_run(db: Session, run: BenchmarkRun, *, category: str | None = None) -> BenchmarkRunSummary:
    rows = _scored_rows(db, run.id, category=category)
    accuracies = [float(prediction.scores["exact_field_accuracy"]) for prediction, _ in rows]
    latencies = [prediction.latency_ms for prediction, _ in rows if prediction.latency_ms is not None]
    costs = [prediction.cost_usd for prediction, _ in rows if prediction.cost_usd is not None]

    by_category: dict[str, list[float]] = defaultdict(list)
    for prediction, case in rows:
        by_category[case.category].append(float(prediction.scores["exact_field_accuracy"]))

    return BenchmarkRunSummary(
        run_id=run.id,
        label=run.label,
        configuration=run.configuration,
        scored_cases=len(rows),
        exact_field_accuracy=round(mean(accuracies), 4) if accuracies else None,
        average_latency_ms=round(mean(latencies), 3) if latencies else None,
        total_cost_usd=round(sum(costs), 6) if costs else None,
        by_category=[
            CategoryAccuracy(category=name, cases=len(values), exact_field_accuracy=round(mean(values), 4))
            for name, values in sorted(by_category.items())
        ],
    )


def compare_runs(
    db: Session, runs: list[BenchmarkRun], *, category: str | None = None
) -> BenchmarkComparison:
    """The section 54 table, plus the section 55 delta when exactly two runs are compared."""
    summaries = [summarize_run(db, run, category=category) for run in runs]

    scored_by_run = {
        run.id: {case.id: float(prediction.scores["exact_field_accuracy"])
                 for prediction, case in _scored_rows(db, run.id, category=category)}
        for run in runs
    }
    shared_cases = set.intersection(*(set(scores) for scores in scored_by_run.values())) if runs else set()

    delta = None
    if len(runs) == 2 and shared_cases:
        baseline, candidate = runs[0], runs[1]
        baseline_mean = mean(scored_by_run[baseline.id][case_id] for case_id in shared_cases)
        candidate_mean = mean(scored_by_run[candidate.id][case_id] for case_id in shared_cases)
        improved = sum(
            scored_by_run[candidate.id][case_id] > scored_by_run[baseline.id][case_id]
            for case_id in shared_cases
        )
        regressed = sum(
            scored_by_run[candidate.id][case_id] < scored_by_run[baseline.id][case_id]
            for case_id in shared_cases
        )
        delta = AblationDelta(
            baseline_run_id=baseline.id,
            baseline_label=baseline.label,
            candidate_run_id=candidate.id,
            candidate_label=candidate.label,
            shared_cases=len(shared_cases),
            baseline_accuracy=round(baseline_mean, 4),
            candidate_accuracy=round(candidate_mean, 4),
            accuracy_delta=round(candidate_mean - baseline_mean, 4),
            improved_cases=improved,
            regressed_cases=regressed,
            unchanged_cases=len(shared_cases) - improved - regressed,
            changed_settings=_changed_settings(baseline.configuration, candidate.configuration),
        )

    return BenchmarkComparison(
        category=category,
        shared_cases=len(shared_cases),
        runs=sorted(
            summaries, key=lambda summary: (-(summary.exact_field_accuracy or -1.0), summary.label)
        ),
        delta=delta,
    )


def _scored_rows(
    db: Session, run_id: int, *, category: str | None
) -> list[tuple[BenchmarkPrediction, EvaluationCase]]:
    statement = (
        select(BenchmarkPrediction, EvaluationCase)
        .join(EvaluationCase, EvaluationCase.id == BenchmarkPrediction.case_id)
        .where(BenchmarkPrediction.run_id == run_id)
    )
    if category is not None:
        statement = statement.where(EvaluationCase.category == category)
    return [
        (prediction, case)
        for prediction, case in db.execute(statement).all()
        if prediction.scores and "exact_field_accuracy" in prediction.scores
    ]


def _changed_settings(baseline: dict, candidate: dict) -> dict[str, list]:
    """Name what actually differs, so a delta is attributable to a specific change."""
    return {
        key: [baseline.get(key), candidate.get(key)]
        for key in sorted(set(baseline) | set(candidate))
        if baseline.get(key) != candidate.get(key)
    }
