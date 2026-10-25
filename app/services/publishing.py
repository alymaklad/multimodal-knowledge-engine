"""Publish a source's media to durable object storage after preparation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.source import Source
from app.models.stored_object import StoredObject, StoredObjectKind
from app.services.object_store import ObjectStore, get_object_store, object_key


@dataclass(frozen=True)
class PublishResult:
    published: int
    skipped: int
    backend: str


def publish_source_media(db: Session, source: Source, *, store: ObjectStore | None = None) -> PublishResult:
    """Copy the source video, audio, and frames into the object store under the section 8 layout.

    Republishing replaces the source's prior object records, so a reprocessed source never accumulates
    entries pointing at frames a newer run has already overwritten.
    """
    store = store or get_object_store()
    user_id = source.user_id
    assets = list(db.scalars(select(MediaAsset).where(MediaAsset.source_id == source.id)))

    planned: list[tuple[StoredObjectKind, Path, str, int | None]] = []
    if source.media_path:
        media_file = Path(source.media_path)
        planned.append(
            (StoredObjectKind.video, media_file, object_key(user_id, source.id, f"video{media_file.suffix or '.mp4'}"), None)
        )
    for asset in assets:
        path = Path(asset.path)
        if asset.kind == MediaAssetKind.audio:
            planned.append((StoredObjectKind.audio, path, object_key(user_id, source.id, f"audio{path.suffix or '.wav'}"), None))
        elif asset.kind == MediaAssetKind.frame:
            planned.append(
                (
                    StoredObjectKind.frame,
                    path,
                    object_key(user_id, source.id, f"frames/{path.name}"),
                    asset.sequence_number,
                )
            )

    db.execute(delete(StoredObject).where(StoredObject.source_id == source.id))
    published = 0
    skipped = 0
    for kind, local_path, key, sequence_number in planned:
        # A working file can be absent when a stage failed part way; that is not a publish failure.
        if not local_path.is_file():
            skipped += 1
            continue
        info = store.put(local_path, key)
        db.add(
            StoredObject(
                source_id=source.id,
                kind=kind,
                storage_key=info.key,
                backend=info.backend,
                content_type=info.content_type,
                size_bytes=info.size_bytes,
                sequence_number=sequence_number,
            )
        )
        published += 1
    return PublishResult(published=published, skipped=skipped, backend=store.backend)
