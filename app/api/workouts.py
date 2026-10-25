from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.models.collection import Collection
from app.schemas.workout import WorkoutPlan, WorkoutPlanRequest
from app.services.workout import generate_workout_plan
from app.services.authorization import get_owned_collection

router = APIRouter(prefix="/api/workouts", tags=["workouts"])


@router.post("/generate", response_model=WorkoutPlan)
def generate_workout(
    payload: WorkoutPlanRequest, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> WorkoutPlan:
    if get_owned_collection(db, payload.collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    try:
        return generate_workout_plan(db, payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
