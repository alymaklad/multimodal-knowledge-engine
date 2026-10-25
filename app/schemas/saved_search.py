from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.search import SearchMode


class SavedSearchCreate(BaseModel):
    collection_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=500)
    content_type: str | None = Field(default=None, max_length=64)
    minimum_score: int = Field(default=1, ge=1)
    mode: SearchMode = "lexical"
    creator: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=255)


class SavedSearchRead(SavedSearchCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
