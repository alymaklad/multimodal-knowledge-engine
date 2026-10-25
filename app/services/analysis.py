"""Multimodal analysis with independently failing stages (architecture sections 49 and 50).

Every perception stage is isolated. A failing OCR pass must not discard a source whose speech
transcribed perfectly, and a failing transcription must not discard its on-screen text. Each stage
records what it did, how long it took, and whether it degraded, so a partial result is visible as a
partial result rather than silently indistinguishable from a clean run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.analysis import ExtractedContent, OCRResult, Transcript, VisualAnalysis
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.stage_metric import StageStatus
from app.providers.base import (
    ExtractionProvider,
    ExtractionResult,
    OCRProvider,
    SpeechProvider,
    VisionProvider,
    VisionResult,
)

logger = logging.getLogger(__name__)

SPEECH_STAGE = "transcription"
OCR_STAGE = "ocr"
VISION_STAGE = "vision"
EXTRACTION_STAGE = "extraction"


@dataclass
class StageOutcome:
    stage: str
    status: StageStatus
    duration_ms: float
    model: str | None = None
    items_processed: int | None = None
    error_message: str | None = None


@dataclass
class AnalysisOutcome:
    """What the analysis stage produced, so the caller can route on it without re-querying."""

    transcript_text: str = ""
    ocr_text: str = ""
    extractions: list[ExtractionResult] = field(default_factory=list)
    stages: list[StageOutcome] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return any(stage.status is StageStatus.failed for stage in self.stages)

    @property
    def failed_stages(self) -> list[str]:
        return [stage.stage for stage in self.stages if stage.status is StageStatus.failed]


class _Timer:
    def __enter__(self) -> "_Timer":
        self._start = perf_counter()
        return self

    def __exit__(self, *_exc) -> None:
        self.duration_ms = (perf_counter() - self._start) * 1000

    duration_ms: float = 0.0


def run_analysis(
    db: Session,
    *,
    source_id: int,
    speech_provider: SpeechProvider | None,
    ocr_provider: OCRProvider,
    vision_provider: VisionProvider,
    extraction_provider: ExtractionProvider,
    fallback_vision_provider: VisionProvider | None = None,
) -> AnalysisOutcome:
    """Run every stage, degrading rather than aborting when one provider fails."""
    assets = list(db.query(MediaAsset).filter(MediaAsset.source_id == source_id))
    audio = next((asset for asset in assets if asset.kind == MediaAssetKind.audio), None)
    frames = sorted(
        (asset for asset in assets if asset.kind == MediaAssetKind.frame),
        key=lambda asset: asset.sequence_number or 0,
    )
    frame_paths = [Path(asset.path) for asset in frames]
    stages: list[StageOutcome] = []

    transcript_text, stage = _transcribe(db, source_id, audio, speech_provider)
    stages.append(stage)

    ocr_text, stage = _run_ocr(db, source_id, frames, frame_paths, ocr_provider)
    stages.append(stage)

    vision, stage = _run_vision(
        db, source_id, frame_paths, transcript_text, ocr_text, vision_provider, fallback_vision_provider
    )
    stages.append(stage)

    extracted, stage = _run_extraction(db, source_id, vision, transcript_text, ocr_text, extraction_provider)
    stages.append(stage)

    return AnalysisOutcome(
        transcript_text=transcript_text, ocr_text=ocr_text, extractions=extracted, stages=stages
    )


def _transcribe(
    db: Session, source_id: int, audio: MediaAsset | None, provider: SpeechProvider | None
) -> tuple[str, StageOutcome]:
    if audio is None or provider is None:
        # A silent source, or transcription intentionally disabled: not a failure.
        reason = "No audio track was extracted" if provider else "No speech provider is configured"
        return "", StageOutcome(SPEECH_STAGE, StageStatus.skipped, 0.0, error_message=reason)
    with _Timer() as timer:
        try:
            result = provider.transcribe(Path(audio.path))
        except Exception as error:
            logger.warning("Transcription failed for source %s: %s", source_id, error, exc_info=True)
            return "", StageOutcome(SPEECH_STAGE, StageStatus.failed, timer.duration_ms, error_message=str(error))
    db.execute(delete(Transcript).where(Transcript.source_id == source_id))
    db.add(
        Transcript(
            source_id=source_id,
            text=result.text,
            language=result.language,
            segments=result.segments,
            model=result.model,
        )
    )
    return result.text, StageOutcome(
        SPEECH_STAGE,
        StageStatus.succeeded,
        timer.duration_ms,
        model=result.model,
        items_processed=len(result.segments),
    )


def _run_ocr(
    db: Session,
    source_id: int,
    frames: list[MediaAsset],
    frame_paths: list[Path],
    provider: OCRProvider,
) -> tuple[str, StageOutcome]:
    if not frame_paths:
        return "", StageOutcome(OCR_STAGE, StageStatus.skipped, 0.0, error_message="No frames were sampled")
    with _Timer() as timer:
        try:
            observations = provider.extract(frame_paths)
        except Exception as error:
            logger.warning("OCR failed for source %s: %s", source_id, error, exc_info=True)
            return "", StageOutcome(OCR_STAGE, StageStatus.failed, timer.duration_ms, error_message=str(error))
    # Normalize separator conventions because providers receive `Path` values on Windows/Linux.
    asset_by_path = {str(Path(asset.path)): asset for asset in frames}
    db.execute(delete(OCRResult).where(OCRResult.source_id == source_id))
    for observation in observations:
        frame = asset_by_path.get(str(Path(observation.frame_path)))
        db.add(
            OCRResult(
                source_id=source_id,
                frame_asset_id=frame.id if frame else None,
                timestamp_seconds=frame.timestamp_seconds if frame else None,
                text=observation.text,
                confidence=observation.confidence,
                bounding_boxes=observation.bounding_boxes,
            )
        )
    return _distinct_lines(observations), StageOutcome(
        OCR_STAGE, StageStatus.succeeded, timer.duration_ms, items_processed=len(frame_paths)
    )


def _distinct_lines(observations: list[Any]) -> str:
    """Collapse the OCR text passed downstream to its distinct lines, in first-seen order.

    A burned-in caption is detected in *every* sampled frame, so a raw join repeats the same two
    lines once per frame — 120 times on a long Source. That bloats the prompt with no new information
    and, observed here, pushed a small vision model into a repetition loop that produced truncated,
    unparsable JSON.

    The per-frame `OCRResult` rows are untouched: the evidence timeline still needs to know that
    "3 x 12 EACH LEG" was on screen at 0s, 1s and 2s. Only the aggregate handed to the vision and
    extraction stages is deduplicated.
    """
    seen: dict[str, None] = {}
    for observation in observations:
        for line in observation.text.splitlines():
            stripped = line.strip()
            if stripped:
                seen.setdefault(stripped, None)
    return "\n".join(seen)


def _is_memory_error(error: Exception) -> bool:
    """Whether a stage failure was an allocation failure rather than a modelling one."""
    text = str(error).lower()
    return "out of memory" in text or "cuda error" in text


def _release_vision_memory(*, unload: bool) -> None:
    """Release the vision model's VRAM, if the local vision provider is the one in use.

    Imported lazily and guarded: `VISION_PROVIDER=evidence` is a supported configuration with no
    torch installed at all, and freeing memory must never be the thing that fails a job.
    """
    try:
        from app.providers.qwen_vision import release_vision_memory

        release_vision_memory(unload=unload)
    except Exception as error:  # pragma: no cover - depends on optional install
        logger.debug("Skipping vision memory release: %s", error)


def _run_vision(
    db: Session,
    source_id: int,
    frame_paths: list[Path],
    transcript_text: str,
    ocr_text: str,
    provider: VisionProvider,
    fallback: VisionProvider | None,
) -> tuple[VisionResult, StageOutcome]:
    """Try the configured provider, then the fallback, before giving up on visual analysis."""
    with _Timer() as timer:
        try:
            vision = provider.analyze_video(frame_paths, transcript_text, ocr_text)
            status, error_message = StageStatus.succeeded, None
            _release_vision_memory(unload=False)
        except Exception as error:
            logger.warning("Vision analysis failed for source %s: %s", source_id, error, exc_info=True)
            error_message = str(error)
            vision, status = None, StageStatus.failed
            # An allocation failure leaves the device exhausted, and the next model to load is the
            # one that dies. Drop the weights so indexing does not inherit the wreckage.
            _release_vision_memory(unload=_is_memory_error(error))
            if fallback is not None:
                try:
                    vision = fallback.analyze_video(frame_paths, transcript_text, ocr_text)
                    status = StageStatus.degraded
                except Exception as fallback_error:
                    logger.warning("Vision fallback failed for source %s: %s", source_id, fallback_error)
                    error_message = f"{error_message}; fallback also failed: {fallback_error}"
    if vision is None:
        # Downstream stages still run: OCR and transcript evidence remain usable on their own.
        vision = VisionResult(
            description="Visual analysis was unavailable for this source.",
            analysis={"error": error_message},
            confidence=0.0,
            model="unavailable",
        )
        db.execute(delete(VisualAnalysis).where(VisualAnalysis.source_id == source_id))
        return vision, StageOutcome(
            VISION_STAGE, StageStatus.failed, timer.duration_ms, error_message=error_message
        )

    db.execute(delete(VisualAnalysis).where(VisualAnalysis.source_id == source_id))
    db.add(
        VisualAnalysis(
            source_id=source_id,
            description=vision.description,
            analysis_json=vision.analysis,
            model=vision.model,
            confidence=vision.confidence,
        )
    )
    return vision, StageOutcome(
        VISION_STAGE,
        status,
        timer.duration_ms,
        model=vision.model,
        items_processed=len(frame_paths),
        error_message=error_message,
    )


def _run_extraction(
    db: Session,
    source_id: int,
    vision: VisionResult,
    transcript_text: str,
    ocr_text: str,
    provider: ExtractionProvider,
) -> tuple[list[ExtractionResult], StageOutcome]:
    with _Timer() as timer:
        try:
            extracted = provider.extract(vision, transcript_text, ocr_text)
        except Exception as error:
            logger.warning("Extraction failed for source %s: %s", source_id, error, exc_info=True)
            # Prior knowledge is left untouched: a failed run should not erase a good one.
            return [], StageOutcome(
                EXTRACTION_STAGE, StageStatus.failed, timer.duration_ms, error_message=str(error)
            )
    db.execute(delete(ExtractedContent).where(ExtractedContent.source_id == source_id))
    for result in extracted:
        db.add(
            ExtractedContent(
                source_id=source_id,
                content_type=result.content_type,
                data=result.data,
                text_representation=result.text_representation,
                confidence=result.confidence,
            )
        )
    return extracted, StageOutcome(
        EXTRACTION_STAGE, StageStatus.succeeded, timer.duration_ms, items_processed=len(extracted)
    )
