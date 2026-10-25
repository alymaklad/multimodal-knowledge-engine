from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.analysis import ExtractedContent
from app.models.correction import ExtractionCorrection
from app.schemas.correction import CorrectionCreate, CorrectionRead
from app.services.authorization import get_owned_source

router = APIRouter(prefix="/api/sources", tags=["corrections"])


@router.post("/{source_id}/corrections", response_model=CorrectionRead, status_code=status.HTTP_201_CREATED)
def create_correction(source_id: int, payload: CorrectionCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> ExtractionCorrection:
    if get_owned_source(db, source_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    content = db.scalar(select(ExtractedContent).where(ExtractedContent.source_id == source_id, ExtractedContent.content_type == payload.content_type))
    if content is None:
        raise HTTPException(status_code=404, detail="Extracted content type not found")
    correction = ExtractionCorrection(
        user_id=user_id, source_id=source_id, content_type=payload.content_type,
        field_name=payload.field_name, previous_value=content.data.get(payload.field_name),
        corrected_value=payload.corrected_value, note=payload.note,
    )
    db.add(correction); db.commit(); db.refresh(correction)
    return correction


@router.get("/{source_id}/corrections", response_model=list[CorrectionRead])
def list_corrections(source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[ExtractionCorrection]:
    if get_owned_source(db, source_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return list(db.scalars(select(ExtractionCorrection).where(ExtractionCorrection.source_id == source_id, ExtractionCorrection.user_id == user_id).order_by(ExtractionCorrection.created_at.desc())))
