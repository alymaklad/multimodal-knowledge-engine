from pydantic import BaseModel


class UserMetrics(BaseModel):
    collections: int
    reels_by_status: dict[str, int]
    jobs_by_status: dict[str, int]
    review_queue_size: int


class StageLatency(BaseModel):
    stage: str
    runs: int
    succeeded: int
    degraded: int
    skipped: int
    failed: int
    failure_rate: float
    average_duration_ms: float
    p95_duration_ms: float
    total_items_processed: int
    models: list[str]


class PipelineMetrics(BaseModel):
    """Per-stage latency and failure rates for the current user's Reels."""

    analyzed_reels: int
    degraded_reels: int
    stages: list[StageLatency]
