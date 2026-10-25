from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user_id
from app.schemas.search import SearchRequest, SearchResult
from app.services.retrieval import SourceScopeError, hybrid_search, search_collection, semantic_search
from app.services.authorization import get_owned_collection

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=list[SearchResult])
def search(
    payload: SearchRequest, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> list[SearchResult]:
    if get_owned_collection(db, payload.collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    try:
        return _run_search(db, payload)
    except SourceScopeError as error:
        raise HTTPException(
            status_code=404, detail={"message": "Sources not in this collection", "source_ids": error.source_ids}
        ) from error


def _run_search(db: Session, payload: SearchRequest) -> list[SearchResult]:
    if payload.mode == "semantic":
        return semantic_search(
            db,
            collection_id=payload.collection_id,
            query=payload.query,
            limit=payload.limit,
            content_type=payload.content_type,
            author=payload.author,
            topic=payload.topic,
            source_ids=payload.source_ids,
        )
    if payload.mode == "hybrid":
        return hybrid_search(
            db,
            collection_id=payload.collection_id,
            query=payload.query,
            limit=payload.limit,
            content_type=payload.content_type,
            author=payload.author,
            topic=payload.topic,
            source_ids=payload.source_ids,
        )
    return search_collection(
        db,
        collection_id=payload.collection_id,
        query=payload.query,
        limit=payload.limit,
        content_type=payload.content_type,
        minimum_score=payload.minimum_score,
        author=payload.author,
        topic=payload.topic,
        source_ids=payload.source_ids,
    )
