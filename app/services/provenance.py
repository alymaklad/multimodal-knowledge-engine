"""Record which models produced a source's knowledge (architecture section 41)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.analysis import Transcript, VisualAnalysis
from app.models.embedding import ContentEmbedding
from app.models.provenance import ProcessingProvenance


def record_provenance(db: Session, *, source_id: int, processing_version: str) -> ProcessingProvenance:
    """Capture the model set actually used, read back from what the stage persisted.

    Reading the stored artifacts rather than the configuration means the record reflects the run
    that happened, not the configuration as it stands when someone later inspects it.
    """
    settings = get_settings()
    # Sessions here run with autoflush off, so the stage's pending writes must be flushed
    # before they can be read back.
    db.flush()
    transcript = db.scalar(select(Transcript).where(Transcript.source_id == source_id))
    visual = db.scalar(select(VisualAnalysis).where(VisualAnalysis.source_id == source_id))
    embedding = db.scalar(select(ContentEmbedding).where(ContentEmbedding.source_id == source_id))

    models = {
        "transcription_model": transcript.model if transcript else None,
        "vision_model": visual.model if visual else None,
        "embedding_model": embedding.model if embedding else None,
        "ocr_provider": settings.ocr_provider,
        "extraction_provider": settings.extraction_provider,
        "extraction_prompt_version": settings.extraction_prompt_version,
    }

    existing = db.scalar(select(ProcessingProvenance).where(ProcessingProvenance.source_id == source_id))
    if existing is None:
        existing = ProcessingProvenance(source_id=source_id, processing_version=processing_version, models=models)
        db.add(existing)
    else:
        existing.processing_version = processing_version
        existing.models = models
    return existing
