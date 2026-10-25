from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ProcessingProvenance(Base):
    """Which models and prompt versions produced a Reel's knowledge (architecture section 41).

    Without this, a stored extraction cannot be attributed to the configuration that produced it,
    which makes evaluation results and reprocessing decisions unreproducible.
    """

    __tablename__ = "processing_provenance"

    id: Mapped[int] = mapped_column(primary_key=True)
    reel_id: Mapped[int] = mapped_column(ForeignKey("reels.id"), unique=True, index=True)
    processing_version: Mapped[str] = mapped_column(String(64))
    models: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    reel: Mapped["Reel"] = relationship(back_populates="provenance")
