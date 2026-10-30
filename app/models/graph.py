from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Entity(Base):
    """A named thing a user's sources talk about: a person, a method, a system, a dataset, a term.

    Entities belong to the owner, not to one source, which is what links sources together: two
    sources that both mention "DPR" share the same entity.
    """

    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("user_id", "normalized", name="uq_entity_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Lowercased, whitespace-collapsed name, used to match mentions and queries.
    normalized: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="term")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    mentions: Mapped[list["EntityMention"]] = relationship(back_populates="entity", cascade="all, delete-orphan")


class EntityMention(Base):
    """An entity appearing in one retrieval chunk. The chunk key keeps every graph link citable."""

    __tablename__ = "entity_mentions"
    __table_args__ = (UniqueConstraint("entity_id", "source_id", "chunk_key", name="uq_entity_mention"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    chunk_key: Mapped[str] = mapped_column(String(128))
    count: Mapped[int] = mapped_column(Integer, default=1)

    entity: Mapped["Entity"] = relationship(back_populates="mentions")
    source: Mapped["Source"] = relationship(back_populates="entity_mentions")


class EntityRelation(Base):
    """A relationship stated in one chunk, such as "BEIR evaluated DPR", or two entities co-mentioned."""

    __tablename__ = "entity_relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    chunk_key: Mapped[str] = mapped_column(String(128))
    subject_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))

    source: Mapped["Source"] = relationship(back_populates="entity_relations")
