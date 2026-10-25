from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ExtractionCorrection(Base):
    """User correction kept separately from model output for auditability and evaluation."""

    __tablename__ = "extraction_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    reel_id: Mapped[int] = mapped_column(ForeignKey("reels.id"), index=True)
    content_type: Mapped[str] = mapped_column(String(64), index=True)
    field_name: Mapped[str] = mapped_column(String(128))
    previous_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    corrected_value: Mapped[Any] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
