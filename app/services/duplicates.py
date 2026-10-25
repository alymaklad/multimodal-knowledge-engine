from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.embedding import ContentEmbedding
from app.models.reel import Reel
from app.providers.embeddings import cosine_similarity
from app.schemas.embedding import DuplicatePair

SUMMARY_CHUNK_KEY = "summary"


def find_near_duplicates(db: Session, *, collection_id: int, threshold: float | None = None) -> list[DuplicatePair]:
    """Report Reels whose whole-content vectors are near-identical.

    Exact re-uploads are already blocked at ingestion by URL and media hash. This catches the
    remaining case: the same content saved from a different post or a re-encoded copy.
    """
    limit = threshold if threshold is not None else get_settings().duplicate_similarity_threshold
    rows = list(
        db.scalars(
            select(ContentEmbedding).where(
                ContentEmbedding.collection_id == collection_id,
                ContentEmbedding.chunk_key == SUMMARY_CHUNK_KEY,
            )
        )
    )
    urls = {
        reel_id: url
        for reel_id, url in db.execute(select(Reel.id, Reel.instagram_url).where(Reel.collection_id == collection_id)).all()
    }
    pairs: list[DuplicatePair] = []
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            similarity = cosine_similarity(list(left.vector or []), list(right.vector or []))
            if similarity < limit:
                continue
            first, second = sorted((left.reel_id, right.reel_id))
            if first not in urls or second not in urls:
                continue
            pairs.append(
                DuplicatePair(
                    reel_id=first,
                    instagram_url=urls[first],
                    duplicate_reel_id=second,
                    duplicate_instagram_url=urls[second],
                    similarity=round(similarity, 6),
                )
            )
    return sorted(pairs, key=lambda pair: (-pair.similarity, pair.reel_id, pair.duplicate_reel_id))
