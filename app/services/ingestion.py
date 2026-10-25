from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.job import ProcessingJob
from app.models.reel import Reel, ReelStatus
from app.schemas.reel import ReelCreate
from app.services.jobs import record_job_event
from app.services.storage import SavedUpload


class DuplicateReelError(Exception):
    def __init__(self, reel: Reel):
        self.reel = reel


def create_reel_record(db: Session, request: ReelCreate) -> Reel:
    """Create the durable ingestion record; worker dispatch is added in a later phase."""
    url = str(request.instagram_url).rstrip("/")
    existing = db.scalar(
        select(Reel).where(Reel.collection_id == request.collection_id, Reel.instagram_url == url)
    )
    if existing:
        raise DuplicateReelError(existing)

    reel = Reel(
        collection_id=request.collection_id,
        instagram_url=url,
        status=ReelStatus.pending,
        processing_version=get_settings().processing_version,
    )
    db.add(reel)
    db.flush()
    job = ProcessingJob(reel_id=reel.id)
    db.add(job)
    db.flush()
    record_job_event(db, job_id=job.id, stage="ingestion", event_type="queued")
    db.commit()
    db.refresh(reel)
    return reel


def create_uploaded_reel_record(db: Session, *, collection_id: int, upload: SavedUpload) -> Reel:
    """Create an ingestion record for media explicitly supplied by the user."""
    duplicate = db.scalar(
        select(Reel).where(Reel.collection_id == collection_id, Reel.external_id == upload.sha256)
    )
    if duplicate:
        raise DuplicateReelError(duplicate)
    reel = Reel(
        collection_id=collection_id,
        instagram_url=f"upload://{upload.path.replace(chr(92), '/').rsplit('/', 1)[-1]}",
        external_id=upload.sha256,
        media_path=upload.path,
        status=ReelStatus.pending,
        processing_version=get_settings().processing_version,
    )
    db.add(reel)
    db.flush()
    job = ProcessingJob(reel_id=reel.id)
    db.add(job)
    db.flush()
    record_job_event(db, job_id=job.id, stage="ingestion", event_type="queued")
    db.commit()
    db.refresh(reel)
    return reel
