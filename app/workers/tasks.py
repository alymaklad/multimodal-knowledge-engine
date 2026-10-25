from app.db.database import SessionLocal
from app.workers.celery_app import celery_app
from app.workers.pipeline import prepare_reel_media
from app.workers.pipeline import analyze_reel
from app.providers.local import EvidenceVisionProvider
from app.providers.factory import get_extraction_provider, get_ocr_provider, get_vision_provider
from app.providers.speech import get_speech_provider


@celery_app.task(name="reels.prepare_media", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def prepare_media_task(job_id: int) -> dict[str, str | int]:
    """Celery entry point for the first executable pipeline stage."""
    with SessionLocal() as db:
        job = prepare_reel_media(db, job_id)
        if job.status.value == "queued" and job.current_stage == "ocr_and_transcription":
            analyze_media_task.delay(job.id)
        return {"job_id": job.id, "status": job.status.value, "stage": job.current_stage}


@celery_app.task(name="reels.analyze_media", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def analyze_media_task(job_id: int) -> dict[str, str | int]:
    """Run the executable local analysis baseline after FFmpeg preparation."""
    with SessionLocal() as db:
        job = analyze_reel(
            db,
            job_id,
            speech_provider=get_speech_provider(),
            ocr_provider=get_ocr_provider(),
            vision_provider=get_vision_provider(),
            extraction_provider=get_extraction_provider(),
            # A remote VLM outage degrades to the local evidence description rather than
            # discarding a Reel whose OCR and transcript succeeded.
            fallback_vision_provider=EvidenceVisionProvider(),
        )
        return {"job_id": job.id, "status": job.status.value, "stage": job.current_stage}
