from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    evidence_limit: int = Field(default=8, ge=1, le=30)
    content_type: str | None = Field(default=None, max_length=64)
    # Ask only some of the collection's sources. Every id must belong to the collection.
    source_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)


class AnswerSource(BaseModel):
    source_id: int
    title: str
    origin_url: str | None
    content_type: str | None
    score: float
    snippet: str
    chunk_key: str = ""
    locator: dict[str, Any] = {}
    citation: str | None = None


class AnswerResponse(BaseModel):
    question: str
    answer: str
    grounded: bool
    model: str
    sources: list[AnswerSource]
