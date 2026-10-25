"""Publish a Reel's media to durable object storage after preparation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.reel import Reel
from app.models.stored_object import StoredObject, StoredObjectKind
from app.services.object_store import ObjectStore, get_object_store, object_key


@dataclass(frozen=True)
class PublishResult:
    published: int
    skipped: int
    backend: str


def publish_reel_media(db: Session, reel: Reel, *, store: ObjectStore | None = None) -> PublishResult:
    """Copy the source video, audio, and frames into the object store under the section 8 layout.

    Republishing replaces the Reel's prior object records, so a reprocessed Reel never accumulates
    entries pointing at frames a newer run has already overwritten.
    """
    store = store or get_object_store()
    user_id = reel.collection.user_id
    assets = list(db.scalars(select(MediaAsset).where(MediaAsset.reel_id == reel.id)))

    planned: list[tuple[StoredObjectKind, Path, str, int | None]] = []
    if reel.media_path:
        source = Path(reel.media_path)
        planned.append(
            (StoredObjectKind.video, source, object_key(user_id, reel.id, f"video{source.suffix or '.mp4'}"), None)
        )
    for asset in assets:
        path = Path(asset.path)
        if asset.kind == MediaAssetKind.audio:
            planned.append((StoredObjectKind.audio, path, object_key(user_id, reel.id, f"audio{path.suffix or '.wav'}"), None))
        elif asset.kind == MediaAssetKind.frame:
            planned.append(
                (
                    StoredObjectKind.frame,
                    path,
                    object_key(user_id, reel.id, f"frames/{path.name}"),
                    asset.sequence_number,
                )
            )

    db.execute(delete(StoredObject).where(StoredObject.reel_id == reel.id))
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
                reel_id=reel.id,
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
