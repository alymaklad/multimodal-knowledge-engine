import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class EvaluationStatus(str, enum.Enum):
    pending = "pending"
    scored = "scored"


class EvaluationCase(Base):
    """A manually annotated sample used to measure extraction quality over time."""

    __tablename__ = "evaluation_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    collection_id: Mapped[int | None] = mapped_column(ForeignKey("collections.id"), nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="general")
    ground_truth: Mapped[dict[str, Any]] = mapped_column(JSON)
    prediction: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    scores: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[EvaluationStatus] = mapped_column(Enum(EvaluationStatus), default=EvaluationStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
