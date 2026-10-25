from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class BenchmarkRun(Base):
    """One configuration evaluated over the case dataset (architecture sections 54 and 55).

    A run is the unit that makes results comparable: without it a stored prediction cannot be
    attributed to the model and ablation that produced it, so two numbers cannot be placed side by
    side. `configuration` holds the model set and the ablation flags (`uses_ocr`, `uses_transcript`,
    `uses_vision`) that distinguish one run from another.
    """

    __tablename__ = "benchmark_runs"
    __table_args__ = (UniqueConstraint("user_id", "label", name="uq_benchmark_run_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    predictions: Mapped[list["BenchmarkPrediction"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BenchmarkPrediction(Base):
    """One configuration's answer for one evaluation case, with its cost and latency."""

    __tablename__ = "benchmark_predictions"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_benchmark_run_case"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("benchmark_runs.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("evaluation_cases.id"), index=True)
    prediction: Mapped[dict[str, Any]] = mapped_column(JSON)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped["BenchmarkRun"] = relationship(back_populates="predictions")
