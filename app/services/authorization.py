from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.collection import Collection
from app.models.job import ProcessingJob
from app.models.source import Source


def get_owned_collection(db: Session, collection_id: int, user_id: str) -> Collection | None:
    return db.scalar(select(Collection).where(Collection.id == collection_id, Collection.user_id == user_id))


def get_owned_source(db: Session, source_id: int, user_id: str) -> Source | None:
    return db.scalar(select(Source).where(Source.id == source_id, Source.user_id == user_id))


def get_owned_job(db: Session, job_id: int, user_id: str) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob).join(Source).where(ProcessingJob.id == job_id, Source.user_id == user_id)
    )
