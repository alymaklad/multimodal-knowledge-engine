from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.collection import Collection
from app.models.job import ProcessingJob
from app.models.reel import Reel


def get_owned_collection(db: Session, collection_id: int, user_id: str) -> Collection | None:
    return db.scalar(select(Collection).where(Collection.id == collection_id, Collection.user_id == user_id))


def get_owned_reel(db: Session, reel_id: int, user_id: str) -> Reel | None:
    return db.scalar(select(Reel).join(Collection).where(Reel.id == reel_id, Collection.user_id == user_id))


def get_owned_job(db: Session, job_id: int, user_id: str) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob).join(Reel).join(Collection).where(ProcessingJob.id == job_id, Collection.user_id == user_id)
    )
