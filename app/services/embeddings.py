from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.collection import CollectionSource
from app.models.source import Source
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.services.chunking import build_chunks
from app.services.vector_store import VectorPoint, VectorStore, get_vector_store


def index_source(
    db: Session,
    source: Source,
    *,
    provider: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
) -> int:
    """Replace every stored chunk and vector for one source. Returns the number of indexed chunks.

    The stored rows are also what lexical search reads, so a source is searchable by either method
    exactly when it has been indexed.
    """
    provider = provider or get_embedding_provider()
    store = store or get_vector_store(db, dimensions=provider.dimensions)
    chunks = build_chunks(source)
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
                locator=chunk.locator,
                position=chunk.position,
                payload={"author": source.author} if source.author else {},
            )
            for chunk, vector in zip(chunks, vectors)
        ],
        source_id=source.id,
    )
    return len(chunks)


def index_collection(db: Session, collection_id: int) -> dict[str, int]:
    """Backfill vectors for a collection's sources after a model or chunking change.

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
