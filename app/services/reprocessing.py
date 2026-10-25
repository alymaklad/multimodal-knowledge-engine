from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.analysis import ExtractedContent, OCRResult, Transcript, VisualAnalysis
from app.models.embedding import ContentEmbedding
from app.models.provenance import ProcessingProvenance
from app.models.stored_object import StoredObject
from app.models.job import JobStatus, ProcessingJob
from app.models.media_asset import MediaAsset
from app.models.reel import Reel, ReelStatus
from app.services.jobs import record_job_event


def reset_reel_for_reprocessing(db: Session, reel: Reel) -> ProcessingJob:
    """Discard derived knowledge, retain source media, and return the job to ingestion."""
    db.execute(delete(Transcript).where(Transcript.reel_id == reel.id))
    db.execute(delete(OCRResult).where(OCRResult.reel_id == reel.id))
    db.execute(delete(VisualAnalysis).where(VisualAnalysis.reel_id == reel.id))
    db.execute(delete(ExtractedContent).where(ExtractedContent.reel_id == reel.id))
    db.execute(delete(MediaAsset).where(MediaAsset.reel_id == reel.id))
    db.execute(delete(ContentEmbedding).where(ContentEmbedding.reel_id == reel.id))
    db.execute(delete(ProcessingProvenance).where(ProcessingProvenance.reel_id == reel.id))
    db.execute(delete(StoredObject).where(StoredObject.reel_id == reel.id))

    job = reel.processing_job
    if job is None:
        job = ProcessingJob(reel_id=reel.id)
        db.add(job)
        db.flush()
    job.status = JobStatus.queued
    job.current_stage = "ingestion"
    job.error_message = None
    reel.status = ReelStatus.pending
    reel.error_message = None
    reel.processing_version = get_settings().processing_version
    record_job_event(
        db,
        job_id=job.id,
        stage="ingestion",
        event_type="requeued",
        message=f"Reprocessing requested with version {reel.processing_version}",
    )
    db.commit()
    db.refresh(job)
    return job
