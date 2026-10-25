from typing import Literal

from pydantic import BaseModel, Field


class ReviewDecision(BaseModel):
    decision: Literal["accepted", "rejected"]
    note: str | None = Field(default=None, max_length=2_000)
