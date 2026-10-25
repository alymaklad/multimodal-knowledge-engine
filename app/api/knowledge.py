from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.providers.embeddings import get_embedding_provider
from app.schemas.embedding import DuplicatePair, IndexResult
from app.schemas.rag import AnswerResponse, AskRequest
from app.services.authorization import get_owned_collection, get_owned_reel
from app.services.duplicates import find_near_duplicates
from app.services.embeddings import index_collection, index_reel
from app.services.rag import answer_collection_question

router = APIRouter(prefix="/api/collections", tags=["knowledge"])


@router.post("/{collection_id}/index", response_model=IndexResult)
def rebuild_collection_index(
    collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> IndexResult:
    """Backfill vectors, needed after an embedding-model change or a bulk import."""
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    counts = index_collection(db, collection_id)
    return IndexResult(collection_id=collection_id, model=get_embedding_provider().model, **counts)


@router.post("/{collection_id}/ask", response_model=AnswerResponse)
def ask_collection(
    collection_id: int,
    payload: AskRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> AnswerResponse:
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return answer_collection_question(
        db,
        collection_id=collection_id,
        question=payload.question,
        evidence_limit=payload.evidence_limit,
        content_type=payload.content_type,
    )


@router.get("/{collection_id}/duplicates", response_model=list[DuplicatePair])
def collection_duplicates(
    collection_id: int,
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[DuplicatePair]:
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return find_near_duplicates(db, collection_id=collection_id, threshold=threshold)


reel_router = APIRouter(prefix="/api/reels", tags=["knowledge"])


@reel_router.post("/{reel_id}/index", response_model=IndexResult)
def rebuild_reel_index(
    reel_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> IndexResult:
    reel = get_owned_reel(db, reel_id, user_id)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    chunk_count = index_reel(db, reel)
    db.commit()
    return IndexResult(
        collection_id=reel.collection_id,
        indexed_reels=1 if chunk_count else 0,
        indexed_chunks=chunk_count,
        model=get_embedding_provider().model,
    )
