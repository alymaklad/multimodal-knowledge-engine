from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.core.security import get_current_user_id
from app.db.database import get_db
from app.models.source import Source
from app.schemas.analysis import SourceAnalysisRead
from app.services.authorization import get_owned_source

router = APIRouter(prefix="/api/sources", tags=["analysis"])


@router.get("/{source_id}/analysis", response_model=SourceAnalysisRead)
def get_source_analysis(
    source_id: int, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> SourceAnalysisRead:
    if get_owned_source(db, source_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    source = db.scalar(
        select(Source)
        .where(Source.id == source_id)
        .options(
            selectinload(Source.transcript),
            selectinload(Source.ocr_results),
            selectinload(Source.visual_analysis),
            selectinload(Source.extracted_content),
            selectinload(Source.provenance),
        )
    )
    assert source is not None
    return SourceAnalysisRead(
        source_id=source.id,
        transcript=source.transcript,
        ocr_results=sorted(source.ocr_results, key=lambda result: result.timestamp_seconds or 0),
        visual_analysis=source.visual_analysis,
        extracted_content=source.extracted_content,
        provenance=source.provenance,
    )
