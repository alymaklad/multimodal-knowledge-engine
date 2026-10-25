from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.models.collection import Collection, CollectionSource
from app.schemas.collection import (
    CollectionCreate,
    CollectionRead,
    CollectionSourcesAdd,
    CollectionSourcesAdded,
    CollectionUpdate,
)
from app.schemas.export import CollectionExport
from app.services.authorization import get_owned_collection
from app.services.export import export_collection
from app.services.lifecycle import delete_collection
from app.services.membership import (
    UnknownSourcesError,
    add_sources_to_collection,
    collection_source_ids,
    remove_source_from_collection,
)
from fastapi.responses import StreamingResponse
import csv
from io import StringIO

router = APIRouter(prefix="/api/collections", tags=["collections"])


def _with_count(db: Session, collection: Collection) -> CollectionRead:
    return CollectionRead.model_validate(collection).model_copy(
        update={"source_count": len(collection_source_ids(db, collection.id))}
    )


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
    """Collections with their source counts, computed in one grouped query."""
    rows = db.execute(
        select(Collection, func.count(CollectionSource.source_id))
        .outerjoin(CollectionSource, CollectionSource.collection_id == Collection.id)
        .where(Collection.user_id == user_id)
        .group_by(Collection.id)
        .order_by(Collection.created_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    ).all()
    return [
        CollectionRead.model_validate(collection).model_copy(update={"source_count": count})
        for collection, count in rows
    ]


@router.get("/{collection_id}", response_model=CollectionRead)
def get_collection(
    collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> CollectionRead:
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return _with_count(db, collection)


@router.patch("/{collection_id}", response_model=CollectionRead)
def update_collection(collection_id: int, payload: CollectionUpdate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> CollectionRead:
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    collection.name, collection.description = payload.name, payload.description
    db.commit(); db.refresh(collection)
    return _with_count(db, collection)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_owned_collection(collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> None:
    """Delete the collection only. Its sources stay in the library and in any other collection."""
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    delete_collection(db, collection)


@router.post("/{collection_id}/sources", response_model=CollectionSourcesAdded)
def add_collection_sources(
    collection_id: int,
    payload: CollectionSourcesAdd,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> CollectionSourcesAdded:
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    try:
        added = add_sources_to_collection(db, collection, payload.source_ids)
    except UnknownSourcesError as error:
        raise HTTPException(
            status_code=404, detail={"message": "Sources not found in your library", "source_ids": error.source_ids}
        ) from error
    return CollectionSourcesAdded(
        collection_id=collection.id, added=added, source_count=len(collection_source_ids(db, collection.id))
    )


@router.delete("/{collection_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_collection_source(
    collection_id: int, source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> None:
    collection = get_owned_collection(db, collection_id, user_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    if not remove_source_from_collection(db, collection, source_id):
        raise HTTPException(status_code=404, detail="Source is not in this collection")


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
    writer.writerow(["source_id", "kind", "title", "url", "status", "author", "description", "content_type", "data"])
    for source in export.sources:
        for item in source.extracted_content or [{}]:
            writer.writerow([source.id, source.kind, source.title, source.origin_url or "", source.status, source.author or "", source.description or "", item.get("content_type", ""), item.get("data", item)])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="collection-{collection.id}.csv"'})


@router.post("/{collection_id}/clone", response_model=CollectionRead, status_code=status.HTTP_201_CREATED)
def clone_collection(collection_id: int, name: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> CollectionRead:
    """A new collection over the same sources. Nothing is reprocessed, since sources are shared."""
    original = get_owned_collection(db, collection_id, user_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    clone = Collection(user_id=user_id, name=name.strip(), description=original.description)
    if not clone.name:
        raise HTTPException(status_code=422, detail="Clone name must not be blank")
    db.add(clone); db.flush()
    for source_id in collection_source_ids(db, original.id):
        db.add(CollectionSource(collection_id=clone.id, source_id=source_id))
    db.commit(); db.refresh(clone)
    return _with_count(db, clone)
