from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.job import JobStatus, ProcessingJob
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.reel import Reel, ReelStatus
from app.providers.base import ExtractionProvider, OCRProvider, SpeechProvider, VisionProvider
from app.models.stage_metric import StageMetric, StageStatus
from app.services.analysis import AnalysisOutcome, run_analysis
from app.services.embeddings import index_reel
from app.services.jobs import record_job_event
from app.services.provenance import record_provenance
from app.services.publishing import publish_reel_media
from app.services.verification import verify_extractions
from app.services.video import FFmpegVideoProcessor, VideoProcessingError


def prepare_reel_media(db: Session, job_id: int, processor: FFmpegVideoProcessor | None = None) -> ProcessingJob:
    """Execute the first worker stage and leave the job queued for downstream analysis."""
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise LookupError(f"Processing job {job_id} does not exist")
    reel = db.get(Reel, job.reel_id)
    if reel is None or not reel.media_path:
        return _fail_job(db, job, reel, "No local media is available for preparation")

    job.status = JobStatus.running
    job.current_stage = "media_preparation"
    job.attempt_count += 1
    reel.status = ReelStatus.processing
    record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="started")
    db.commit()

    output_directory = Path(get_settings().media_root) / "prepared" / str(reel.id)
    try:
        prepared = (processor or FFmpegVideoProcessor()).prepare(reel.media_path, output_directory)
    except VideoProcessingError as error:
        return _fail_job(db, job, reel, str(error))

    # Retry-safe: generated artifacts replace the prior output from this stage.
    db.execute(delete(MediaAsset).where(MediaAsset.reel_id == reel.id))
    if prepared.audio_path:
        db.add(MediaAsset(reel_id=reel.id, kind=MediaAssetKind.audio, path=prepared.audio_path))
    for sequence_number, frame_path in enumerate(prepared.frame_paths, start=1):
        db.add(
            MediaAsset(
                reel_id=reel.id,
                kind=MediaAssetKind.frame,
                path=frame_path,
                sequence_number=sequence_number,
                timestamp_seconds=(sequence_number - 1) / get_settings().frame_sample_fps,
            )
        )
    reel.duration = prepared.metadata.duration_seconds
    db.flush()
    published = publish_reel_media(db, reel)
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


def _fail_job(db: Session, job: ProcessingJob, reel: Reel | None, message: str) -> ProcessingJob:
    job.status = JobStatus.failed
    job.error_message = message
    if reel is not None:
        reel.status = ReelStatus.failed
        reel.error_message = message
    record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="failed", message=message)
    db.commit()
    db.refresh(job)
    return job


def analyze_reel(
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
    reel = db.get(Reel, job.reel_id)
    if reel is None:
        return _fail_job(db, job, None, "The Reel associated with this job no longer exists")
    job.status = JobStatus.running
    job.current_stage = "multimodal_analysis"
    job.attempt_count += 1
    record_job_event(db, job_id=job.id, stage=job.current_stage, event_type="started")
    db.commit()
    try:
        outcome = run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=speech_provider,
            ocr_provider=ocr_provider,
            vision_provider=vision_provider,
            extraction_provider=extraction_provider,
            fallback_vision_provider=fallback_vision_provider,
        )
        _record_stage_metrics(db, reel_id=reel.id, job_id=job.id, outcome=outcome)
        db.flush()
        db.refresh(reel)
        index_reel(db, reel)
        record_provenance(db, reel_id=reel.id, processing_version=reel.processing_version)
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
        reel.status = ReelStatus.needs_review if requires_review else ReelStatus.completed
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
        return _fail_job(db, job, reel, str(error))
    db.refresh(job)
    return job


def _record_stage_metrics(db: Session, *, reel_id: int, job_id: int, outcome: AnalysisOutcome) -> None:
    """Append this attempt's per-stage timings (architecture section 49)."""
    for stage in outcome.stages:
        db.add(
            StageMetric(
                reel_id=reel_id,
                job_id=job_id,
                stage=stage.stage,
                status=StageStatus(stage.status),
                duration_ms=round(stage.duration_ms, 3),
                model=stage.model,
                items_processed=stage.items_processed,
                error_message=stage.error_message,
            )
        )
