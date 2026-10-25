import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class StageStatus(str, enum.Enum):
    succeeded = "succeeded"
    degraded = "degraded"
    skipped = "skipped"
    failed = "failed"


class StageMetric(Base):
    """Per-stage timing and outcome for one analysis run (architecture section 49).

    Recorded per attempt rather than overwritten, so a retry's improvement over the previous run
    stays visible and latency can be aggregated across attempts.
    """

    __tablename__ = "stage_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    reel_id: Mapped[int] = mapped_column(ForeignKey("reels.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id"), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[StageStatus] = mapped_column(Enum(StageStatus), index=True)
    duration_ms: Mapped[float] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    items_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reel: Mapped["Reel"] = relationship(back_populates="stage_metrics")
