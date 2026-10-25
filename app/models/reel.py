import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ReelStatus(str, enum.Enum):
    pending = "pending"
    downloading = "downloading"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    needs_review = "needs_review"


class Reel(Base):
    __tablename__ = "reels"
    __table_args__ = (UniqueConstraint("collection_id", "instagram_url", name="uq_reel_collection_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id"), index=True)
    instagram_url: Mapped[str] = mapped_column(String(2048))
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    creator_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    creator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[ReelStatus] = mapped_column(Enum(ReelStatus), default=ReelStatus.pending, index=True)
    processing_version: Mapped[str] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    collection: Mapped["Collection"] = relationship(back_populates="reels")
    processing_job: Mapped["ProcessingJob | None"] = relationship(
        back_populates="reel", cascade="all, delete-orphan", uselist=False
    )
    media_assets: Mapped[list["MediaAsset"]] = relationship(
        back_populates="reel", cascade="all, delete-orphan"
    )
    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="reel", cascade="all, delete-orphan", uselist=False
    )
    ocr_results: Mapped[list["OCRResult"]] = relationship(back_populates="reel", cascade="all, delete-orphan")
    visual_analysis: Mapped["VisualAnalysis | None"] = relationship(
        back_populates="reel", cascade="all, delete-orphan", uselist=False
    )
    extracted_content: Mapped[list["ExtractedContent"]] = relationship(
        back_populates="reel", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["ContentEmbedding"]] = relationship(
        back_populates="reel", cascade="all, delete-orphan"
    )
    provenance: Mapped["ProcessingProvenance | None"] = relationship(
        back_populates="reel", cascade="all, delete-orphan", uselist=False
    )
    stored_objects: Mapped[list["StoredObject"]] = relationship(
        back_populates="reel", cascade="all, delete-orphan"
    )
    stage_metrics: Mapped[list["StageMetric"]] = relationship(
        back_populates="reel", cascade="all, delete-orphan"
    )
