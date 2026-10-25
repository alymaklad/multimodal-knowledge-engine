import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class SourceStatus(str, enum.Enum):
    pending = "pending"
    downloading = "downloading"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    needs_review = "needs_review"


class SourceKind(str, enum.Enum):
    """What a source is, which decides the processor that turns it into evidence."""

    document = "document"
    web = "web"
    youtube = "youtube"
    image = "image"
    audio = "audio"
    video = "video"
    text = "text"


class Source(Base):
    """One item in a user's library.

    Sources belong to their owner, not to a collection. A collection is a selection over the library,
    so the same source can sit in several collections and is still processed and embedded once.
    """

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", name="uq_source_user_hash"),
        UniqueConstraint("user_id", "origin_url", name="uq_source_user_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), default="local-user", index=True)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind), index=True)
    title: Mapped[str] = mapped_column(String(500))
    origin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kind-specific facts that do not deserve a column, such as a document's page count.
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration: Mapped[float | None] = mapped_column(nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus), default=SourceStatus.pending, index=True)
    processing_version: Mapped[str] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    collections: Mapped[list["Collection"]] = relationship(
        secondary="collection_sources", back_populates="sources"
    )
    processing_job: Mapped["ProcessingJob | None"] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    media_assets: Mapped[list["MediaAsset"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    ocr_results: Mapped[list["OCRResult"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    visual_analysis: Mapped["VisualAnalysis | None"] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    extracted_content: Mapped[list["ExtractedContent"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["ContentEmbedding"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    provenance: Mapped["ProcessingProvenance | None"] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    stored_objects: Mapped[list["StoredObject"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    stage_metrics: Mapped[list["StageMetric"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    segments: Mapped[list["SourceSegment"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", order_by="SourceSegment.sequence"
    )
