"""The knowledge graph: entities named in a user's sources, linked through the chunks that name them.

Built at indexing time, after the chunks exist. Each chunk is read once by the graph extractor; every
entity it names gets a mention row pointing back at that chunk, and every relationship it states gets
a relation row pointing at the same chunk. Nothing in the graph is free-floating, so whatever the
graph helps retrieve is still an ordinary chunk that can be cited.

Entities belong to the owner, which is what connects sources: "DPR" named in two sources is one
entity with mentions in both. A per-source summary is written at the same time. It is shown in the
app, but never handed to the answer model as evidence, so every claim still cites a passage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.models.graph import Entity, EntityMention, EntityRelation
from app.models.source import Source
from app.providers.base import GraphExtractor, Summarizer
from app.providers.factory import get_graph_extractor, get_summarizer
from app.providers.graph_local import normalize_entity
from app.services.chunking import SUMMARY_CHUNK_KEY, Chunk

logger = logging.getLogger(__name__)
NAME_LIMIT = 200


@dataclass(frozen=True)
class GraphStats:
    entities: int
    mentions: int
    relations: int
    summarized: bool


def clear_source_graph(db: Session, source_id: int) -> None:
    db.execute(delete(EntityRelation).where(EntityRelation.source_id == source_id))
    db.execute(delete(EntityMention).where(EntityMention.source_id == source_id))


def prune_orphan_entities(db: Session, user_id: str) -> None:
    """Drop entities no chunk mentions any more, such as those of a deleted or reprocessed source."""
    orphaned = select(Entity.id).where(
        Entity.user_id == user_id,
        ~exists().where(EntityMention.entity_id == Entity.id),
    )
    ids = list(db.scalars(orphaned))
    if ids:
        db.execute(
            delete(EntityRelation).where(EntityRelation.subject_id.in_(ids) | EntityRelation.object_id.in_(ids))
        )
        db.execute(delete(Entity).where(Entity.id.in_(ids)))


def build_source_graph(
    db: Session,
    source: Source,
    chunks: list[Chunk],
    *,
    extractor: GraphExtractor | None = None,
    summarizer: Summarizer | None = None,
) -> GraphStats:
    """Replace the source's entities, relations and summary from its current chunks.

    A graph or summary that fails to build leaves the source searchable without it: the graph only
    adds a ranking signal, so a model outage should not fail indexing.
    """
    extractor = extractor if extractor is not None else get_graph_extractor()
    summarizer = summarizer if summarizer is not None else get_summarizer()
    clear_source_graph(db, source.id)
    evidence = [chunk for chunk in chunks if chunk.chunk_key != SUMMARY_CHUNK_KEY]

    entities: dict[str, Entity] = {}
    mentions: dict[tuple[str, str], int] = {}
    relations: set[tuple[str, str, str, str]] = set()
    if extractor is not None and evidence:
        try:
            for chunk in evidence:
                _extract_chunk(db, source.user_id, chunk, extractor, entities, mentions, relations)
        except Exception:
            logger.exception("Graph extraction failed for source %s", source.id)
            entities, mentions, relations = {}, {}, set()

    db.add_all(
        EntityMention(entity_id=entities[name].id, source_id=source.id, chunk_key=chunk_key, count=count)
        for (name, chunk_key), count in mentions.items()
    )
    db.add_all(
        EntityRelation(
            source_id=source.id,
            chunk_key=chunk_key,
            subject_id=entities[subject].id,
            object_id=entities[object_].id,
            label=label[:120],
        )
        for subject, label, object_, chunk_key in relations
    )
    db.flush()
    prune_orphan_entities(db, source.user_id)

    source.summary, source.summary_model = None, None
    if summarizer is not None and evidence:
        try:
            summary = summarizer.summarize(source.title or "", [chunk.text for chunk in evidence])
            if summary.text:
                source.summary, source.summary_model = summary.text, summary.model[:128]
        except Exception:
            logger.exception("Summary failed for source %s", source.id)
    return GraphStats(
        entities=len({name for name, _ in mentions}),
        mentions=len(mentions),
        relations=len(relations),
        summarized=source.summary is not None,
    )


def _extract_chunk(
    db: Session,
    user_id: str,
    chunk: Chunk,
    extractor: GraphExtractor,
    entities: dict[str, Entity],
    mentions: dict[tuple[str, str], int],
    relations: set[tuple[str, str, str, str]],
) -> None:
    extraction = extractor.extract(chunk.text)
    lowered = chunk.text.lower()

    def mention(name: str, kind: str) -> str | None:
        key = normalize_entity(name)[:NAME_LIMIT]
        if len(key) < 2:
            return None
        if key not in entities:
            entities[key] = _upsert_entity(db, user_id, name[:NAME_LIMIT], key, kind)
        mentions[(key, chunk.chunk_key)] = max(1, lowered.count(key))
        return key

    for entity in extraction.entities:
        mention(entity.name, entity.kind)
    for relation in extraction.relations:
        subject = mention(relation.subject, "other")
        object_ = mention(relation.object, "other")
        if subject and object_ and subject != object_ and relation.label:
            relations.add((subject, relation.label, object_, chunk.chunk_key))


def _upsert_entity(db: Session, user_id: str, name: str, normalized: str, kind: str) -> Entity:
    entity = db.scalar(select(Entity).where(Entity.user_id == user_id, Entity.normalized == normalized))
    if entity is None:
        entity = Entity(user_id=user_id, name=name, normalized=normalized, kind=kind)
        db.add(entity)
        db.flush()
    return entity
