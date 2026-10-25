import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class StoredObjectKind(str, enum.Enum):
    video = "video"
    audio = "audio"
    frame = "frame"
    thumbnail = "thumbnail"


class StoredObject(Base):
    """An artifact published to durable object storage (architecture section 8).

    Separate from `media_assets`, which records the *working* local files the processors read. This
    records what survives processing, what can be served, and which backend holds it.
    """

    __tablename__ = "stored_objects"
    __table_args__ = (UniqueConstraint("reel_id", "storage_key", name="uq_stored_object_reel_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    reel_id: Mapped[int] = mapped_column(ForeignKey("reels.id"), index=True)
    kind: Mapped[StoredObjectKind] = mapped_column(Enum(StoredObjectKind), index=True)
    storage_key: Mapped[str] = mapped_column(String(1024))
    backend: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reel: Mapped["Reel"] = relationship(back_populates="stored_objects")
