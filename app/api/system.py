from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.schemas.system import PipelineMetrics, UserMetrics
from app.services.metrics import get_pipeline_metrics, get_user_metrics
from app.services.export import export_collection
from app.models.collection import Collection
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/metrics", response_model=UserMetrics)
def user_metrics(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> UserMetrics:
    return get_user_metrics(db, user_id)


@router.get("/metrics/pipeline", response_model=PipelineMetrics)
def pipeline_metrics(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> PipelineMetrics:
    """Per-stage latency, failure rates, and models used across this user's analysis runs."""
    return get_pipeline_metrics(db, user_id)


@router.get("/export")
def export_user_data(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> JSONResponse:
    collections = list(db.query(Collection).filter(Collection.user_id == user_id))
    return JSONResponse(content={"user_id": user_id, "collections": [export_collection(db, collection).model_dump(mode="json") for collection in collections]})
