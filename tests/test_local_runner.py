"""The in-process background runner used when Redis and Celery are not available."""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.main import app
from app.models.collection import Collection
from app.models.job import JobStatus, ProcessingJob
from app.models.reel import Reel, ReelStatus
from app.services import local_runner
from app.services.dispatch import JobDispatchError, dispatch_media_preparation

client = TestClient(app)


def _seed(name: str, *, media: bool = True, stage: str = "ingestion") -> int:
    with SessionLocal() as db:
        collection = Collection(name=name, user_id="runner-owner")
        db.add(collection)
        db.flush()
        reel = Reel(
            collection_id=collection.id,
            instagram_url=f"upload://{name}.mp4",
            media_path=f"data/test-run/media/{name}.mp4" if media else None,
            status=ReelStatus.pending,
            processing_version="v1",
        )
        db.add(reel)
        db.flush()
        job = ProcessingJob(reel_id=reel.id, status=JobStatus.queued, current_stage=stage)
        db.add(job)
        db.commit()
        return job.id


def test_dispatch_routes_to_the_local_runner(monkeypatch) -> None:
    """With TASK_RUNNER=local there is no broker probe and no Celery involvement."""
    monkeypatch.setenv("TASK_RUNNER", "local")
    get_settings.cache_clear()
    submitted: list[int] = []
    monkeypatch.setattr(local_runner, "submit", lambda job_id: submitted.append(job_id) or "local-test")
    try:
        assert dispatch_media_preparation(4242) == "local-test"
        assert submitted == [4242]
    finally:
        get_settings.cache_clear()


def test_dispatch_still_uses_celery_by_default(monkeypatch) -> None:
    """The default path must keep probing the broker rather than silently running in-process."""
    monkeypatch.setenv("TASK_RUNNER", "celery")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:1/0")  # nothing listening
    get_settings.cache_clear()
    try:
        with pytest.raises(JobDispatchError, match="not reachable"):
            dispatch_media_preparation(1)
    finally:
        get_settings.cache_clear()


def test_the_runner_processes_a_queued_job() -> None:
    """The thread should pick the job up and drive the pipeline without anything else prompting it."""
    job_id = _seed("runner-runs")
    processed: list[int] = []

    original = local_runner._process
    local_runner._process = lambda job_id: processed.append(job_id)
    try:
        handle = local_runner.submit(job_id)
        assert handle.startswith("local-")
        deadline = time.time() + 5
        while not processed and time.time() < deadline:
            time.sleep(0.05)
        assert processed == [job_id]
    finally:
        local_runner._process = original


def test_one_bad_job_does_not_kill_the_worker() -> None:
    """A failing job must not stop everything queued behind it."""
    seen: list[int] = []

    def flaky(job_id: int) -> None:
        seen.append(job_id)
        if job_id == 1:
            raise RuntimeError("boom")

    original = local_runner._process
    local_runner._process = flaky
    try:
        local_runner.submit(1)
        local_runner.submit(2)
        deadline = time.time() + 5
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert seen == [1, 2]
    finally:
        local_runner._process = original


def test_resume_requeues_unfinished_jobs() -> None:
    job_id = _seed("runner-resume", stage="ocr_and_transcription")
    submitted: list[int] = []
    original = local_runner.submit
    local_runner.submit = lambda job_id: submitted.append(job_id) or "local-x"
    try:
        local_runner.resume_unfinished()
        assert job_id in submitted
    finally:
        local_runner.submit = original


def test_resume_skips_reels_with_no_media() -> None:
    """A URL-only Reel can never progress; queueing it would spin the worker forever."""
    job_id = _seed("runner-url-only", media=False)
    submitted: list[int] = []
    original = local_runner.submit
    local_runner.submit = lambda job_id: submitted.append(job_id) or "local-x"
    try:
        local_runner.resume_unfinished()
        assert job_id not in submitted
    finally:
        local_runner.submit = original


def test_resume_skips_jobs_that_already_finished() -> None:
    job_id = _seed("runner-done")
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = JobStatus.completed
        job.current_stage = "completed"
        db.commit()

    submitted: list[int] = []
    original = local_runner.submit
    local_runner.submit = lambda job_id: submitted.append(job_id) or "local-x"
    try:
        local_runner.resume_unfinished()
        assert job_id not in submitted
    finally:
        local_runner.submit = original


def test_resume_recovers_a_job_that_died_during_analysis() -> None:
    """A worker killed inside the analysis stage leaves the job at `running` on that stage.

    Until this was covered, such a job was never re-queued: the Reel sat at `processing` with
    nothing coming to collect it, spinning in the interface forever. Observed on a real Reel whose
    vision stage exhausted the GPU, not hypothesised.
    """
    job_id = _seed("runner-died-in-analysis", stage="multimodal_analysis")
    with SessionLocal() as db:
        db.get(ProcessingJob, job_id).status = JobStatus.running
        db.commit()

    submitted: list[int] = []
    original = local_runner.submit
    local_runner.submit = lambda job_id: submitted.append(job_id) or "local-x"
    try:
        local_runner.resume_unfinished()
        assert job_id in submitted
    finally:
        local_runner.submit = original


def test_a_resumed_analysis_job_actually_reruns_the_stage(monkeypatch) -> None:
    """Re-queueing is only half the fix: the runner must also accept the stage it resumed on.

    Matching only the entry stage would re-queue such a job and then do nothing with it, leaving it
    exactly as stuck as before while looking like it had been handled.
    """
    import app.workers.pipeline as pipeline

    job_id = _seed("runner-reruns-analysis", stage="multimodal_analysis")
    analysed: list[int] = []
    monkeypatch.setattr(pipeline, "analyze_reel", lambda db, jid, **kwargs: analysed.append(jid))

    local_runner._process(job_id)

    assert analysed == [job_id]
