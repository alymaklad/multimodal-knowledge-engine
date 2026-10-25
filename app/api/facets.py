from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.schemas.facets import CollectionFacets
from app.services.authorization import get_owned_collection
from app.services.facets import collection_facets

router = APIRouter(prefix="/api/collections", tags=["facets"])


@router.get("/{collection_id}/facets", response_model=CollectionFacets)
def get_facets(collection_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> CollectionFacets:
    if get_owned_collection(db, collection_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection_facets(db, collection_id)
