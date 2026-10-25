from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.models.collection import Collection
from app.schemas.reporting import CollectionReport, NarratedCollectionReport
from app.services.reporting import build_collection_report, build_narrated_report
from app.services.authorization import get_owned_collection

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/collections/{collection_id}", response_model=CollectionReport)
def collection_report(
    collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> CollectionReport:
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return build_collection_report(db, collection_id)


@router.get("/collections/{collection_id}/narrative", response_model=NarratedCollectionReport)
def narrated_collection_report(
    collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> NarratedCollectionReport:
    """Aggregates plus a summary written from those aggregates, never from the Reels themselves."""
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return build_narrated_report(db, collection_id)
