from sqlalchemy.orm import Session

from app.models.job_event import JobEvent


def record_job_event(db: Session, *, job_id: int, stage: str, event_type: str, message: str | None = None) -> None:
    db.add(JobEvent(job_id=job_id, stage=stage, event_type=event_type, message=message))
