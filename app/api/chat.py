"""Conversations with a collection.

`POST /api/conversations/{id}/messages` answers and returns both messages as JSON.
`POST /api/conversations/{id}/messages/stream` answers as Server-Sent Events:

    event: user_message   the stored question
    event: retrieval      {"query": the search actually run, "passages": how many were found}
    event: delta          {"text": the next fragment of the answer}
    event: done           the stored answer, with its citations
    event: error          {"status": "failed" | "refused", "message": ...}; the stream then ends
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import SessionLocal, get_db
from app.models.conversation import ChatMessage, Conversation
from app.providers.factory import get_chat_provider
from app.schemas.chat import (
    ChatMessageCreate,
    ChatMessageRead,
    ChatTurnResult,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    ConversationUpdate,
)
from app.services.authorization import get_owned_collection
from app.services.chat import ConversationBusyError, create_conversation, ensure_not_busy, run_turn
from app.services.retrieval import SourceScopeError, resolve_scope

collection_router = APIRouter(prefix="/api/collections", tags=["chat"])
router = APIRouter(prefix="/api/conversations", tags=["chat"])


def _scope_error(error: SourceScopeError) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"message": "Sources not in this collection", "source_ids": error.source_ids}
    )


def _owned_conversation(db: Session, conversation_id: int, user_id: str) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@collection_router.post(
    "/{collection_id}/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED
)
def start_conversation(
    collection_id: int,
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Conversation:
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    try:
        return create_conversation(db, collection, user_id=user_id, title=payload.title, source_ids=payload.source_ids)
    except SourceScopeError as error:
        raise _scope_error(error) from error


@collection_router.get("/{collection_id}/conversations", response_model=list[ConversationRead])
def list_conversations(
    collection_id: int,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[Conversation]:
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.collection_id == collection_id, Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .offset(offset)
            .limit(min(limit, 100))
        )
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> Conversation:
    return _owned_conversation(db, conversation_id, user_id)


@router.patch("/{conversation_id}", response_model=ConversationRead)
def update_conversation(
    conversation_id: int,
    payload: ConversationUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Conversation:
    conversation = _owned_conversation(db, conversation_id, user_id)
    if payload.title is not None:
        conversation.title = payload.title.strip()
    if "source_ids" in payload.model_fields_set:
        try:
            conversation.source_ids = (
                None if payload.source_ids is None else resolve_scope(db, conversation.collection_id, payload.source_ids)
            )
        except SourceScopeError as error:
            raise _scope_error(error) from error
    db.commit()
    db.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> None:
    db.delete(_owned_conversation(db, conversation_id, user_id))
    db.commit()


def _ready(db: Session, conversation_id: int, user_id: str) -> Conversation:
    conversation = _owned_conversation(db, conversation_id, user_id)
    try:
        ensure_not_busy(db, conversation)
    except ConversationBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return conversation


@router.post("/{conversation_id}/messages", response_model=ChatTurnResult)
def send_message(
    conversation_id: int,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ChatTurnResult:
    """Ask a question and wait for the whole answer."""
    _ready(db, conversation_id, user_id)
    question_id = None
    answer_id = None
    for event in run_turn(db, conversation_id, payload.content.strip(), provider=get_chat_provider()):
        if event.type == "user_message":
            question_id = event.data["id"]
        elif event.type in {"done", "error"}:
            answer_id = event.data.get("id") or event.data.get("message_id")
    return ChatTurnResult(
        question=ChatMessageRead.model_validate(db.get(ChatMessage, question_id)),
        answer=ChatMessageRead.model_validate(db.get(ChatMessage, answer_id)),
    )


@router.post("/{conversation_id}/messages/stream")
def stream_message(
    conversation_id: int,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:
    """Ask a question and receive the answer as it is written, as Server-Sent Events."""
    _ready(db, conversation_id, user_id)
    provider = get_chat_provider()
    question = payload.content.strip()

    def events() -> Iterator[str]:
        # The request's session may be closed before the body finishes streaming, so the stream
        # works in a session of its own.
        with SessionLocal() as stream_db:
            for event in run_turn(stream_db, conversation_id, question, provider=provider):
                yield f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
