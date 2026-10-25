from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class SourceSegment(Base):
    """One passage of a source's content, with where in the source it came from.

    Processors split a source at its natural boundaries - pages, sections, time windows - and the
    locator records that boundary so an answer can cite "page 4" or "12:30" rather than the whole
    source. Chunking for retrieval works from these segments, not from the raw file.

    Locator keys, all optional: `page`, `section` (heading path), `paragraph`, `start` and `end`
    (seconds), `region`.
    """

    __tablename__ = "source_segments"
    __table_args__ = (UniqueConstraint("source_id", "sequence", name="uq_segment_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    # Which kind of evidence the text is: text, transcript, ocr, or visual.
    modality: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["Source"] = relationship(back_populates="segments")
