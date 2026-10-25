from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import ExtractedContent
from app.models.reel import Reel
from app.schemas.facets import CollectionFacets


def collection_facets(db: Session, collection_id: int) -> CollectionFacets:
    entries = list(db.scalars(select(ExtractedContent).join(Reel).where(Reel.collection_id == collection_id)))
    types, topics, technologies, muscles = Counter(), Counter(), Counter(), Counter()
    for entry in entries:
        types[entry.content_type] += 1
        data = entry.data
        if isinstance(data.get("topic"), str): topics[data["topic"]] += 1
        for item in data.get("technologies", []):
            if isinstance(item, str): technologies[item] += 1
        for item in data.get("target_muscles", data.get("primary_muscles", [])):
            if isinstance(item, str): muscles[item] += 1
    return CollectionFacets(content_types=types.most_common(20), topics=topics.most_common(20), technologies=technologies.most_common(20), target_muscles=muscles.most_common(20))
