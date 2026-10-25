from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.benchmark import BenchmarkPrediction, BenchmarkRun
from app.models.evaluation import EvaluationCase
from app.schemas.benchmark import (
    BenchmarkComparison,
    BenchmarkRunCreate,
    BenchmarkRunRead,
    BenchmarkRunSummary,
    PredictionResult,
    PredictionSubmission,
)
from app.services.benchmarking import compare_runs, score_prediction, summarize_run

router = APIRouter(prefix="/api/evaluations/runs", tags=["benchmarks"])


@router.post("", response_model=BenchmarkRunRead, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: BenchmarkRunCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> BenchmarkRun:
    """Register a configuration to evaluate: a model set, an ablation, or both."""
    existing = db.scalar(
        select(BenchmarkRun).where(BenchmarkRun.user_id == user_id, BenchmarkRun.label == payload.label)
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A run with that label already exists")
    run = BenchmarkRun(user_id=user_id, **payload.model_dump())
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.get("", response_model=list[BenchmarkRunRead])
def list_runs(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[BenchmarkRun]:
    return list(
        db.scalars(
            select(BenchmarkRun).where(BenchmarkRun.user_id == user_id).order_by(BenchmarkRun.created_at.desc())
        )
    )


@router.post("/{run_id}/predictions", response_model=list[PredictionResult])
def submit_predictions(
    run_id: int,
    submissions: list[PredictionSubmission],
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[PredictionResult]:
    """Record and score this run's answers. Re-submitting a case replaces its previous answer."""
    run = _owned_run(db, run_id, user_id)
    results = []
    for submission in submissions:
        case = db.scalar(
            select(EvaluationCase).where(
                EvaluationCase.id == submission.case_id, EvaluationCase.user_id == user_id
            )
        )
        if case is None:
            raise HTTPException(status_code=404, detail=f"Evaluation case {submission.case_id} not found")
        scores = score_prediction(case, submission.prediction)
        existing = db.scalar(
            select(BenchmarkPrediction).where(
                BenchmarkPrediction.run_id == run.id, BenchmarkPrediction.case_id == case.id
            )
        )
        if existing is None:
            existing = BenchmarkPrediction(run_id=run.id, case_id=case.id)
            db.add(existing)
        existing.prediction = submission.prediction
        existing.scores = scores
        existing.latency_ms = submission.latency_ms
        existing.cost_usd = submission.cost_usd
        results.append(PredictionResult(case_id=case.id, scores=scores))
    db.commit()
    return results


@router.get("/compare", response_model=BenchmarkComparison)
def compare(
    run_ids: str = Query(description="Comma-separated run ids, e.g. 1,2"),
    category: str | None = Query(default=None, description="Restrict to one case category"),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> BenchmarkComparison:
    """Rank configurations, and quantify the difference when exactly two are compared."""
    try:
        identifiers = [int(value) for value in run_ids.split(",") if value.strip()]
    except ValueError as error:
        raise HTTPException(status_code=422, detail="run_ids must be comma-separated integers") from error
    if len(identifiers) < 2:
        raise HTTPException(status_code=422, detail="Supply at least two run ids to compare")
    return compare_runs(db, [_owned_run(db, identifier, user_id) for identifier in identifiers], category=category)


@router.get("/{run_id}", response_model=BenchmarkRunSummary)
def run_summary(
    run_id: int,
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> BenchmarkRunSummary:
    return summarize_run(db, _owned_run(db, run_id, user_id), category=category)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> None:
    db.delete(_owned_run(db, run_id, user_id))
    db.commit()


def _owned_run(db: Session, run_id: int, user_id: str) -> BenchmarkRun:
    run = db.scalar(select(BenchmarkRun).where(BenchmarkRun.id == run_id, BenchmarkRun.user_id == user_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return run
