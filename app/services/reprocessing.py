from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.analysis import ExtractedContent, OCRResult, Transcript, VisualAnalysis
from app.models.embedding import ContentEmbedding
from app.models.provenance import ProcessingProvenance
from app.models.segment import SourceSegment
from app.models.stored_object import StoredObject
from app.models.job import JobStatus, ProcessingJob
from app.models.media_asset import MediaAsset
from app.models.source import Source, SourceStatus
from app.services.jobs import record_job_event


def reset_source_for_reprocessing(db: Session, source: Source) -> ProcessingJob:
    """Discard derived knowledge, retain source media, and return the job to ingestion."""
    db.execute(delete(Transcript).where(Transcript.source_id == source.id))
    db.execute(delete(OCRResult).where(OCRResult.source_id == source.id))
    db.execute(delete(VisualAnalysis).where(VisualAnalysis.source_id == source.id))
    db.execute(delete(ExtractedContent).where(ExtractedContent.source_id == source.id))
    db.execute(delete(MediaAsset).where(MediaAsset.source_id == source.id))
    db.execute(delete(ContentEmbedding).where(ContentEmbedding.source_id == source.id))
    db.execute(delete(ProcessingProvenance).where(ProcessingProvenance.source_id == source.id))
    db.execute(delete(StoredObject).where(StoredObject.source_id == source.id))
    db.execute(delete(SourceSegment).where(SourceSegment.source_id == source.id))

    job = source.processing_job
    if job is None:
        job = ProcessingJob(source_id=source.id)
        db.add(job)
        db.flush()
    job.status = JobStatus.queued
    job.current_stage = "ingestion"
    job.error_message = None
    source.status = SourceStatus.pending
    source.error_message = None
    source.processing_version = get_settings().processing_version
    record_job_event(
        db,
        job_id=job.id,
        stage="ingestion",
        event_type="requeued",
        message=f"Reprocessing requested with version {source.processing_version}",
    )
    db.commit()
    db.refresh(job)
    return job
