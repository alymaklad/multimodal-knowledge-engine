from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.models.collection import Collection
from app.models.reel import Reel
from app.schemas.collection import CollectionCreate, CollectionRead, CollectionUpdate
from app.schemas.export import CollectionExport
from app.services.authorization import get_owned_collection
from app.services.export import export_collection
from app.services.lifecycle import delete_collection_and_local_media
from fastapi.responses import StreamingResponse
import csv
from io import StringIO

router = APIRouter(prefix="/api/collections", tags=["collections"])


@router.post("", response_model=CollectionRead, status_code=status.HTTP_201_CREATED)
def create_collection(
    payload: CollectionCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Collection:
    collection = Collection(**payload.model_dump(), user_id=user_id)
    db.add(collection)
    db.commit()
    db.refresh(collection)
    return collection


@router.get("", response_model=list[CollectionRead])
def list_collections(
    offset: int = 0, limit: int = 50, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> list[CollectionRead]:
    """Collections with their Reel counts, computed in one grouped query."""
    rows = db.execute(
        select(Collection, func.count(Reel.id))
        .outerjoin(Reel, Reel.collection_id == Collection.id)
        .where(Collection.user_id == user_id)
        .group_by(Collection.id)
        .order_by(Collection.created_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    ).all()
    return [
        CollectionRead.model_validate(collection).model_copy(update={"reel_count": count})
        for collection, count in rows
    ]


@router.get("/{collection_id}", response_model=CollectionRead)
def get_collection(
    collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> Collection:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id, Collection.user_id == user_id))
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


@router.patch("/{collection_id}", response_model=CollectionRead)
def update_collection(collection_id: int, payload: CollectionUpdate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> Collection:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id, Collection.user_id == user_id))
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    collection.name, collection.description = payload.name, payload.description
    db.commit(); db.refresh(collection)
    return collection


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_collection(collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> None:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id, Collection.user_id == user_id))
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    delete_collection_and_local_media(db, collection)


@router.get("/{collection_id}/export", response_model=CollectionExport)
def export_owned_collection(
    collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> CollectionExport:
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return export_collection(db, collection)


@router.get("/{collection_id}/export.csv")
def export_owned_collection_csv(collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> StreamingResponse:
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    export = export_collection(db, collection)
    output = StringIO(); writer = csv.writer(output)
    writer.writerow(["reel_id", "url", "status", "creator", "caption", "content_type", "data"])
    for reel in export.reels:
        for item in reel.extracted_content or [{}]:
            writer.writerow([reel.id, reel.instagram_url, reel.status, reel.creator_username or "", reel.caption or "", item.get("content_type", ""), item.get("data", item)])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="collection-{collection.id}.csv"'})


@router.post("/{collection_id}/clone", response_model=CollectionRead, status_code=status.HTTP_201_CREATED)
def clone_collection(collection_id: int, name: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> Collection:
    source = get_owned_collection(db, collection_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    clone = Collection(user_id=user_id, name=name.strip(), description=source.description)
    if not clone.name:
        raise HTTPException(status_code=422, detail="Clone name must not be blank")
    db.add(clone); db.commit(); db.refresh(clone)
    return clone
