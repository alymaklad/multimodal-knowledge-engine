"""Retrieval over a collection's indexed chunks.

Lexical and dense retrieval rank the same units, the stored chunks, so their rankings can be fused
chunk by chunk. Lexical scoring is BM25, computed over the chunks of the sources in scope; dense
scoring is the vector store's similarity. Search results group chunks back into sources, while
evidence for answering stays at chunk level so a citation can name a page or a moment.

Retrieval reads only what indexing stored. A source that has not been indexed is invisible to every
mode, which keeps lexical and dense results consistent with each other.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.collection import CollectionSource
from app.models.embedding import ContentEmbedding
from app.models.source import Source
from app.core.config import get_settings
from app.providers.embeddings import get_embedding_provider
from app.providers.reranking import RerankProvider, get_rerank_provider
from app.schemas.search import SearchMode, SearchPassage, SearchResult
from app.services.chunking import SUMMARY_CHUNK_KEY, describe_locator
from app.services.membership import collection_source_ids
from app.services.vector_store import get_vector_store

RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75
PASSAGES_PER_RESULT = 3
_TOKEN = re.compile(r"\w+")
# The summary chunk restates a source's other chunks; it serves whole-source comparison only.
_NOT_RETRIEVED = (SUMMARY_CHUNK_KEY,)


class SourceScopeError(ValueError):
    """Requested sources that are not in the collection being searched."""

    def __init__(self, source_ids: list[int]):
        super().__init__(f"Sources not in this collection: {source_ids}")
        self.source_ids = source_ids


@dataclass(frozen=True)
class RetrievedEvidence:
    source_id: int
    title: str
    origin_url: str | None
    content_type: str | None
    text: str
    score: float
    chunk_key: str = ""
    locator: dict[str, Any] = field(default_factory=dict)

    @property
    def citation(self) -> str | None:
        return describe_locator(self.locator)


@dataclass(frozen=True)
class SourceFacets:
    """The attributes a result can be filtered and labelled by."""

    title: str
    origin_url: str | None
    author: str | None
    topics: list[str]


@dataclass(frozen=True)
class _RankedChunk:
    source_id: int
    chunk_key: str
    content_type: str | None
    text: str
    locator: dict[str, Any]
    score: float

    @property
    def key(self) -> tuple[int, str]:
        return self.source_id, self.chunk_key


def resolve_scope(db: Session, collection_id: int, source_ids: Iterable[int] | None = None) -> list[int]:
    """The collection's sources, or the requested subset of them. Raises for ids outside it."""
    members = collection_source_ids(db, collection_id)
    if source_ids is None:
        return members
    requested = list(dict.fromkeys(source_ids))
    member_set = set(members)
    unknown = [source_id for source_id in requested if source_id not in member_set]
    if unknown:
        raise SourceScopeError(unknown)
    return requested


def collection_facets(db: Session, collection_id: int) -> dict[int, SourceFacets]:
    sources = db.scalars(
        select(Source)
        .join(CollectionSource)
        .where(CollectionSource.collection_id == collection_id)
        .options(selectinload(Source.extracted_content))
    )
    return {
        source.id: SourceFacets(
            title=source.title, origin_url=source.origin_url, author=source.author, topics=_source_topics(source)
        )
        for source in sources
    }


def _source_topics(source: Source) -> list[str]:
    """Topics come from extracted knowledge, so a filter matches what analysis actually found."""
    topics: list[str] = []
    for item in source.extracted_content:
        topic = item.data.get("topic")
        if isinstance(topic, str) and topic.strip() and topic.strip() not in topics:
            topics.append(topic.strip())
    return topics


def _matches_filters(facets: SourceFacets, author: str | None, topic: str | None) -> bool:
    if author is not None:
        # Authors are compared without a leading @ so "@name" and "name" behave the same.
        if (facets.author or "").lstrip("@").lower() != author.lstrip("@").lower():
            return False
    if topic is not None and not any(topic.lower() in value.lower() for value in facets.topics):
        return False
    return True


def _eligible(
    db: Session,
    collection_id: int,
    *,
    source_ids: Iterable[int] | None,
    author: str | None,
    topic: str | None,
) -> dict[int, SourceFacets]:
    scope = set(resolve_scope(db, collection_id, source_ids))
    return {
        source_id: facets
        for source_id, facets in collection_facets(db, collection_id).items()
        if source_id in scope and _matches_filters(facets, author, topic)
    }


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
    source_ids: Iterable[int] | None = None,
) -> list[SearchResult]:
    """BM25 over the chunks of the sources in scope. Exact terms such as model names do well here.

    `minimum_score` is the number of query-term occurrences a source needs across its chunks.
    """
    eligible = _eligible(db, collection_id, source_ids=source_ids, author=author, topic=topic)
    chunks, occurrences = _lexical_chunks(db, eligible.keys(), query, content_type)
    qualified = {source_id for source_id, count in occurrences.items() if count >= minimum_score}
    ranked = [chunk for chunk in chunks if chunk.source_id in qualified]
    return _group(ranked, eligible, "lexical", lambda chunk: round(chunk.score, 6))[:limit]


def semantic_search(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    author: str | None = None,
    topic: str | None = None,
    source_ids: Iterable[int] | None = None,
) -> list[SearchResult]:
    """Dense retrieval over the chunks of the sources in scope."""
    eligible = _eligible(db, collection_id, source_ids=source_ids, author=author, topic=topic)
    # Several chunks of one source can outrank every other source, so fetch enough to fill a page.
    chunks = _dense_chunks(db, eligible.keys(), query, limit * PASSAGES_PER_RESULT * 2, content_type)
    return _group(chunks, eligible, "semantic", lambda chunk: round(chunk.score, 6))[:limit]


def hybrid_search(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    author: str | None = None,
    topic: str | None = None,
    source_ids: Iterable[int] | None = None,
    reranker: RerankProvider | None = None,
) -> list[SearchResult]:
    """Reciprocal rank fusion of the lexical and dense chunk rankings, then reranking by source.

    Rank fusion is used rather than score addition because BM25 scores and cosine similarities are
    not on a comparable scale; only their orderings are meaningful together.
    """
    eligible = _eligible(db, collection_id, source_ids=source_ids, author=author, topic=topic)
    window = limit * PASSAGES_PER_RESULT * 2
    lexical, _ = _lexical_chunks(db, eligible.keys(), query, content_type)
    dense = _dense_chunks(db, eligible.keys(), query, window, content_type)
    fused = _fuse(lexical[:window], dense)
    lexical_sources = {chunk.source_id for chunk in lexical[:window]}
    dense_sources = {chunk.source_id for chunk in dense}
    results = _group(fused, eligible, "hybrid", lambda chunk: round(chunk.score, 6))
    for result in results:
        in_both = result.source_id in lexical_sources and result.source_id in dense_sources
        result.match_type = "hybrid" if in_both else ("lexical" if result.source_id in lexical_sources else "semantic")
    return rerank_results(query, results, limit=limit, provider=reranker)


def retrieve_evidence(
    db: Session,
    *,
    collection_id: int,
    query: str,
    limit: int,
    content_type: str | None = None,
    source_ids: Iterable[int] | None = None,
    reranker: RerankProvider | None = None,
) -> list[RetrievedEvidence]:
    """Chunk-level evidence for grounded answering: hybrid retrieval, then reranking of the chunks."""
    scope = resolve_scope(db, collection_id, source_ids)
    if not scope:
        return []
    labels = _source_labels(db, scope)
    window = max(limit * 3, get_settings().rerank_candidates)
    lexical, _ = _lexical_chunks(db, scope, query, content_type)
    dense = _dense_chunks(db, scope, query, window, content_type)
    candidates = _fuse(lexical[:window], dense)[:window]
    ordered = _rerank(query, candidates, [chunk.text for chunk in candidates], reranker)[:limit]
    return [
        RetrievedEvidence(
            source_id=chunk.source_id,
            title=labels[chunk.source_id][0],
            origin_url=labels[chunk.source_id][1],
            content_type=chunk.content_type,
            text=chunk.text,
            score=round(chunk.score, 6),
            chunk_key=chunk.chunk_key,
            locator=chunk.locator,
        )
        for chunk in ordered
        if chunk.source_id in labels
    ]


def _lexical_chunks(
    db: Session, source_ids: Collection[int], query: str, content_type: str | None
) -> tuple[list[_RankedChunk], Counter[int]]:
    """Chunks containing a query term, ranked by BM25, and each source's total term occurrences."""
    terms = set(_TOKEN.findall(query.lower()))
    if not source_ids or not terms:
        return [], Counter()
    statement = select(
        ContentEmbedding.source_id,
        ContentEmbedding.chunk_key,
        ContentEmbedding.content_type,
        ContentEmbedding.text,
        ContentEmbedding.locator,
    ).where(ContentEmbedding.source_id.in_(list(source_ids)), ContentEmbedding.content_type.notin_(_NOT_RETRIEVED))
    if content_type is not None:
        statement = statement.where(ContentEmbedding.content_type == content_type)
    rows = db.execute(statement).all()
    if not rows:
        return [], Counter()

    tokenized = [Counter(_TOKEN.findall(row.text.lower())) for row in rows]
    average_length = sum(sum(tokens.values()) for tokens in tokenized) / len(rows) or 1.0
    document_frequency = {term: sum(1 for tokens in tokenized if term in tokens) for term in terms}
    chunks: list[_RankedChunk] = []
    occurrences: Counter[int] = Counter()
    for row, tokens in zip(rows, tokenized):
        length = sum(tokens.values())
        score = 0.0
        for term in terms:
            frequency = tokens.get(term, 0)
            if not frequency:
                continue
            occurrences[row.source_id] += frequency
            idf = math.log(1 + (len(rows) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += idf * frequency * (BM25_K1 + 1) / (frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / average_length))
        if score > 0:
            chunks.append(
                _RankedChunk(row.source_id, row.chunk_key, row.content_type, row.text, dict(row.locator or {}), score)
            )
    chunks.sort(key=lambda chunk: (-chunk.score, chunk.source_id, chunk.chunk_key))
    return chunks, occurrences


def _dense_chunks(
    db: Session, source_ids: Collection[int], query: str, limit: int, content_type: str | None
) -> list[_RankedChunk]:
    if not source_ids:
        return []
    provider = get_embedding_provider()
    store = get_vector_store(db, dimensions=provider.dimensions)
    matches = store.search(
        source_ids=set(source_ids),
        vector=provider.embed_one(query),
        limit=limit,
        content_type=content_type,
        exclude_content_types=_NOT_RETRIEVED,
    )
    return [
        _RankedChunk(match.source_id, match.chunk_key, match.content_type, match.text, match.locator, match.score)
        for match in matches
    ]


def _fuse(*rankings: list[_RankedChunk]) -> list[_RankedChunk]:
    scores: dict[tuple[int, str], float] = {}
    chunks: dict[tuple[int, str], _RankedChunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk.key] = scores.get(chunk.key, 0.0) + 1.0 / (RRF_K + rank)
            chunks.setdefault(chunk.key, chunk)
    fused = [
        _RankedChunk(chunk.source_id, chunk.chunk_key, chunk.content_type, chunk.text, chunk.locator, scores[key])
        for key, chunk in chunks.items()
    ]
    return sorted(fused, key=lambda chunk: (-chunk.score, chunk.source_id, chunk.chunk_key))


def _group(
    chunks: list[_RankedChunk], facets: dict[int, SourceFacets], match_type: SearchMode, score_of
) -> list[SearchResult]:
    """One result per source, scored by its best chunk, carrying its best few passages."""
    results: dict[int, SearchResult] = {}
    for chunk in chunks:
        source_facets = facets.get(chunk.source_id)
        if source_facets is None:
            continue
        passage = SearchPassage(
            chunk_key=chunk.chunk_key,
            content_type=chunk.content_type,
            text=chunk.text,
            locator=chunk.locator,
            citation=describe_locator(chunk.locator),
        )
        result = results.get(chunk.source_id)
        if result is None:
            results[chunk.source_id] = SearchResult(
                source_id=chunk.source_id,
                title=source_facets.title,
                origin_url=source_facets.origin_url,
                author=source_facets.author,
                topics=source_facets.topics,
                score=score_of(chunk),
                snippets=[chunk.text[:300]],
                passages=[passage],
                match_type=match_type,
            )
        elif len(result.passages) < PASSAGES_PER_RESULT:
            result.passages.append(passage)
            result.snippets.append(chunk.text[:300])
    return sorted(results.values(), key=lambda result: (-result.score, result.source_id))


def _source_labels(db: Session, source_ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """Title and link for each source, keyed by source id."""
    return {
        source_id: (title, url)
        for source_id, title, url in db.execute(
            select(Source.id, Source.title, Source.origin_url).where(Source.id.in_(source_ids))
        ).all()
    }


def _rerank(query: str, items: list, texts: list[str], provider: RerankProvider | None) -> list:
    """Reorder items by the reranker's scores; a provider returning the wrong number is ignored."""
    if not items:
        return []
    scores = (provider or get_rerank_provider()).score(query, texts)
    if len(scores) != len(items):
        return list(items)
    order = sorted(range(len(items)), key=lambda index: (-scores[index], index))
    return [items[index] for index in order]


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
    candidates = results[: max(limit, get_settings().rerank_candidates)]
    return _rerank(query, candidates, ["\n".join(result.snippets) for result in candidates], provider)[:limit]
