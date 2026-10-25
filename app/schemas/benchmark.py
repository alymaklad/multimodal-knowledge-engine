from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkRunCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    # Free-form so any model set or ablation can be described, e.g.
    # {"vision_model": "claude-opus-5", "uses_ocr": false}
    configuration: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRunRead(BaseModel):
    id: int
    label: str
    notes: str | None
    configuration: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PredictionSubmission(BaseModel):
    case_id: int = Field(gt=0)
    prediction: dict[str, Any]
    latency_ms: float | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class PredictionResult(BaseModel):
    case_id: int
    scores: dict[str, Any]


class CategoryAccuracy(BaseModel):
    category: str
    cases: int
    exact_field_accuracy: float


class BenchmarkRunSummary(BaseModel):
    run_id: int
    label: str
    configuration: dict[str, Any]
    scored_cases: int
    exact_field_accuracy: float | None
    average_latency_ms: float | None
    total_cost_usd: float | None
    by_category: list[CategoryAccuracy]


class AblationDelta(BaseModel):
    """What changed between two runs, and what it bought."""

    baseline_run_id: int
    baseline_label: str
    candidate_run_id: int
    candidate_label: str
    shared_cases: int
    baseline_accuracy: float
    candidate_accuracy: float
    accuracy_delta: float
    improved_cases: int
    regressed_cases: int
    unchanged_cases: int
    changed_settings: dict[str, list[Any]]


class BenchmarkComparison(BaseModel):
    category: str | None
    shared_cases: int
    runs: list[BenchmarkRunSummary]
    delta: AblationDelta | None = None
