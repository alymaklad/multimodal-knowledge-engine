from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.models.collection import Collection, CollectionSource
from app.models.source import Source, SourceKind, SourceStatus
from app.schemas.source import SourceIngestionResponse, SourceMetadataUpdate, SourceRead, SourceUrlCreate
from app.schemas.review import ReviewDecision
from app.models.job import JobStatus
from app.services.jobs import record_job_event
from app.services.ingestion import DuplicateSourceError, create_uploaded_source, create_url_source
from app.services.reprocessing import reset_source_for_reprocessing
from app.services.storage import save_uploaded_media
from app.services.authorization import get_owned_collection, get_owned_source
from app.services.lifecycle import delete_source_and_local_media

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _optional_collection(db: Session, collection_id: int | None, user_id: str) -> Collection | None:
    if collection_id is None:
        return None
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


@router.post("/url", response_model=SourceIngestionResponse, status_code=status.HTTP_202_ACCEPTED)
def add_url_source(
    payload: SourceUrlCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> SourceIngestionResponse:
    collection = _optional_collection(db, payload.collection_id, user_id)
    try:
        source = create_url_source(
            db, user_id=user_id, url=str(payload.url), title=payload.title, collection=collection
        )
    except DuplicateSourceError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "This link is already in your library", "source_id": error.source.id},
        ) from error
    return SourceIngestionResponse(source_id=source.id, status=source.status, message="Source queued for processing")


@router.post("/upload", response_model=SourceIngestionResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_source(
    media: UploadFile = File(...),
    collection_id: int | None = Form(default=None, gt=0),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> SourceIngestionResponse:
    collection = _optional_collection(db, collection_id, user_id)
    upload = await save_uploaded_media(media)
    try:
        source = create_uploaded_source(
            db,
            user_id=user_id,
            upload=upload,
            kind=SourceKind.video,
            original_filename=media.filename,
            mime_type=media.content_type,
            collection=collection,
        )
    except DuplicateSourceError as error:
        Path(upload.path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "This file is already in your library", "source_id": error.source.id},
        ) from error
    except Exception:
        Path(upload.path).unlink(missing_ok=True)
        raise
    return SourceIngestionResponse(source_id=source.id, status=source.status, message="Uploaded source queued for processing")


@router.get("", response_model=list[SourceRead])
def list_library(
    kind: SourceKind | None = None,
    status_filter: SourceStatus | None = None,
    collection_id: int | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[Source]:
    """The user's whole library, optionally narrowed to one collection's members."""
    query = select(Source).where(Source.user_id == user_id)
    if collection_id is not None:
        if get_owned_collection(db, collection_id, user_id) is None:
            raise HTTPException(status_code=404, detail="Collection not found")
        query = query.join(CollectionSource).where(CollectionSource.collection_id == collection_id)
    if kind:
        query = query.where(Source.kind == kind)
    if status_filter:
        query = query.where(Source.status == status_filter)
    return list(db.scalars(query.order_by(Source.created_at.desc(), Source.id.desc()).offset(offset).limit(min(limit, 100))))


@router.post("/{source_id}/reprocess", response_model=SourceIngestionResponse, status_code=status.HTTP_202_ACCEPTED)
def reprocess_source(
    source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> SourceIngestionResponse:
    source = get_owned_source(db, source_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    reset_source_for_reprocessing(db, source)
    return SourceIngestionResponse(source_id=source.id, status=source.status, message="Source requeued for processing")


@router.get("/review/queue", response_model=list[SourceRead])
def review_queue(offset: int = 0, limit: int = 50, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[Source]:
    return list(db.scalars(select(Source).where(Source.user_id == user_id, Source.status == SourceStatus.needs_review).order_by(Source.updated_at).offset(offset).limit(min(limit, 100))))


@router.get("/{source_id}", response_model=SourceRead)
def get_source(source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> Source:
    source = get_owned_source(db, source_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.patch("/{source_id}", response_model=SourceRead)
def update_source_metadata(
    source_id: int, payload: SourceMetadataUpdate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> Source:
    source = get_owned_source(db, source_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    # Only fields the client sent are changed; the title is required, so it can be renamed but not cleared.
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "title" and value is None:
            continue
        setattr(source, field, value)
    db.commit(); db.refresh(source)
    return source


@router.post("/{source_id}/review", response_model=SourceRead)
def review_source(
    source_id: int, payload: ReviewDecision, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> Source:
    source = get_owned_source(db, source_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.status != SourceStatus.needs_review or source.processing_job is None:
        raise HTTPException(status_code=409, detail="This source is not awaiting review")
    accepted = payload.decision == "accepted"
    source.status = SourceStatus.completed if accepted else SourceStatus.failed
    source.processing_job.status = JobStatus.completed if accepted else JobStatus.failed
    source.processing_job.current_stage = "completed" if accepted else "rejected"
    source.processing_job.error_message = None if accepted else payload.note or "Rejected during review"
    record_job_event(
        db, job_id=source.processing_job.id, stage=source.processing_job.current_stage,
        event_type="review_accepted" if accepted else "review_rejected", message=payload.note,
    )
    db.commit(); db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> None:
    """Remove a source from the library, and so from every collection that contains it."""
    source = get_owned_source(db, source_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    delete_source_and_local_media(db, source)
