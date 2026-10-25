from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import ExtractedContent
from app.models.collection import CollectionSource
from app.schemas.facets import CollectionFacets


def collection_facets(db: Session, collection_id: int) -> CollectionFacets:
    """What a collection's extracted records cover: record types, topics, and named entities."""
    entries = list(
        db.scalars(
            select(ExtractedContent)
            .join(CollectionSource, CollectionSource.source_id == ExtractedContent.source_id)
            .where(CollectionSource.collection_id == collection_id)
        )
    )
    types, topics, entities = Counter(), Counter(), Counter()
    for entry in entries:
        types[entry.content_type] += 1
        data = entry.data
        if isinstance(data.get("topic"), str) and data["topic"].strip():
            topics[data["topic"].strip()] += 1
        for item in data.get("entities", []):
            if isinstance(item, str) and item.strip():
                entities[item.strip()] += 1
    return CollectionFacets(
        content_types=types.most_common(20), topics=topics.most_common(20), entities=entities.most_common(20)
    )
