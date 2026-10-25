"""Which library sources a collection contains."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.collection import Collection, CollectionSource
from app.models.source import Source


class UnknownSourcesError(Exception):
    def __init__(self, source_ids: list[int]):
        self.source_ids = source_ids


def collection_source_ids(db: Session, collection_id: int) -> list[int]:
    return list(
        db.scalars(select(CollectionSource.source_id).where(CollectionSource.collection_id == collection_id))
    )


def add_sources_to_collection(db: Session, collection: Collection, source_ids: Iterable[int]) -> list[int]:
    """Add owned sources to a collection. Already-present ids are ignored, so the call is idempotent.

    Every id is checked against the collection owner's library first, so nobody can pull another
    user's source into their collection by guessing its id.
    """
    requested = list(dict.fromkeys(source_ids))
    owned = set(
        db.scalars(select(Source.id).where(Source.id.in_(requested), Source.user_id == collection.user_id))
    )
    unknown = [source_id for source_id in requested if source_id not in owned]
    if unknown:
        raise UnknownSourcesError(unknown)
    present = set(collection_source_ids(db, collection.id))
    added = [source_id for source_id in requested if source_id not in present]
    for source_id in added:
        db.add(CollectionSource(collection_id=collection.id, source_id=source_id))
    db.commit()
    return added


def remove_source_from_collection(db: Session, collection: Collection, source_id: int) -> bool:
    """Drop a source from one collection. The source itself stays in the library."""
    result = db.execute(
        delete(CollectionSource).where(
            CollectionSource.collection_id == collection.id, CollectionSource.source_id == source_id
        )
    )
    db.commit()
    return result.rowcount > 0
