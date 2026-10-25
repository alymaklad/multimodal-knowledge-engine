from typing import Literal

from pydantic import BaseModel, Field

SearchMode = Literal["lexical", "semantic", "hybrid"]


class SearchRequest(BaseModel):
    collection_id: int = Field(gt=0)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    content_type: str | None = Field(default=None, max_length=64)
    minimum_score: int = Field(default=1, ge=1)
    mode: SearchMode = "lexical"
    author: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=255)


class SearchResult(BaseModel):
    source_id: int
    title: str
    origin_url: str | None
    score: float
    snippets: list[str]
    match_type: SearchMode = "lexical"
    author: str | None = None
    topics: list[str] = []
