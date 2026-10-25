from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.collection import CollectionSource
from app.models.source import Source
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.services.vector_store import VectorPoint, VectorStore, get_vector_store

MAX_CHUNK_CHARACTERS = 4000


@dataclass(frozen=True)
class SourceChunk:
    chunk_key: str
    content_type: str | None
    text: str


def build_source_chunks(source: Source) -> list[SourceChunk]:
    """Turn a source's evidence into the textual representations that get embedded.

    Each modality is embedded separately so a match can be attributed to the evidence that
    produced it, and a whole-source summary chunk carries the cross-modal context. Processed
    segments (documents, pages, transcripts, images) are embedded one chunk per segment.
    """
    chunks: list[SourceChunk] = []
    if source.transcript and source.transcript.text.strip():
        chunks.append(SourceChunk("transcript", "transcript", source.transcript.text.strip()))
    ocr_text = "\n".join(item.text.strip() for item in source.ocr_results if item.text.strip())
    if ocr_text:
        chunks.append(SourceChunk("ocr", "ocr", ocr_text))
    if source.visual_analysis and source.visual_analysis.description.strip():
        chunks.append(SourceChunk("visual", "visual", source.visual_analysis.description.strip()))
    for item in source.extracted_content:
        text = (item.text_representation or "").strip()
        if text:
            chunks.append(SourceChunk(f"extracted:{item.content_type}", item.content_type, text))
    for segment in source.segments:
        if segment.text.strip():
            chunks.append(SourceChunk(f"segment:{segment.sequence}", segment.modality, segment.text.strip()))

    summary = "\n".join(
        part
        for part in [
            f"Title: {source.title}" if source.title else "",
            f"Author: {source.author}" if source.author else "",
            f"Description: {source.description}" if source.description else "",
            *(chunk.text for chunk in chunks),
        ]
        if part
    ).strip()
    if summary:
        chunks.append(SourceChunk("summary", "summary", summary))
    return [SourceChunk(chunk.chunk_key, chunk.content_type, chunk.text[:MAX_CHUNK_CHARACTERS]) for chunk in chunks]


def index_source(
    db: Session,
    source: Source,
    *,
    provider: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
) -> int:
    """Replace every stored vector for one source. Returns the number of indexed chunks."""
    provider = provider or get_embedding_provider()
    store = store or get_vector_store(db, dimensions=provider.dimensions)
    chunks = build_source_chunks(source)
    if not chunks:
        store.delete_source(source.id)
        return 0
    vectors = provider.embed([chunk.text for chunk in chunks])
    store.replace_source(
        [
            VectorPoint(
                source_id=source.id,
                chunk_key=chunk.chunk_key,
                content_type=chunk.content_type,
                text=chunk.text,
                vector=vector,
                model=provider.model,
                payload={"author": source.author} if source.author else {},
            )
            for chunk, vector in zip(chunks, vectors)
        ],
        source_id=source.id,
    )
    return len(chunks)


def index_collection(db: Session, collection_id: int) -> dict[str, int]:
    """Backfill vectors for a collection's sources after a model or prompt upgrade.

    Vectors belong to sources, so this also refreshes them for every other collection sharing one.
    """
    provider = get_embedding_provider()
    store = get_vector_store(db, dimensions=provider.dimensions)
    sources = db.scalars(
        select(Source)
        .join(CollectionSource)
        .where(CollectionSource.collection_id == collection_id)
        .options(
            selectinload(Source.transcript),
            selectinload(Source.ocr_results),
            selectinload(Source.visual_analysis),
            selectinload(Source.extracted_content),
            selectinload(Source.segments),
        )
    )
    indexed_sources = 0
    indexed_chunks = 0
    for source in sources:
        chunk_count = index_source(db, source, provider=provider, store=store)
        if chunk_count:
            indexed_sources += 1
        indexed_chunks += chunk_count
    db.commit()
    return {"indexed_sources": indexed_sources, "indexed_chunks": indexed_chunks}
