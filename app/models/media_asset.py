import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class MediaAssetKind(str, enum.Enum):
    audio = "audio"
    frame = "frame"


class MediaAsset(Base):
    """A generated artifact retained for downstream transcription, OCR, and vision tasks."""

    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("source_id", "kind", "sequence_number", name="uq_media_asset_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    kind: Mapped[MediaAssetKind] = mapped_column(Enum(MediaAssetKind), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["Source"] = relationship(back_populates="media_assets")
