from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.collection import Collection
from app.models.reel import Reel
from app.schemas.export import CollectionExport, CollectionExportReel


def export_collection(db: Session, collection: Collection) -> CollectionExport:
    reels = list(db.scalars(select(Reel).where(Reel.collection_id == collection.id).options(selectinload(Reel.extracted_content))))
    return CollectionExport(
        collection_id=collection.id,
        name=collection.name,
        exported_at=datetime.now(timezone.utc),
        reels=[
            CollectionExportReel(
                id=reel.id, instagram_url=reel.instagram_url, status=reel.status.value,
                creator_username=reel.creator_username, caption=reel.caption,
                extracted_content=[{"content_type": item.content_type, "data": item.data, "confidence": item.confidence} for item in reel.extracted_content],
            ) for reel in reels
        ],
    )
