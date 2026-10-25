from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.embedding import ContentEmbedding
from app.models.source import Source
from app.services.membership import collection_source_ids
from app.providers.embeddings import cosine_similarity
from app.schemas.embedding import DuplicatePair

SUMMARY_CHUNK_KEY = "summary"


def find_near_duplicates(db: Session, *, collection_id: int, threshold: float | None = None) -> list[DuplicatePair]:
    """Report sources whose whole-content vectors are near-identical.

    Exact re-uploads are already blocked at ingestion by URL and media hash. This catches the
    remaining case: the same content saved from a different post or a re-encoded copy.
    """
    limit = threshold if threshold is not None else get_settings().duplicate_similarity_threshold
    source_ids = collection_source_ids(db, collection_id)
    if not source_ids:
        return []
    rows = list(
        db.scalars(
            select(ContentEmbedding).where(
                ContentEmbedding.source_id.in_(source_ids),
                ContentEmbedding.chunk_key == SUMMARY_CHUNK_KEY,
            )
        )
    )
    titles = dict(db.execute(select(Source.id, Source.title).where(Source.id.in_(source_ids))).all())
    pairs: list[DuplicatePair] = []
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            similarity = cosine_similarity(list(left.vector or []), list(right.vector or []))
            if similarity < limit:
                continue
            first, second = sorted((left.source_id, right.source_id))
            if first not in titles or second not in titles:
                continue
            pairs.append(
                DuplicatePair(
                    source_id=first,
                    title=titles[first],
                    duplicate_source_id=second,
                    duplicate_title=titles[second],
                    similarity=round(similarity, 6),
                )
            )
    return sorted(pairs, key=lambda pair: (-pair.similarity, pair.source_id, pair.duplicate_source_id))
