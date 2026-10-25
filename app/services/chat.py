"""The collection chatbot: conversations, history-aware retrieval, streamed answers, citations.

A turn runs in five steps, each visible to a streaming client as an event:

1. the question is stored (`user_message`);
2. a follow-up is rewritten into a standalone query using the conversation so far, and the
   conversation's sources are searched with it (`retrieval`);
3. the answer streams from the chat model (`delta`), citing passages inline as [1], [2], ...;
4. the markers are resolved to the passages they name - each with its source, page or timestamp,
   and snippet - and markers that point at nothing are removed from the text;
5. the finished answer is stored with its citations (`done`), or the failure is (`error`).

Every outcome is persisted, including a client that disconnects mid-answer, so a conversation
never shows a message that silently vanished.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.collection import Collection
from app.models.conversation import ChatMessage, Conversation
from app.providers.answering import NO_EVIDENCE_ANSWER
from app.providers.base import ChatProvider, ChatProviderError, ChatRefusedError, ChatTurn, EvidenceChunk
from app.services.membership import collection_source_ids
from app.services.retrieval import RetrievedEvidence, resolve_scope, retrieve_evidence

logger = logging.getLogger(__name__)

TITLE_LENGTH = 60
# A message still "streaming" after this long belongs to a worker that died; it no longer blocks.
STALE_STREAM = timedelta(minutes=10)
REFUSED_ANSWER = "The model declined to answer this question, so no answer was kept."
_MARKER_GROUP = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


class ConversationBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


def create_conversation(
    db: Session, collection: Collection, *, user_id: str, title: str | None = None, source_ids: list[int] | None = None
) -> Conversation:
    """Raises `SourceScopeError` when `source_ids` names a source outside the collection."""
    if source_ids is not None:
        source_ids = resolve_scope(db, collection.id, source_ids)
    conversation = Conversation(
        collection_id=collection.id, user_id=user_id, title=(title or "").strip() or "New conversation", source_ids=source_ids
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def conversation_scope(db: Session, conversation: Conversation) -> list[int] | None:
    """The sources a conversation searches. Sources removed from the collection since are skipped."""
    if conversation.source_ids is None:
        return None
    members = set(collection_source_ids(db, conversation.collection_id))
    return [source_id for source_id in conversation.source_ids if source_id in members]


def ensure_not_busy(db: Session, conversation: Conversation) -> None:
    """One answer at a time per conversation, or two turns would interleave in its history."""
    cutoff = datetime.now(timezone.utc) - STALE_STREAM
    for message in db.scalars(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation.id, ChatMessage.status == "streaming")
    ):
        created = message.created_at if message.created_at.tzinfo else message.created_at.replace(tzinfo=timezone.utc)
        if created > cutoff:
            raise ConversationBusyError("This conversation is still answering the previous question")


def run_turn(db: Session, conversation_id: int, question: str, *, provider: ChatProvider) -> Iterator[ChatEvent]:
    """Answer one question in a conversation, yielding events as the work progresses."""
    settings = get_settings()
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise LookupError(f"Conversation {conversation_id} does not exist")

    history = _history(db, conversation, settings.chat_history_messages)
    user_message = ChatMessage(conversation_id=conversation.id, role="user", content=question, status="complete")
    db.add(user_message)
    if conversation.title == "New conversation":
        conversation.title = _title_from(question)
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    yield ChatEvent("user_message", message_payload(user_message))

    query = question
    if history:
        try:
            query = provider.rewrite_query(history, question) or question
        except ChatProviderError as error:
            # The original wording still retrieves something; a rewrite is an improvement, not a need.
            logger.warning("Query rewrite failed; searching with the question as asked: %s", error)

    retrieved = retrieve_evidence(
        db,
        collection_id=conversation.collection_id,
        query=query,
        limit=settings.rag_evidence_limit,
        source_ids=conversation_scope(db, conversation),
    )
    evidence = [_evidence_chunk(item) for item in retrieved]
    yield ChatEvent("retrieval", {"query": query, "passages": len(evidence)})

    assistant = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        status="streaming",
        content="",
        retrieval_query=query,
        model=provider.model,
    )
    db.add(assistant)
    db.commit()

    parts: list[str] = []
    try:
        if not evidence:
            parts.append(NO_EVIDENCE_ANSWER)
            yield ChatEvent("delta", {"text": NO_EVIDENCE_ANSWER})
        else:
            for text in provider.stream_answer(history, question, evidence):
                parts.append(text)
                yield ChatEvent("delta", {"text": text})
    except ChatRefusedError as error:
        _finish(db, assistant, status="refused", content=REFUSED_ANSWER, error=str(error))
        yield ChatEvent("error", {"status": "refused", "message": str(error), "message_id": assistant.id})
        return
    except ChatProviderError as error:
        _finish(db, assistant, status="failed", content="".join(parts), error=str(error))
        yield ChatEvent("error", {"status": "failed", "message": str(error), "message_id": assistant.id})
        return
    except GeneratorExit:
        # The client disconnected. Keep what was said so far, marked as cut short, then stop.
        _finish(db, assistant, status="interrupted", content="".join(parts), error="The client disconnected")
        raise
    except Exception as error:
        logger.exception("Chat turn failed", extra={"conversation_id": conversation.id})
        _finish(db, assistant, status="failed", content="".join(parts), error=f"{type(error).__name__}: {error}")
        yield ChatEvent("error", {"status": "failed", "message": "The answer failed unexpectedly", "message_id": assistant.id})
        return

    text, citations = resolve_citations("".join(parts), evidence, retrieved)
    assistant.citations = citations
    assistant.grounded = bool(citations)
    _finish(db, assistant, status="complete", content=text)
    yield ChatEvent("done", message_payload(assistant))


def resolve_citations(
    text: str, evidence: list[EvidenceChunk], retrieved: list[RetrievedEvidence]
) -> tuple[str, list[dict[str, Any]]]:
    """Map [n] markers to the passages they name, in order of first use; drop markers that name none.

    A marker outside the passage list was invented by the model. Leaving it in the text would show a
    reader a citation that leads nowhere, so it is removed rather than kept.
    """
    used: list[int] = []

    def replace(match: re.Match[str]) -> str:
        numbers = [int(number) for number in re.split(r"\s*,\s*", match.group(1))]
        valid = [number for number in numbers if 1 <= number <= len(evidence)]
        for number in valid:
            if number not in used:
                used.append(number)
        return "".join(f"[{number}]" for number in valid)

    cleaned = _MARKER_GROUP.sub(replace, text)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    citations = []
    for number in used:
        chunk, item = evidence[number - 1], retrieved[number - 1]
        citations.append(
            {
                "marker": number,
                "source_id": chunk.source_id,
                "title": chunk.title,
                "origin_url": chunk.origin_url,
                "chunk_key": chunk.chunk_key,
                "content_type": chunk.content_type,
                "locator": item.locator,
                "citation": chunk.citation,
                "snippet": chunk.text[:300],
            }
        )
    return cleaned.strip(), citations


def message_payload(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "grounded": message.grounded,
        "citations": message.citations or [],
        "retrieval_query": message.retrieval_query,
        "model": message.model,
        "error_message": message.error_message,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _history(db: Session, conversation: Conversation, limit: int) -> list[ChatTurn]:
    """The last `limit` messages worth showing the model: questions and completed answers only."""
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.id.desc())
            .limit(limit * 2)
        )
    )
    usable = [row for row in reversed(rows) if row.role == "user" or row.status == "complete"]
    turns = [ChatTurn(role=row.role, content=row.content) for row in usable][-limit:] if limit > 0 else []
    # The history must open with a question for every chat API.
    while turns and turns[0].role != "user":
        turns.pop(0)
    return turns


def _evidence_chunk(item: RetrievedEvidence) -> EvidenceChunk:
    return EvidenceChunk(
        source_id=item.source_id,
        title=item.title,
        origin_url=item.origin_url,
        content_type=item.content_type,
        text=item.text,
        score=item.score,
        chunk_key=item.chunk_key,
        citation=item.citation,
    )


def _finish(db: Session, message: ChatMessage, *, status: str, content: str, error: str | None = None) -> None:
    message.status = status
    message.content = content
    message.error_message = error
    db.commit()
    db.refresh(message)


def _title_from(question: str) -> str:
    words = " ".join(question.split())
    return words if len(words) <= TITLE_LENGTH else words[: TITLE_LENGTH - 1].rsplit(" ", 1)[0] + "…"
