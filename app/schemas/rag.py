from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    evidence_limit: int = Field(default=8, ge=1, le=30)
    content_type: str | None = Field(default=None, max_length=64)


class AnswerSource(BaseModel):
    reel_id: int
    instagram_url: str
    content_type: str | None
    score: float
    snippet: str


class AnswerResponse(BaseModel):
    question: str
    answer: str
    grounded: bool
    model: str
    sources: list[AnswerSource]
