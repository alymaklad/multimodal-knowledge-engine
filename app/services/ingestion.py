from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.collection import Collection
from app.models.job import ProcessingJob
from app.models.source import Source, SourceKind, SourceStatus
from app.services.jobs import record_job_event
from app.services.storage import SavedUpload

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


class DuplicateSourceError(Exception):
    def __init__(self, source: Source):
        self.source = source


def kind_for_url(url: str) -> SourceKind:
    return SourceKind.youtube if (urlparse(url).hostname or "").lower() in YOUTUBE_HOSTS else SourceKind.web


def create_url_source(
    db: Session, *, user_id: str, url: str, title: str | None = None, collection: Collection | None = None
) -> Source:
    """Record a link in the library. Duplicates are detected per owner, not per collection."""
    url = url.rstrip("/")
    existing = db.scalar(select(Source).where(Source.user_id == user_id, Source.origin_url == url))
    if existing:
        raise DuplicateSourceError(existing)
    source = Source(
        user_id=user_id,
        kind=kind_for_url(url),
        title=(title or "").strip() or url,
        origin_url=url,
        status=SourceStatus.pending,
        processing_version=get_settings().processing_version,
    )
    return _queue(db, source, collection)


def create_uploaded_source(
    db: Session,
    *,
    user_id: str,
    upload: SavedUpload,
    kind: SourceKind,
    original_filename: str | None = None,
    mime_type: str | None = None,
    collection: Collection | None = None,
) -> Source:
    """Record a file the user supplied. The content hash makes a renamed copy a duplicate."""
    duplicate = db.scalar(select(Source).where(Source.user_id == user_id, Source.content_hash == upload.sha256))
    if duplicate:
        raise DuplicateSourceError(duplicate)
    source = Source(
        user_id=user_id,
        kind=kind,
        title=Path(original_filename).stem if original_filename else Path(upload.path).name,
        original_filename=original_filename,
        mime_type=mime_type,
        content_hash=upload.sha256,
        media_path=upload.path,
        status=SourceStatus.pending,
        processing_version=get_settings().processing_version,
    )
    return _queue(db, source, collection)


def _queue(db: Session, source: Source, collection: Collection | None) -> Source:
    db.add(source)
    db.flush()
    if collection is not None:
        collection.sources.append(source)
    job = ProcessingJob(source_id=source.id)
    db.add(job)
    db.flush()
    record_job_event(db, job_id=job.id, stage="ingestion", event_type="queued")
    db.commit()
    db.refresh(source)
    return source
