from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.providers.base import AnswerProvider, EvidenceChunk
from app.providers.factory import get_answer_provider
from app.schemas.rag import AnswerResponse, AnswerSource
from app.services.retrieval import retrieve_evidence


def answer_collection_question(
    db: Session,
    *,
    collection_id: int,
    question: str,
    evidence_limit: int | None = None,
    content_type: str | None = None,
    provider: AnswerProvider | None = None,
) -> AnswerResponse:
    """Retrieve, then answer strictly from what was retrieved, returning the cited sources."""
    limit = evidence_limit or get_settings().rag_evidence_limit
    retrieved = retrieve_evidence(db, collection_id=collection_id, query=question, limit=limit, content_type=content_type)
    chunks = [
        EvidenceChunk(
            reel_id=item.reel_id,
            instagram_url=item.instagram_url,
            content_type=item.content_type,
            text=item.text,
            score=item.score,
        )
        for item in retrieved
    ]
    grounded = (provider or get_answer_provider()).answer(question, chunks)
    cited = set(grounded.cited_reel_ids)
    sources = [
        AnswerSource(
            reel_id=chunk.reel_id,
            instagram_url=chunk.instagram_url,
            content_type=chunk.content_type,
            score=chunk.score,
            snippet=chunk.text[:300],
        )
        for chunk in chunks
        if not cited or chunk.reel_id in cited
    ]
    return AnswerResponse(
        question=question,
        answer=grounded.answer,
        grounded=grounded.grounded,
        model=grounded.model,
        sources=sources if grounded.grounded else [],
    )
