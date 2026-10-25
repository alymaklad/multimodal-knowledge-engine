"""An in-process background worker, for running without Redis and Celery.

The architecture's rule is that the HTTP request must not run the pipeline — not that the pipeline
must run on another machine. For a single-user local install, a background thread satisfies the
rule with none of the infrastructure: the request returns `202` immediately and the work happens
after it.

Deliberately **one job at a time**. The vision model, Whisper and the embedding model share one 8 GB
GPU; running two sources concurrently is the reliable way to hit out-of-memory. A queue that is slow
and finishes is worth more than parallelism that dies half way.

What this gives up compared to Celery, stated plainly:

- Work in flight is lost if the process stops. The `ProcessingJob` row survives, so the source can be
  processed again — nothing is silently dropped, but a restart mid-analysis means redoing it.
- No horizontal scaling, no retry backoff, no cross-machine workers.

Set `TASK_RUNNER=celery` for the durable path once Redis is available.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid

from app.db.database import SessionLocal
from app.models.job import JobStatus, ProcessingJob
from app.services.links import FETCHED_KINDS

logger = logging.getLogger(__name__)

_queue: queue.Queue[int] = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


def submit(job_id: int) -> str:
    """Queue a job for the background thread and return a handle for the dispatch record."""
    _ensure_worker()
    _queue.put(job_id)
    logger.info("Queued job for the local runner", extra={"job_id": job_id, "depth": _queue.qsize()})
    return f"local-{uuid.uuid4().hex[:12]}"


def pending_depth() -> int:
    return _queue.qsize()


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            # Daemon: a queued job must never keep the process alive at shutdown.
            _worker = threading.Thread(target=_run_forever, name="local-task-runner", daemon=True)
            _worker.start()
            logger.info("Started the local task runner")


def _run_forever() -> None:
    while True:
        job_id = _queue.get()
        try:
            _process(job_id)
        except Exception:
            # Never let one bad job kill the worker; the job row already carries the failure.
            logger.exception("Local runner failed", extra={"job_id": job_id})
        finally:
            _queue.task_done()


def _process(job_id: int) -> None:
    # Imported here so configuring TASK_RUNNER=local does not drag model libraries into
    # every process that merely imports this module.
    from app.providers.factory import (
        get_extraction_provider,
        get_ocr_provider,
        get_vision_provider,
    )
    from app.providers.local import EvidenceVisionProvider
    from app.providers.speech import get_speech_provider
    from app.processors.registry import default_context
    from app.workers.pipeline import (
        AWAITING_PROCESSOR,
        CONTENT_EXTRACTION,
        analyze_source,
        process_source_content,
        start_source_job,
    )

    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            logger.warning("Job vanished before the runner reached it", extra={"job_id": job_id})
            return

        # `fetching` is included so a link whose fetch was cut off by a restart is fetched again.
        if job.current_stage in ("ingestion", "fetching"):
            job = start_source_job(db, job_id)
            if job.status is JobStatus.failed:
                return

        # Every non-video source arrives here once its raw content is stored. A job interrupted
        # inside extraction is run again; the stage replaces its own output.
        if job.current_stage in (AWAITING_PROCESSOR, CONTENT_EXTRACTION):
            process_source_content(db, job_id, context=default_context())
            return

        # Same chaining the Celery task does: preparation leaves the job ready for analysis.
        # `multimodal_analysis` is accepted as well so a job resumed after a worker died *inside*
        # that stage actually re-runs it; matching only the entry stage would re-queue such a job
        # and then silently do nothing with it, leaving it exactly as stuck as before.
        if job.current_stage in ("ocr_and_transcription", "multimodal_analysis"):
            analyze_source(
                db,
                job_id,
                speech_provider=get_speech_provider(),
                ocr_provider=get_ocr_provider(),
                vision_provider=get_vision_provider(),
                extraction_provider=get_extraction_provider(),
                fallback_vision_provider=EvidenceVisionProvider(),
            )


def resume_unfinished() -> int:
    """Re-queue jobs left mid-flight by a previous run.

    In-process work does not survive a restart, so without this a source uploaded just before a
    restart would sit at `queued` forever with nothing coming to collect it.
    """
    with SessionLocal() as db:
        # `multimodal_analysis` belongs here too: a worker that dies mid-analysis - an allocation
        # failure is the realistic way that happens - leaves the job at `running` on that stage with
        # nothing that will ever collect it, so the source spins in the interface forever. Observed,
        # not hypothetical. Re-running the stage is safe: it is retry-safe by design and replaces
        # its own prior output.
        stages = (
            "ingestion",
            "fetching",
            "awaiting_processor",
            "content_extraction",
            "ocr_and_transcription",
            "multimodal_analysis",
        )
        jobs = [
            job.id
            for job in db.query(ProcessingJob)
            .filter(ProcessingJob.status.in_([JobStatus.queued, JobStatus.running]))
            .filter(ProcessingJob.current_stage.in_(stages))
            .all()
            # A source with neither a file nor a fetchable link can never progress; queueing it would
            # spin forever.
            if job.source is not None and (job.source.media_path or job.source.kind in FETCHED_KINDS)
        ]
    for job_id in jobs:
        submit(job_id)
    if jobs:
        logger.info("Resumed unfinished jobs", extra={"count": len(jobs)})
    return len(jobs)
