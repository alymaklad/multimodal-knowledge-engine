from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.evaluation import EvaluationCase
from app.schemas.evaluation import EvaluationCaseCreate, EvaluationCaseRead, EvaluationPrediction, EvaluationSummary
from app.services.authorization import get_owned_collection, get_owned_reel
from app.services.evaluation import score_case, summarize_cases

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


@router.post("/cases", response_model=EvaluationCaseRead, status_code=status.HTTP_201_CREATED)
def create_case(payload: EvaluationCaseCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> EvaluationCase:
    if payload.collection_id and get_owned_collection(db, payload.collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    if payload.reel_id and get_owned_reel(db, payload.reel_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    case = EvaluationCase(user_id=user_id, **payload.model_dump())
    db.add(case); db.commit(); db.refresh(case)
    return case


@router.get("/cases", response_model=list[EvaluationCaseRead])
def list_cases(category: str | None = None, offset: int = 0, limit: int = 50, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[EvaluationCase]:
    query = select(EvaluationCase).where(EvaluationCase.user_id == user_id)
    if category:
        query = query.where(EvaluationCase.category == category)
    return list(db.scalars(query.order_by(EvaluationCase.created_at.desc()).offset(offset).limit(min(limit, 100))))


@router.post("/cases/{case_id}/score", response_model=EvaluationCaseRead)
def submit_prediction(case_id: int, payload: EvaluationPrediction, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> EvaluationCase:
    case = db.scalar(select(EvaluationCase).where(EvaluationCase.id == case_id, EvaluationCase.user_id == user_id))
    if case is None:
        raise HTTPException(status_code=404, detail="Evaluation case not found")
    score_case(case, payload.prediction)
    db.commit(); db.refresh(case)
    return case


@router.get("/summary", response_model=EvaluationSummary)
def evaluation_summary(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> EvaluationSummary:
    return summarize_cases(db, user_id)


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> None:
    case = db.scalar(select(EvaluationCase).where(EvaluationCase.id == case_id, EvaluationCase.user_id == user_id))
    if case is None:
        raise HTTPException(status_code=404, detail="Evaluation case not found")
    db.delete(case); db.commit()
