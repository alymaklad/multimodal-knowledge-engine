from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evaluation import EvaluationCase, EvaluationStatus
from app.schemas.evaluation import EvaluationSummary


def score_case(case: EvaluationCase, prediction: dict[str, object]) -> EvaluationCase:
    expected_keys = set(case.ground_truth)
    matches = sum(prediction.get(key) == case.ground_truth[key] for key in expected_keys)
    case.prediction = prediction
    case.scores = {"matched_fields": matches, "total_fields": len(expected_keys), "exact_field_accuracy": matches / len(expected_keys) if expected_keys else 1.0}
    case.status = EvaluationStatus.scored
    return case


def summarize_cases(db: Session, user_id: str) -> EvaluationSummary:
    cases = list(db.scalars(select(EvaluationCase).where(EvaluationCase.user_id == user_id)))
    scored = [case for case in cases if case.status == EvaluationStatus.scored and case.scores]
    category_scores: dict[str, list[float]] = defaultdict(list)
    for case in scored:
        category_scores[case.category].append(float(case.scores["exact_field_accuracy"]))
    return EvaluationSummary(
        total_cases=len(cases),
        scored_cases=len(scored),
        exact_field_accuracy=sum(float(case.scores["exact_field_accuracy"]) for case in scored) / len(scored) if scored else None,
        by_category={category: sum(values) / len(values) for category, values in category_scores.items()},
    )
