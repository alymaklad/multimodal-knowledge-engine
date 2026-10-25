from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.collection import CollectionSource
from app.models.source import Source
from app.core.config import get_settings
from app.providers.embeddings import get_embedding_provider
from app.providers.reranking import RerankProvider, get_rerank_provider
from app.schemas.search import SearchResult
from app.services.membership import collection_source_ids
from app.services.vector_store import VectorMatch, get_vector_store

RRF_K = 60


@dataclass(frozen=True)
class RetrievedEvidence:
    source_id: int
    title: str
    origin_url: str | None
    content_type: str | None
    text: str
    score: float


@dataclass(frozen=True)
class SourceFacets:
    """The attributes a result can be filtered and labelled by."""

    title: str
    origin_url: str | None
    author: str | None
    topics: list[str]


def _sources_with_evidence(db: Session, collection_id: int):
    return db.scalars(
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


def _source_topics(source: Source) -> list[str]:
    """Topics come from extracted knowledge, so a filter matches what analysis actually found."""
    topics: list[str] = []
    for item in source.extracted_content:
        topic = item.data.get("topic")
        if isinstance(topic, str) and topic.strip() and topic.strip() not in topics:
            topics.append(topic.strip())
    return topics


def _facets_for(source: Source) -> SourceFacets:
    return SourceFacets(
        title=source.title, origin_url=source.origin_url, author=source.author, topics=_source_topics(source)
    )


def collection_facets(db: Session, collection_id: int) -> dict[int, SourceFacets]:
    return {source.id: _facets_for(source) for source in _sources_with_evidence(db, collection_id)}


def _matches_filters(facets: SourceFacets, author: str | None, topic: str | None) -> bool:
    if author is not None:
        # Authors are compared without a leading @ so "@name" and "name" behave the same.
        if (facets.author or "").lstrip("@").lower() != author.lstrip("@").lower():
            return False
    if topic is not None and not any(topic.lower() in value.lower() for value in facets.topics):
        return False
    return True


def _result(source_id: int, facets: SourceFacets, **fields) -> SearchResult:
    return SearchResult(
        source_id=source_id,
        title=facets.title,
        origin_url=facets.origin_url,
        author=facets.author,
        topics=facets.topics,
        **fields,
    )


def search_collection(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    minimum_score: int = 1,
    author: str | None = None,
    topic: str | None = None,
) -> list[SearchResult]:
    """A transparent lexical baseline, useful on its own for exact technical terms."""
    terms = [term.lower() for term in query.split() if term.strip()]
    results: list[SearchResult] = []
    for source in _sources_with_evidence(db, collection_id):
        facets = _facets_for(source)
        if not _matches_filters(facets, author, topic):
            continue
        parts = [
            source.transcript.text if source.transcript else "",
            *(item.text for item in source.ocr_results),
            source.visual_analysis.description if source.visual_analysis else "",
            *(
                item.text_representation
                for item in source.extracted_content
                if content_type is None or item.content_type == content_type
            ),
            *(
                segment.text
                for segment in source.segments
                if content_type is None or segment.modality == content_type
            ),
        ]
        combined = "\n".join(parts).lower()
        score = sum(combined.count(term) for term in terms)
        if score >= minimum_score:
            snippets = [part[:300] for part in parts if any(term in part.lower() for term in terms)][:3]
            results.append(_result(source.id, facets, score=score, snippets=snippets, match_type="lexical"))
    return sorted(results, key=lambda result: (-result.score, result.source_id))[:limit]


def semantic_search(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    author: str | None = None,
    topic: str | None = None,
) -> list[SearchResult]:
    """Dense retrieval over the indexed evidence chunks of the collection's sources."""
    facets = collection_facets(db, collection_id)
    eligible = {
        source_id for source_id, value in facets.items() if _matches_filters(value, author, topic)
    }
    # Searching only the eligible sources keeps a filter from emptying a full page of results.
    matches = _vector_matches(
        db, source_ids=eligible, query=query, limit=limit * 5, content_type=content_type
    )
    best: dict[int, SearchResult] = {}
    for match in matches:
        existing = best.get(match.source_id)
        if existing is None:
            best[match.source_id] = _result(
                match.source_id,
                facets[match.source_id],
                score=round(match.score, 6),
                snippets=[match.text[:300]],
                match_type="semantic",
            )
        elif len(existing.snippets) < 3:
            existing.snippets.append(match.text[:300])
    return sorted(best.values(), key=lambda result: (-result.score, result.source_id))[:limit]


def hybrid_search(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    author: str | None = None,
    topic: str | None = None,
    reranker: RerankProvider | None = None,
) -> list[SearchResult]:
    """Reciprocal rank fusion of the lexical and dense rankings, then reranking.

    Rank fusion is used rather than score addition because lexical counts and cosine similarities
    are not on a comparable scale; only their orderings are meaningful together.
    """
    lexical = search_collection(
        db,
        collection_id=collection_id,
        query=query,
        limit=limit * 3,
        content_type=content_type,
        minimum_score=1,
        author=author,
        topic=topic,
    )
    dense = semantic_search(
        db,
        collection_id=collection_id,
        query=query,
        limit=limit * 3,
        content_type=content_type,
        author=author,
        topic=topic,
    )

    fused: dict[int, float] = {}
    for ranking in (lexical, dense):
        for rank, result in enumerate(ranking, start=1):
            fused[result.source_id] = fused.get(result.source_id, 0.0) + 1.0 / (RRF_K + rank)

    by_id = {result.source_id: result for result in dense}
    by_id.update({result.source_id: result for result in lexical})
    lexical_ids = {result.source_id for result in lexical}
    dense_ids = {result.source_id for result in dense}
    results = [
        by_id[source_id].model_copy(
            update={
                "score": round(score, 6),
                "match_type": "hybrid"
                if source_id in lexical_ids and source_id in dense_ids
                else by_id[source_id].match_type,
            }
        )
        for source_id, score in fused.items()
    ]
    ranked = sorted(results, key=lambda result: (-result.score, result.source_id))
    return rerank_results(query, ranked, limit=limit, provider=reranker)


def retrieve_evidence(
    db: Session, *, collection_id: int, query: str, limit: int, content_type: str | None = None
) -> list[RetrievedEvidence]:
    """Chunk-level evidence for grounded answering, ordered by dense similarity."""
    labels = _source_labels(db, collection_id)
    matches = _vector_matches(
        db, source_ids=labels.keys(), query=query, limit=limit, content_type=content_type
    )
    evidence = [
        RetrievedEvidence(
            source_id=match.source_id,
            title=labels[match.source_id][0],
            origin_url=labels[match.source_id][1],
            content_type=match.content_type,
            text=match.text,
            score=round(match.score, 6),
        )
        for match in matches
        if match.source_id in labels
    ]
    if evidence:
        return evidence
    # Nothing indexed yet: fall back to the lexical baseline so answering still cites real sources.
    return [
        RetrievedEvidence(
            source_id=result.source_id,
            title=result.title,
            origin_url=result.origin_url,
            content_type=None,
            text="\n".join(result.snippets),
            score=float(result.score),
        )
        for result in search_collection(
            db, collection_id=collection_id, query=query, limit=limit, content_type=content_type
        )
    ]


def _vector_matches(
    db: Session, *, source_ids, query: str, limit: int, content_type: str | None
) -> list[VectorMatch]:
    source_ids = set(source_ids)
    if not source_ids:
        return []
    provider = get_embedding_provider()
    store = get_vector_store(db, dimensions=provider.dimensions)
    return store.search(
        source_ids=source_ids,
        vector=provider.embed_one(query),
        limit=limit,
        content_type=content_type,
    )


def _source_labels(db: Session, collection_id: int) -> dict[int, tuple[str, str | None]]:
    """Title and link for each source in the collection, keyed by source id."""
    source_ids = collection_source_ids(db, collection_id)
    if not source_ids:
        return {}
    return {
        source_id: (title, url)
        for source_id, title, url in db.execute(
            select(Source.id, Source.title, Source.origin_url).where(Source.id.in_(source_ids))
        ).all()
    }


def rerank_results(
    query: str,
    results: list[SearchResult],
    *,
    limit: int,
    provider: RerankProvider | None = None,
) -> list[SearchResult]:
    """Reorder the fused candidates, then cut to the requested page size.

    Reranking is applied to a wider candidate window than the caller asked for, because its whole
    value is promoting something the earlier stages ranked just below the cut.
    """
    provider = provider or get_rerank_provider()
    candidates = results[: max(limit, get_settings().rerank_candidates)]
    if not candidates:
        return []
    scores = provider.score(query, ["\n".join(result.snippets) for result in candidates])
    if len(scores) != len(candidates):
        # A provider that returns a mismatched vector is ignored rather than trusted.
        return candidates[:limit]
    ordered = sorted(
        zip(candidates, scores), key=lambda pair: (-pair[1], pair[0].source_id)
    )
    return [result for result, _ in ordered][:limit]
