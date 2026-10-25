from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.providers.embeddings import get_embedding_provider
from app.schemas.embedding import DuplicatePair, IndexResult
from app.schemas.rag import AnswerResponse, AskRequest
from app.services.authorization import get_owned_collection, get_owned_source
from app.services.duplicates import find_near_duplicates
from app.services.embeddings import index_collection, index_source
from app.services.rag import answer_collection_question
from app.services.retrieval import SourceScopeError

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
    try:
        return answer_collection_question(
            db,
            collection_id=collection_id,
            question=payload.question,
            evidence_limit=payload.evidence_limit,
            content_type=payload.content_type,
            source_ids=payload.source_ids,
        )
    except SourceScopeError as error:
        raise HTTPException(
            status_code=404, detail={"message": "Sources not in this collection", "source_ids": error.source_ids}
        ) from error


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


source_router = APIRouter(prefix="/api/sources", tags=["knowledge"])


@source_router.post("/{source_id}/index", response_model=IndexResult)
def rebuild_source_index(
    source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> IndexResult:
    source = get_owned_source(db, source_id, user_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    chunk_count = index_source(db, source)
    db.commit()
    return IndexResult(
        indexed_sources=1 if chunk_count else 0,
        indexed_chunks=chunk_count,
        model=get_embedding_provider().model,
    )
