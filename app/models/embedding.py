from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ContentEmbedding(Base):
    """One embedded evidence chunk of a source, kept alongside the text it was generated from."""

    __tablename__ = "content_embeddings"
    __table_args__ = (UniqueConstraint("source_id", "chunk_key", name="uq_embedding_source_chunk"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    chunk_key: Mapped[str] = mapped_column(String(128))
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    vector: Mapped[list[float]] = mapped_column(JSON)
    dimensions: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["Source"] = relationship(back_populates="embeddings")
