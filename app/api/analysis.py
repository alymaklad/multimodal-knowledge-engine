from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.reel import Reel
from app.schemas.analysis import ReelAnalysisRead
from app.services.authorization import get_owned_reel

router = APIRouter(prefix="/api/reels", tags=["analysis"])


@router.get("/{reel_id}/analysis", response_model=ReelAnalysisRead)
def get_reel_analysis(
    reel_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> ReelAnalysisRead:
    if get_owned_reel(db, reel_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    reel = db.scalar(
        select(Reel)
        .where(Reel.id == reel_id)
        .options(
            selectinload(Reel.transcript),
            selectinload(Reel.ocr_results),
            selectinload(Reel.visual_analysis),
            selectinload(Reel.extracted_content),
            selectinload(Reel.provenance),
        )
    )
    assert reel is not None
    return ReelAnalysisRead(
        reel_id=reel.id,
        transcript=reel.transcript,
        ocr_results=sorted(reel.ocr_results, key=lambda result: result.timestamp_seconds or 0),
        visual_analysis=reel.visual_analysis,
        extracted_content=reel.extracted_content,
        provenance=reel.provenance,
    )
