from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.reel import Reel
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.services.vector_store import VectorPoint, VectorStore, get_vector_store

MAX_CHUNK_CHARACTERS = 4000


@dataclass(frozen=True)
class ReelChunk:
    chunk_key: str
    content_type: str | None
    text: str


def build_reel_chunks(reel: Reel) -> list[ReelChunk]:
    """Turn a Reel's evidence into the textual representations that get embedded.

    Each modality is embedded separately so a match can be attributed to the evidence that
    produced it, and a whole-Reel summary chunk carries the cross-modal context.
    """
    chunks: list[ReelChunk] = []
    if reel.transcript and reel.transcript.text.strip():
        chunks.append(ReelChunk("transcript", "transcript", reel.transcript.text.strip()))
    ocr_text = "\n".join(item.text.strip() for item in reel.ocr_results if item.text.strip())
    if ocr_text:
        chunks.append(ReelChunk("ocr", "ocr", ocr_text))
    if reel.visual_analysis and reel.visual_analysis.description.strip():
        chunks.append(ReelChunk("visual", "visual", reel.visual_analysis.description.strip()))
    for item in reel.extracted_content:
        text = (item.text_representation or "").strip()
        if text:
            chunks.append(ReelChunk(f"extracted:{item.content_type}", item.content_type, text))

    summary = "\n".join(
        part
        for part in [
            f"Creator: {reel.creator_username}" if reel.creator_username else "",
            f"Caption: {reel.caption}" if reel.caption else "",
            *(chunk.text for chunk in chunks),
        ]
        if part
    ).strip()
    if summary:
        chunks.append(ReelChunk("summary", "summary", summary))
    return [ReelChunk(chunk.chunk_key, chunk.content_type, chunk.text[:MAX_CHUNK_CHARACTERS]) for chunk in chunks]


def index_reel(
    db: Session,
    reel: Reel,
    *,
    provider: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
) -> int:
    """Replace every stored vector for one Reel. Returns the number of indexed chunks."""
    provider = provider or get_embedding_provider()
    store = store or get_vector_store(db, dimensions=provider.dimensions)
    chunks = build_reel_chunks(reel)
    if not chunks:
        store.delete_reel(reel.id)
        return 0
    vectors = provider.embed([chunk.text for chunk in chunks])
    store.replace_reel(
        [
            VectorPoint(
                reel_id=reel.id,
                collection_id=reel.collection_id,
                chunk_key=chunk.chunk_key,
                content_type=chunk.content_type,
                text=chunk.text,
                vector=vector,
                model=provider.model,
                payload={"creator": reel.creator_username} if reel.creator_username else {},
            )
            for chunk, vector in zip(chunks, vectors)
        ],
        reel_id=reel.id,
    )
    return len(chunks)


def index_collection(db: Session, collection_id: int) -> dict[str, int]:
    """Backfill vectors for a whole collection after a model or prompt upgrade."""
    provider = get_embedding_provider()
    store = get_vector_store(db, dimensions=provider.dimensions)
    reels = db.scalars(
        select(Reel)
        .where(Reel.collection_id == collection_id)
        .options(
            selectinload(Reel.transcript),
            selectinload(Reel.ocr_results),
            selectinload(Reel.visual_analysis),
            selectinload(Reel.extracted_content),
        )
    )
    indexed_reels = 0
    indexed_chunks = 0
    for reel in reels:
        chunk_count = index_reel(db, reel, provider=provider, store=store)
        if chunk_count:
            indexed_reels += 1
        indexed_chunks += chunk_count
    db.commit()
    return {"indexed_reels": indexed_reels, "indexed_chunks": indexed_chunks}
