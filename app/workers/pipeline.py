from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.job import JobStatus, ProcessingJob
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.source import Source, SourceStatus
from app.providers.base import ExtractionProvider, OCRProvider, SpeechProvider, VisionProvider
from app.models.stage_metric import StageMetric, StageStatus
from app.services.analysis import AnalysisOutcome, run_analysis
from app.services.embeddings import index_source
from app.services.jobs import record_job_event
from app.services.provenance import record_provenance
from app.services.publishing import publish_source_media
from app.services.verification import verify_extractions
from app.services.video import FFmpegVideoProcessor, VideoProcessingError


def prepare_source_media(db: Session, job_id: int, processor: FFmpegVideoProcessor | None = None) -> ProcessingJob:
    """Execute the first worker stage and leave the job queued for downstream analysis."""
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"Processing job {job_id} does not exist")
    source = db.get(Source, job.source_id)
    if source is None or not source.media_path:
        return _fail_job(db, job, source, "No local media is available for preparation")

    job.status = JobStatus.running
    job.current_stage = "media_preparation"
    job.attempt_count += 1
    source.status = SourceStatus.processing
    record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="started")
    db.commit()

    output_directory = Path(get_settings().media_root) / "prepared" / str(source.id)
    try:
        prepared = (processor or FFmpegVideoProcessor()).prepare(source.media_path, output_directory)
    except VideoProcessingError as error:
        return _fail_job(db, job, source, str(error))

    # Retry-safe: generated artifacts replace the prior output from this stage.
    db.execute(delete(MediaAsset).where(MediaAsset.source_id == source.id))
    if prepared.audio_path:
        db.add(MediaAsset(source_id=source.id, kind=MediaAssetKind.audio, path=prepared.audio_path))
    for sequence_number, frame_path in enumerate(prepared.frame_paths, start=1):
        db.add(
            MediaAsset(
                source_id=source.id,
                kind=MediaAssetKind.frame,
                path=frame_path,
                sequence_number=sequence_number,
                timestamp_seconds=(sequence_number - 1) / get_settings().frame_sample_fps,
            )
        )
    source.duration = prepared.metadata.duration_seconds
    db.flush()
    published = publish_source_media(db, source)
    job.status = JobStatus.queued
    job.current_stage = "ocr_and_transcription"
    job.error_message = None
    record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="completed", message="Media artifacts prepared")
    record_job_event(
        db,
        job_id=job.id,
        stage="media_publication",
        event_type="published",
        message=f"{published.published} objects stored in the {published.backend} object store",
    )
    db.commit()
    db.refresh(job)
    return job


def _fail_job(db: Session, job: ProcessingJob, source: Source | None, message: str) -> ProcessingJob:
    job.status = JobStatus.failed
    job.error_message = message
    if source is not None:
        source.status = SourceStatus.failed
        source.error_message = message
    record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="failed", message=message)
    db.commit()
    db.refresh(job)
    return job


def analyze_source(
    db: Session,
    job_id: int,
    *,
    speech_provider: SpeechProvider | None,
    ocr_provider: OCRProvider,
    vision_provider: VisionProvider,
    extraction_provider: ExtractionProvider,
    fallback_vision_provider: VisionProvider | None = None,
) -> ProcessingJob:
    """Execute the provider-backed multimodal analysis stage after media preparation."""
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"Processing job {job_id} does not exist")
    source = db.get(Source, job.source_id)
    if source is None:
        return _fail_job(db, job, None, "The source associated with this job no longer exists")
    job.status = JobStatus.running
    job.current_stage = "multimodal_analysis"
    job.attempt_count += 1
    record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="started")
    db.commit()
    try:
        outcome = run_analysis(
            db,
            source_id=source.id,
            speech_provider=speech_provider,
            ocr_provider=ocr_provider,
            vision_provider=vision_provider,
            extraction_provider=extraction_provider,
            fallback_vision_provider=fallback_vision_provider,
        )
        _record_stage_metrics(db, source_id=source.id, job_id=job.id, outcome=outcome)
        db.flush()
        db.refresh(source)
        index_source(db, source)
        record_provenance(db, source_id=source.id, processing_version=source.processing_version)
        verdict = verify_extractions(
            outcome.extractions, transcript=outcome.transcript_text, ocr_text=outcome.ocr_text
        )
        # A stage that failed outright means the knowledge rests on partial evidence, so it is
        # reviewed regardless of how confident the extraction that survived happens to be.
        requires_review = verdict.requires_review or outcome.degraded
        if outcome.degraded:
            record_job_event(
                db,
                job_id=job.id,
                stage="multimodal_analysis",
                event_type="degraded",
                message="Stages that failed: " + ", ".join(outcome.failed_stages),
            )
        source.status = SourceStatus.needs_review if requires_review else SourceStatus.completed
        job.status = JobStatus.needs_review if requires_review else JobStatus.completed
        job.current_stage = "needs_review" if requires_review else "completed"
        job.error_message = None
        # The decision and its reason are kept in the job history, not just the resulting status.
        record_job_event(
            db,
            job_id=job.id,
            stage="confidence_verification",
            event_type=verdict.decision.value,
            message=verdict.reason,
        )
        record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="needs_review" if requires_review else "completed")
        db.commit()
    except Exception as error:
        return _fail_job(db, job, source, str(error))
    db.refresh(job)
    return job


def _record_stage_metrics(db: Session, *, source_id: int, job_id: int, outcome: AnalysisOutcome) -> None:
    """Append this attempt's per-stage timings (architecture section 49)."""
    for stage in outcome.stages:
        db.add(
            StageMetric(
                source_id=source_id,
                job_id=job_id,
                stage=stage.stage,
                status=StageStatus(stage.status),
                duration_ms=round(stage.duration_ms, 3),
                model=stage.model,
                items_processed=stage.items_processed,
                error_message=stage.error_message,
            )
        )
