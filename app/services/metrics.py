from collections import Counter
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.collection import Collection
from app.models.job import ProcessingJob
from app.models.reel import Reel
from app.models.stage_metric import StageMetric, StageStatus
from app.schemas.system import PipelineMetrics, StageLatency, UserMetrics


def get_user_metrics(db: Session, user_id: str) -> UserMetrics:
    collection_ids = list(db.scalars(select(Collection.id).where(Collection.user_id == user_id)))
    if not collection_ids:
        return UserMetrics(collections=0, reels_by_status={}, jobs_by_status={}, review_queue_size=0)
    reels = list(db.scalars(select(Reel).where(Reel.collection_id.in_(collection_ids))))
    reel_statuses = Counter(reel.status.value for reel in reels)
    reel_ids = [reel.id for reel in reels]
    jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.reel_id.in_(reel_ids)))) if reel_ids else []
    job_statuses = Counter(job.status.value for job in jobs)
    return UserMetrics(
        collections=len(collection_ids),
        reels_by_status=dict(reel_statuses),
        jobs_by_status=dict(job_statuses),
        review_queue_size=reel_statuses.get("needs_review", 0),
    )


STAGE_ORDER = ["transcription", "ocr", "vision", "extraction"]


def get_pipeline_metrics(db: Session, user_id: str) -> PipelineMetrics:
    """Aggregate per-stage latency and failure rates across the user's analysis runs."""
    collection_ids = list(db.scalars(select(Collection.id).where(Collection.user_id == user_id)))
    if not collection_ids:
        return PipelineMetrics(analyzed_reels=0, degraded_reels=0, stages=[])
    reel_ids = list(db.scalars(select(Reel.id).where(Reel.collection_id.in_(collection_ids))))
    if not reel_ids:
        return PipelineMetrics(analyzed_reels=0, degraded_reels=0, stages=[])

    metrics = list(db.scalars(select(StageMetric).where(StageMetric.reel_id.in_(reel_ids))))
    by_stage: dict[str, list[StageMetric]] = {}
    for metric in metrics:
        by_stage.setdefault(metric.stage, []).append(metric)

    stages = []
    for stage in sorted(by_stage, key=lambda name: (STAGE_ORDER.index(name) if name in STAGE_ORDER else 99, name)):
        entries = by_stage[stage]
        statuses = Counter(entry.status for entry in entries)
        # Failure rate counts attempts that ran, so a skipped stage does not look like a failure.
        attempted = len(entries) - statuses.get(StageStatus.skipped, 0)
        failed = statuses.get(StageStatus.failed, 0)
        durations = sorted(entry.duration_ms for entry in entries)
        stages.append(
            StageLatency(
                stage=stage,
                runs=len(entries),
                succeeded=statuses.get(StageStatus.succeeded, 0),
                degraded=statuses.get(StageStatus.degraded, 0),
                skipped=statuses.get(StageStatus.skipped, 0),
                failed=failed,
                failure_rate=round(failed / attempted, 4) if attempted else 0.0,
                average_duration_ms=round(sum(durations) / len(durations), 3) if durations else 0.0,
                p95_duration_ms=round(_percentile(durations, 0.95), 3),
                total_items_processed=sum(entry.items_processed or 0 for entry in entries),
                models=sorted({entry.model for entry in entries if entry.model}),
            )
        )

    degraded_reels = len({entry.reel_id for entry in metrics if entry.status is StageStatus.failed})
    return PipelineMetrics(
        analyzed_reels=len({entry.reel_id for entry in metrics}),
        degraded_reels=degraded_reels,
        stages=stages,
    )


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile: with a handful of runs, interpolation would invent precision."""
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, ceil(fraction * len(sorted_values)) - 1))
    return sorted_values[index]
