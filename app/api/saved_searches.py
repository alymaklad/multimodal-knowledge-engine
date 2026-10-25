from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.saved_search import SavedSearch
from app.schemas.saved_search import SavedSearchCreate, SavedSearchRead
from app.schemas.search import SearchResult
from app.services.authorization import get_owned_collection
from app.services.retrieval import hybrid_search, search_collection, semantic_search

router = APIRouter(prefix="/api/saved-searches", tags=["saved searches"])


@router.post("", response_model=SavedSearchRead, status_code=status.HTTP_201_CREATED)
def create_saved_search(payload: SavedSearchCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> SavedSearch:
    if get_owned_collection(db, payload.collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    saved = SavedSearch(user_id=user_id, collection_id=payload.collection_id, name=payload.name, query=payload.query, filters={"content_type": payload.content_type, "minimum_score": payload.minimum_score, "mode": payload.mode, "creator": payload.creator, "topic": payload.topic})
    db.add(saved); db.commit(); db.refresh(saved)
    return SavedSearchRead(id=saved.id, collection_id=saved.collection_id, name=saved.name, query=saved.query, content_type=saved.filters.get("content_type"), minimum_score=saved.filters.get("minimum_score", 1), mode=saved.filters.get("mode", "lexical"), creator=saved.filters.get("creator"), topic=saved.filters.get("topic"), created_at=saved.created_at)


@router.get("", response_model=list[SavedSearchRead])
def list_saved_searches(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[SavedSearchRead]:
    entries = db.scalars(select(SavedSearch).where(SavedSearch.user_id == user_id).order_by(SavedSearch.created_at.desc()))
    return [SavedSearchRead(id=item.id, collection_id=item.collection_id, name=item.name, query=item.query, content_type=item.filters.get("content_type"), minimum_score=item.filters.get("minimum_score", 1), mode=item.filters.get("mode", "lexical"), creator=item.filters.get("creator"), topic=item.filters.get("topic"), created_at=item.created_at) for item in entries]


@router.post("/{search_id}/run", response_model=list[SearchResult])
def run_saved_search(search_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[SearchResult]:
    saved = db.scalar(select(SavedSearch).where(SavedSearch.id == search_id, SavedSearch.user_id == user_id))
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    mode = saved.filters.get("mode", "lexical")
    if mode == "semantic":
        return semantic_search(db, collection_id=saved.collection_id, query=saved.query, limit=50, content_type=saved.filters.get("content_type"), creator=saved.filters.get("creator"), topic=saved.filters.get("topic"))
    if mode == "hybrid":
        return hybrid_search(db, collection_id=saved.collection_id, query=saved.query, limit=50, content_type=saved.filters.get("content_type"), creator=saved.filters.get("creator"), topic=saved.filters.get("topic"))
    return search_collection(db, collection_id=saved.collection_id, query=saved.query, limit=50, content_type=saved.filters.get("content_type"), minimum_score=saved.filters.get("minimum_score", 1), creator=saved.filters.get("creator"), topic=saved.filters.get("topic"))


@router.delete("/{search_id}", status_code=204)
def delete_saved_search(search_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> None:
    saved = db.scalar(select(SavedSearch).where(SavedSearch.id == search_id, SavedSearch.user_id == user_id))
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(saved); db.commit()
