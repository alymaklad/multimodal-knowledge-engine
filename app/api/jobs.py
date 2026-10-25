from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.models.job import ProcessingJob
from app.models.job_event import JobEvent
from app.models.source import Source
from app.models.task_dispatch import TaskDispatch
from app.schemas.job import JobRead
from app.schemas.job_event import JobEventRead
from app.schemas.dispatch import TaskDispatchRead
from app.workers.celery_app import celery_app
from app.services.dispatch import JobDispatchError, dispatch_media_preparation
from app.services.authorization import get_owned_job
from app.services.reprocessing import reset_source_for_reprocessing
from app.services.jobs import record_job_event

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/source/{source_id}", response_model=JobRead)
def get_source_job(
    source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> ProcessingJob:
    job = db.scalar(
        select(ProcessingJob)
        .join(ProcessingJob.source)
        .where(ProcessingJob.source_id == source_id, Source.user_id == user_id)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    return job


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> ProcessingJob:
    job = get_owned_job(db, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    return job


@router.get("/{job_id}/events", response_model=list[JobEventRead])
def list_job_events(
    job_id: int, event_type: str | None = None, offset: int = 0, limit: int = 100, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> list[JobEvent]:
    if get_owned_job(db, job_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    query = select(JobEvent).where(JobEvent.job_id == job_id)
    if event_type:
        query = query.where(JobEvent.event_type == event_type)
    return list(db.scalars(query.order_by(JobEvent.created_at, JobEvent.id).offset(offset).limit(min(limit, 200))))


@router.get("", response_model=list[JobRead])
def list_jobs(
    status_filter: str | None = None, offset: int = 0, limit: int = 50,
    db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> list[ProcessingJob]:
    query = select(ProcessingJob).join(ProcessingJob.source).where(Source.user_id == user_id)
    if status_filter:
        query = query.where(ProcessingJob.status == status_filter)
    return list(db.scalars(query.order_by(ProcessingJob.created_at.desc()).offset(offset).limit(min(limit, 100))))


@router.post("/{job_id}/dispatch", status_code=202)
def dispatch_job(
    job_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> dict[str, str | int]:
    job = get_owned_job(db, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    if job.current_stage != "ingestion":
        raise HTTPException(status_code=409, detail="Only new ingestion jobs can be dispatched for media preparation")
    if not job.source.media_path:
        raise HTTPException(status_code=422, detail="A local media upload is required before this job can be dispatched")
    try:
        task_id = dispatch_media_preparation(job.id)
    except JobDispatchError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    record_job_event(db, job_id=job.id, stage="ingestion", event_type="dispatched", message=f"Celery task {task_id}")
    db.add(TaskDispatch(job_id=job.id, task_id=task_id, task_name="sources.prepare_media"))
    db.commit()
    return {"job_id": job.id, "task_id": task_id, "message": "Media preparation queued"}


@router.get("/{job_id}/dispatches", response_model=list[TaskDispatchRead])
def list_dispatches(job_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[TaskDispatch]:
    if get_owned_job(db, job_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    return list(db.scalars(select(TaskDispatch).where(TaskDispatch.job_id == job_id).order_by(TaskDispatch.created_at.desc())))


@router.post("/{job_id}/cancel", status_code=202)
def cancel_queued_job(job_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> dict[str, str | int]:
    job = get_owned_job(db, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    dispatch = db.scalar(select(TaskDispatch).where(TaskDispatch.job_id == job.id, TaskDispatch.state == "queued").order_by(TaskDispatch.created_at.desc()))
    if dispatch is None:
        raise HTTPException(status_code=409, detail="No queued dispatch is available to cancel")
    celery_app.control.revoke(dispatch.task_id, terminate=False)
    dispatch.state = "cancelled"
    job.status = JobStatus.failed; job.current_stage = "cancelled"; job.error_message = "Cancelled by user"
    record_job_event(db, job_id=job.id, stage="cancelled", event_type="cancelled", message=f"Celery task {dispatch.task_id}")
    db.commit()
    return {"job_id": job.id, "task_id": dispatch.task_id, "message": "Queued task cancelled"}


@router.post("/{job_id}/retry", status_code=202)
def retry_job(job_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> dict[str, str | int]:
    job = get_owned_job(db, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    if job.status.value not in {"failed", "needs_review"}:
        raise HTTPException(status_code=409, detail="Only failed or review-required jobs can be retried")
    reset_source_for_reprocessing(db, job.source)
    try:
        task_id = dispatch_media_preparation(job.id)
    except JobDispatchError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    record_job_event(db, job_id=job.id, stage="ingestion", event_type="dispatched", message=f"Retry task {task_id}")
    db.add(TaskDispatch(job_id=job.id, task_id=task_id, task_name="sources.prepare_media"))
    db.commit()
    return {"job_id": job.id, "task_id": task_id, "message": "Job requeued"}
