from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.models.source import SourceKind, SourceStatus


class SourceUrlCreate(BaseModel):
    url: AnyHttpUrl
    title: str | None = Field(default=None, max_length=500)
    # Convenience for the common "add a source straight into this collection" flow.
    collection_id: int | None = Field(default=None, gt=0)


class SourceTextCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    # The upper bound is MAX_TEXT_CHARACTERS, checked by the endpoint so it stays configurable.
    text: str = Field(min_length=1)
    collection_id: int | None = Field(default=None, gt=0)


class SourceRead(BaseModel):
    id: int
    user_id: str
    kind: SourceKind
    title: str
    origin_url: str | None
    original_filename: str | None
    mime_type: str | None
    author: str | None
    description: str | None
    # A short overview written at indexing time; for reading, never cited as evidence.
    summary: str | None = None
    details: dict[str, Any]
    duration: float | None
    media_path: str | None
    status: SourceStatus
    processing_version: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SourceIngestionResponse(BaseModel):
    source_id: int
    status: SourceStatus
    message: str


class SourceMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
