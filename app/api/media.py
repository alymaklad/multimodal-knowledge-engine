from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.stored_object import StoredObject
from app.schemas.media import PublishResponse, StoredObjectRead
from app.services.authorization import get_owned_source
from app.services.object_store import get_object_store
from app.services.publishing import publish_source_media

router = APIRouter(prefix="/api/sources", tags=["media"])


@router.get("/{source_id}/media", response_model=list[StoredObjectRead])
def list_source_media(
    source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> list[StoredObjectRead]:
    """List durable objects for a source, with a signed URL where the backend can issue one."""
    if get_owned_source(db, source_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    store = get_object_store()
    objects = db.scalars(
        select(StoredObject)
        .where(StoredObject.source_id == source_id)
        .order_by(StoredObject.kind, StoredObject.sequence_number, StoredObject.id)
    )
    results = []
    for item in objects:
        signed = store.signed_url(item.storage_key)
        results.append(
            StoredObjectRead(
                id=item.id,
                kind=item.kind.value,
                storage_key=item.storage_key,
                backend=item.backend,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                sequence_number=item.sequence_number,
                created_at=item.created_at,
                # Backends that cannot sign fall back to streaming through this API's own
                # ownership check, so an object is never reachable without authorization.
                url=signed or f"/api/sources/{source_id}/media/{item.id}",
                signed=signed is not None,
            )
        )
    return results


@router.get("/{source_id}/media/{object_id}")
def download_source_media(
    source_id: int,
    object_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:
    if get_owned_source(db, source_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    stored = db.scalar(
        select(StoredObject).where(StoredObject.id == object_id, StoredObject.source_id == source_id)
    )
    if stored is None:
        raise HTTPException(status_code=404, detail="Stored object not found")
    try:
        stream = get_object_store().open(stored.storage_key)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Stored object is no longer available") from error
    return StreamingResponse(stream, media_type=stored.content_type)


@router.post("/{source_id}/publish", response_model=PublishResponse)
def publish_source(
    source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> PublishResponse:
    """Republish a source's working files, for a backend change or a failed publication."""
    source = get_owned_source(db, source_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    result = publish_source_media(db, source)
    db.commit()
    return PublishResponse(
        source_id=source_id, backend=result.backend, published=result.published, skipped=result.skipped
    )
