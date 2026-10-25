from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.evaluation import EvaluationStatus


class EvaluationCaseCreate(BaseModel):
    collection_id: int | None = Field(default=None, gt=0)
    source_id: int | None = Field(default=None, gt=0)
    category: str = Field(default="general", min_length=1, max_length=64)
    ground_truth: dict[str, Any]


class EvaluationPrediction(BaseModel):
    prediction: dict[str, Any]


class EvaluationCaseRead(BaseModel):
    id: int
    collection_id: int | None
    source_id: int | None
    category: str
    ground_truth: dict[str, Any]
    prediction: dict[str, Any] | None
    scores: dict[str, Any] | None
    status: EvaluationStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvaluationSummary(BaseModel):
    total_cases: int
    scored_cases: int
    exact_field_accuracy: float | None
    by_category: dict[str, float]
