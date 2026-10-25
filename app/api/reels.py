from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.models.collection import Collection
from app.models.reel import Reel, ReelStatus
from app.schemas.reel import ReelCreate, ReelIngestionResponse, ReelMetadataUpdate, ReelRead
from app.schemas.review import ReviewDecision
from app.models.job import JobStatus
from app.services.jobs import record_job_event
from app.services.ingestion import DuplicateReelError, create_reel_record, create_uploaded_reel_record
from app.services.reprocessing import reset_reel_for_reprocessing
from app.services.storage import save_uploaded_media
from app.services.authorization import get_owned_collection, get_owned_reel
from app.services.lifecycle import delete_reel_and_local_media

router = APIRouter(prefix="/api/reels", tags=["reels"])


@router.post("", response_model=ReelIngestionResponse, status_code=status.HTTP_202_ACCEPTED)
def create_reel(
    payload: ReelCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> ReelIngestionResponse:
    if get_owned_collection(db, payload.collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    try:
        reel = create_reel_record(db, payload)
    except DuplicateReelError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Reel already exists in this collection", "reel_id": error.reel.id},
        ) from error
    return ReelIngestionResponse(reel_id=reel.id, status=reel.status, message="Reel queued for processing")


@router.post("/upload", response_model=ReelIngestionResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_reel(
    collection_id: int = Form(gt=0),
    media: UploadFile = File(...),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ReelIngestionResponse:
    """Accept user-supplied video without relying on Instagram automation."""
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    upload = await save_uploaded_media(media)
    try:
        reel = create_uploaded_reel_record(db, collection_id=collection_id, upload=upload)
    except DuplicateReelError as error:
        Path(upload.path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "This media was already uploaded to the collection", "reel_id": error.reel.id},
        ) from error
    except Exception:
        Path(upload.path).unlink(missing_ok=True)
        raise
    return ReelIngestionResponse(reel_id=reel.id, status=reel.status, message="Uploaded Reel queued for processing")


@router.post("/{reel_id}/reprocess", response_model=ReelIngestionResponse, status_code=status.HTTP_202_ACCEPTED)
def reprocess_reel(
    reel_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> ReelIngestionResponse:
    reel = get_owned_reel(db, reel_id, user_id)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    reset_reel_for_reprocessing(db, reel)
    return ReelIngestionResponse(reel_id=reel.id, status=reel.status, message="Reel requeued for processing")


@router.get("/collection/{collection_id}", response_model=list[ReelRead])
def list_reels(
    collection_id: int, status_filter: ReelStatus | None = None, offset: int = 0, limit: int = 50, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> list[Reel]:
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    query = select(Reel).where(Reel.collection_id == collection_id)
    if status_filter:
        query = query.where(Reel.status == status_filter)
    return list(db.scalars(query.order_by(Reel.created_at.desc()).offset(offset).limit(min(limit, 100))))


@router.get("/review/queue", response_model=list[ReelRead])
def review_queue(offset: int = 0, limit: int = 50, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[Reel]:
    return list(db.scalars(select(Reel).join(Collection).where(Collection.user_id == user_id, Reel.status == ReelStatus.needs_review).order_by(Reel.updated_at).offset(offset).limit(min(limit, 100))))


@router.get("/{reel_id}", response_model=ReelRead)
def get_reel(reel_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> Reel:
    reel = get_owned_reel(db, reel_id, user_id)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    return reel


@router.patch("/{reel_id}", response_model=ReelRead)
def update_reel_metadata(
    reel_id: int, payload: ReelMetadataUpdate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> Reel:
    reel = get_owned_reel(db, reel_id, user_id)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    for field, value in payload.model_dump().items():
        setattr(reel, field, value)
    db.commit(); db.refresh(reel)
    return reel


@router.post("/{reel_id}/review", response_model=ReelRead)
def review_reel(
    reel_id: int, payload: ReviewDecision, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> Reel:
    reel = get_owned_reel(db, reel_id, user_id)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    if reel.status != ReelStatus.needs_review or reel.processing_job is None:
        raise HTTPException(status_code=409, detail="This Reel is not awaiting review")
    accepted = payload.decision == "accepted"
    reel.status = ReelStatus.completed if accepted else ReelStatus.failed
    reel.processing_job.status = JobStatus.completed if accepted else JobStatus.failed
    reel.processing_job.current_stage = "completed" if accepted else "rejected"
    reel.processing_job.error_message = None if accepted else payload.note or "Rejected during review"
    record_job_event(
        db, job_id=reel.processing_job.id, stage=reel.processing_job.current_stage,
        event_type="review_accepted" if accepted else "review_rejected", message=payload.note,
    )
    db.commit(); db.refresh(reel)
    return reel


@router.delete("/{reel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reel(reel_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> None:
    reel = get_owned_reel(db, reel_id, user_id)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    delete_reel_and_local_media(db, reel)
