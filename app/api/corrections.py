from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.analysis import ExtractedContent
from app.models.correction import ExtractionCorrection
from app.schemas.correction import CorrectionCreate, CorrectionRead
from app.services.authorization import get_owned_reel

router = APIRouter(prefix="/api/reels", tags=["corrections"])


@router.post("/{reel_id}/corrections", response_model=CorrectionRead, status_code=status.HTTP_201_CREATED)
def create_correction(reel_id: int, payload: CorrectionCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> ExtractionCorrection:
    if get_owned_reel(db, reel_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    content = db.scalar(select(ExtractedContent).where(ExtractedContent.reel_id == reel_id, ExtractedContent.content_type == payload.content_type))
    if content is None:
        raise HTTPException(status_code=404, detail="Extracted content type not found")
    correction = ExtractionCorrection(
        user_id=user_id, reel_id=reel_id, content_type=payload.content_type,
        field_name=payload.field_name, previous_value=content.data.get(payload.field_name),
        corrected_value=payload.corrected_value, note=payload.note,
    )
    db.add(correction); db.commit(); db.refresh(correction)
    return correction


@router.get("/{reel_id}/corrections", response_model=list[CorrectionRead])
def list_corrections(reel_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> list[ExtractionCorrection]:
    if get_owned_reel(db, reel_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    return list(db.scalars(select(ExtractionCorrection).where(ExtractionCorrection.reel_id == reel_id, ExtractionCorrection.user_id == user_id).order_by(ExtractionCorrection.created_at.desc())))
