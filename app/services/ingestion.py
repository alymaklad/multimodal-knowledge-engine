from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.collection import Collection
from app.models.job import ProcessingJob
from app.models.source import Source, SourceKind, SourceStatus
from app.services.jobs import record_job_event
from app.services.links import normalize_link
from app.services.storage import SavedUpload


class DuplicateSourceError(Exception):
    def __init__(self, source: Source):
        self.source = source


def create_url_source(
    db: Session, *, user_id: str, url: str, title: str | None = None, collection: Collection | None = None
) -> Source:
    """Record a link in the library. Duplicates are detected per owner on the normalised link.

    Nothing is fetched here: the request must stay fast, and fetching someone else's server belongs
    in the worker with its timeouts and network checks. Raises `InvalidLinkError` for a link that
    cannot be added.
    """
    link = normalize_link(url)
    existing = db.scalar(select(Source).where(Source.user_id == user_id, Source.origin_url == link.url))
    if existing:
        raise DuplicateSourceError(existing)
    source = Source(
        user_id=user_id,
        kind=link.kind,
        # Until the fetch finds the page title, the link itself is the most useful label.
        title=(title or "").strip() or link.url,
        origin_url=link.url,
        details=link.details,
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
    title: str | None = None,
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
        title=title or (Path(original_filename).stem if original_filename else Path(upload.path).name),
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
