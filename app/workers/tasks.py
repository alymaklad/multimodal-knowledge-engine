from app.db.database import SessionLocal
from app.workers.celery_app import celery_app
from app.processors.registry import default_context
from app.workers.pipeline import AWAITING_PROCESSOR, process_source_content, start_source_job
from app.workers.pipeline import analyze_source
from app.providers.local import EvidenceVisionProvider
from app.providers.factory import get_extraction_provider, get_ocr_provider, get_vision_provider
from app.providers.speech import get_speech_provider


@celery_app.task(name="sources.start", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def start_source_task(job_id: int) -> dict[str, str | int]:
    """Celery entry point for the first stage of any source: fetch, prepare, or hold by kind."""
    with SessionLocal() as db:
        job = start_source_job(db, job_id)
        if job.status.value == "queued" and job.current_stage == "ocr_and_transcription":
            analyze_media_task.delay(job.id)
        elif job.status.value == "queued" and job.current_stage == AWAITING_PROCESSOR:
            extract_content_task.delay(job.id)
        return {"job_id": job.id, "status": job.status.value, "stage": job.current_stage}


@celery_app.task(name="sources.analyze_media", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def analyze_media_task(job_id: int) -> dict[str, str | int]:
    """Run the executable local analysis baseline after FFmpeg preparation."""
    with SessionLocal() as db:
        job = analyze_source(
            db,
            job_id,
            speech_provider=get_speech_provider(),
            ocr_provider=get_ocr_provider(),
            vision_provider=get_vision_provider(),
            extraction_provider=get_extraction_provider(),
            # A remote VLM outage degrades to the local evidence description rather than
            # discarding a source whose OCR and transcript succeeded.
            fallback_vision_provider=EvidenceVisionProvider(),
        )
        return {"job_id": job.id, "status": job.status.value, "stage": job.current_stage}


@celery_app.task(name="sources.extract_content", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def extract_content_task(job_id: int) -> dict[str, str | int]:
    """Turn a stored document, page, transcript, image, or recording into indexed segments."""
    with SessionLocal() as db:
        job = process_source_content(db, job_id, context=default_context())
        return {"job_id": job.id, "status": job.status.value, "stage": job.current_stage}
