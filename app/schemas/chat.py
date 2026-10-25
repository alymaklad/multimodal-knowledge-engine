from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    # Limit the conversation to some of the collection's sources; omit for all of them.
    source_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    # Send null to search the whole collection again.
    source_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)


class ConversationRead(BaseModel):
    id: int
    collection_id: int
    title: str
    source_ids: list[int] | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Citation(BaseModel):
    marker: int
    source_id: int
    title: str
    origin_url: str | None = None
    chunk_key: str
    content_type: str | None = None
    locator: dict[str, Any] = {}
    citation: str | None = None
    snippet: str


class ChatMessageRead(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    status: str
    grounded: bool
    citations: list[Citation]
    retrieval_query: str | None
    model: str | None
    error_message: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationDetail(ConversationRead):
    messages: list[ChatMessageRead]


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ChatTurnResult(BaseModel):
    """The two messages one question produces."""

    question: ChatMessageRead
    answer: ChatMessageRead
