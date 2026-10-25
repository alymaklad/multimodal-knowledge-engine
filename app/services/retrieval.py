from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.reel import Reel
from app.core.config import get_settings
from app.providers.embeddings import get_embedding_provider
from app.providers.reranking import RerankProvider, get_rerank_provider
from app.schemas.search import SearchResult
from app.services.vector_store import VectorMatch, get_vector_store

RRF_K = 60


@dataclass(frozen=True)
class RetrievedEvidence:
    reel_id: int
    instagram_url: str
    content_type: str | None
    text: str
    score: float


@dataclass(frozen=True)
class ReelFacets:
    """The attributes a result can be filtered and labelled by."""

    instagram_url: str
    creator: str | None
    topics: list[str]


def _reels_with_evidence(db: Session, collection_id: int):
    return db.scalars(
        select(Reel)
        .where(Reel.collection_id == collection_id)
        .options(
            selectinload(Reel.transcript),
            selectinload(Reel.ocr_results),
            selectinload(Reel.visual_analysis),
            selectinload(Reel.extracted_content),
        )
    )


def _reel_topics(reel: Reel) -> list[str]:
    """Topics come from extracted knowledge, so a filter matches what analysis actually found."""
    topics: list[str] = []
    for item in reel.extracted_content:
        topic = item.data.get("topic")
        if isinstance(topic, str) and topic.strip() and topic.strip() not in topics:
            topics.append(topic.strip())
    return topics


def collection_facets(db: Session, collection_id: int) -> dict[int, ReelFacets]:
    return {
        reel.id: ReelFacets(
            instagram_url=reel.instagram_url, creator=reel.creator_username, topics=_reel_topics(reel)
        )
        for reel in _reels_with_evidence(db, collection_id)
    }


def _matches_filters(facets: ReelFacets, creator: str | None, topic: str | None) -> bool:
    if creator is not None:
        # Creators are compared without the leading @ so "@name" and "name" behave the same.
        if (facets.creator or "").lstrip("@").lower() != creator.lstrip("@").lower():
            return False
    if topic is not None and not any(topic.lower() in value.lower() for value in facets.topics):
        return False
    return True


def _decorate(result: SearchResult, facets: ReelFacets) -> SearchResult:
    result.creator = facets.creator
    result.topics = facets.topics
    return result


def search_collection(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    minimum_score: int = 1,
    creator: str | None = None,
    topic: str | None = None,
) -> list[SearchResult]:
    """A transparent lexical baseline, useful on its own for exact technical terms."""
    terms = [term.lower() for term in query.split() if term.strip()]
    results: list[SearchResult] = []
    for reel in _reels_with_evidence(db, collection_id):
        facets = ReelFacets(reel.instagram_url, reel.creator_username, _reel_topics(reel))
        if not _matches_filters(facets, creator, topic):
            continue
        sources = [
            reel.transcript.text if reel.transcript else "",
            *(item.text for item in reel.ocr_results),
            reel.visual_analysis.description if reel.visual_analysis else "",
            *(
                item.text_representation
                for item in reel.extracted_content
                if content_type is None or item.content_type == content_type
            ),
        ]
        combined = "\n".join(sources).lower()
        score = sum(combined.count(term) for term in terms)
        if score >= minimum_score:
            snippets = [source[:300] for source in sources if any(term in source.lower() for term in terms)][:3]
            results.append(
                _decorate(
                    SearchResult(
                        reel_id=reel.id,
                        instagram_url=reel.instagram_url,
                        score=score,
                        snippets=snippets,
                        match_type="lexical",
                    ),
                    facets,
                )
            )
    return sorted(results, key=lambda result: (-result.score, result.reel_id))[:limit]


def semantic_search(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    creator: str | None = None,
    topic: str | None = None,
) -> list[SearchResult]:
    """Dense retrieval over the indexed evidence chunks."""
    facets = collection_facets(db, collection_id)
    eligible = {
        reel_id for reel_id, value in facets.items() if _matches_filters(value, creator, topic)
    }
    # Over-fetch, because filtering after retrieval can otherwise empty a full page of results.
    matches = _vector_matches(
        db,
        collection_id=collection_id,
        query=query,
        limit=limit if not eligible or len(eligible) == len(facets) else limit * 5,
        content_type=content_type,
    )
    best: dict[int, SearchResult] = {}
    for match in matches:
        if match.reel_id not in eligible:
            continue
        existing = best.get(match.reel_id)
        if existing is None:
            best[match.reel_id] = _decorate(
                SearchResult(
                    reel_id=match.reel_id,
                    instagram_url=facets[match.reel_id].instagram_url,
                    score=round(match.score, 6),
                    snippets=[match.text[:300]],
                    match_type="semantic",
                ),
                facets[match.reel_id],
            )
        elif len(existing.snippets) < 3:
            existing.snippets.append(match.text[:300])
    return sorted(best.values(), key=lambda result: (-result.score, result.reel_id))[:limit]


def hybrid_search(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    creator: str | None = None,
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
        creator=creator,
        topic=topic,
    )
    dense = semantic_search(
        db,
        collection_id=collection_id,
        query=query,
        limit=limit * 3,
        content_type=content_type,
        creator=creator,
        topic=topic,
    )

    fused: dict[int, float] = {}
    for ranking in (lexical, dense):
        for rank, result in enumerate(ranking, start=1):
            fused[result.reel_id] = fused.get(result.reel_id, 0.0) + 1.0 / (RRF_K + rank)

    by_id = {result.reel_id: result for result in dense}
    by_id.update({result.reel_id: result for result in lexical})
    lexical_ids = {result.reel_id for result in lexical}
    dense_ids = {result.reel_id for result in dense}
    results = [
        SearchResult(
            reel_id=reel_id,
            instagram_url=by_id[reel_id].instagram_url,
            score=round(score, 6),
            snippets=by_id[reel_id].snippets,
            match_type="hybrid" if reel_id in lexical_ids and reel_id in dense_ids else by_id[reel_id].match_type,
            creator=by_id[reel_id].creator,
            topics=by_id[reel_id].topics,
        )
        for reel_id, score in fused.items()
    ]
    ranked = sorted(results, key=lambda result: (-result.score, result.reel_id))
    return rerank_results(query, ranked, limit=limit, provider=reranker)


def retrieve_evidence(
    db: Session, *, collection_id: int, query: str, limit: int, content_type: str | None = None
) -> list[RetrievedEvidence]:
    """Chunk-level evidence for grounded answering, ordered by dense similarity."""
    urls = _reel_urls(db, collection_id)
    matches = _vector_matches(
        db, collection_id=collection_id, query=query, limit=limit, content_type=content_type
    )
    evidence = [
        RetrievedEvidence(
            reel_id=match.reel_id,
            instagram_url=urls[match.reel_id],
            content_type=match.content_type,
            text=match.text,
            score=round(match.score, 6),
        )
        for match in matches
        if match.reel_id in urls
    ]
    if evidence:
        return evidence
    # Nothing indexed yet: fall back to the lexical baseline so answering still cites real sources.
    return [
        RetrievedEvidence(
            reel_id=result.reel_id,
            instagram_url=result.instagram_url,
            content_type=None,
            text="\n".join(result.snippets),
            score=float(result.score),
        )
        for result in search_collection(
            db, collection_id=collection_id, query=query, limit=limit, content_type=content_type
        )
    ]


def _vector_matches(
    db: Session, *, collection_id: int, query: str, limit: int, content_type: str | None
) -> list[VectorMatch]:
    provider = get_embedding_provider()
    store = get_vector_store(db, dimensions=provider.dimensions)
    return store.search(
        collection_id=collection_id,
        vector=provider.embed_one(query),
        limit=limit,
        content_type=content_type,
    )


def _reel_urls(db: Session, collection_id: int) -> dict[int, str]:
    return {
        reel_id: url
        for reel_id, url in db.execute(
            select(Reel.id, Reel.instagram_url).where(Reel.collection_id == collection_id)
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
        zip(candidates, scores), key=lambda pair: (-pair[1], pair[0].reel_id)
    )
    return [result for result, _ in ordered][:limit]
