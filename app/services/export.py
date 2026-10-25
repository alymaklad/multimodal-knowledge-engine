from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.collection import Collection, CollectionSource
from app.models.source import Source
from app.schemas.export import CollectionExport, CollectionExportSource


def export_collection(db: Session, collection: Collection) -> CollectionExport:
    sources = list(
        db.scalars(
            select(Source)
            .join(CollectionSource)
            .where(CollectionSource.collection_id == collection.id)
            .options(selectinload(Source.extracted_content))
            .order_by(Source.id)
        )
    )
    return CollectionExport(
        collection_id=collection.id,
        name=collection.name,
        exported_at=datetime.now(timezone.utc),
        sources=[
            CollectionExportSource(
                id=source.id, kind=source.kind.value, title=source.title, origin_url=source.origin_url,
                status=source.status.value, author=source.author, description=source.description,
                extracted_content=[{"content_type": item.content_type, "data": item.data, "confidence": item.confidence} for item in source.extracted_content],
            ) for source in sources
        ],
    )
