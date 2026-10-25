import re
from pathlib import Path

from app.core.config import get_settings
from app.providers.base import (
    KEY_POINTS_CONTENT_TYPE,
    ExtractionProvider,
    ExtractionResult,
    OCRObservation,
    OCRProvider,
    VisionProvider,
    VisionResult,
)
from app.providers.structured_extraction import key_points_text


class TesseractOCRProvider(OCRProvider):
    """Local OCR implementation for the initial pipeline; PaddleOCR can replace it later."""

    def extract(self, frame_paths: list[Path]) -> list[OCRObservation]:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as error:
            raise RuntimeError("Install pytesseract and Pillow to enable local OCR") from error
        pytesseract.pytesseract.tesseract_cmd = get_settings().tesseract_binary
        observations: list[OCRObservation] = []
        for frame_path in frame_paths:
            data = pytesseract.image_to_data(Image.open(frame_path), output_type=pytesseract.Output.DICT)
            words = [word.strip() for word in data["text"] if word.strip()]
            confidences = [float(value) for value in data["conf"] if value not in {"-1", -1}]
            if words:
                observations.append(
                    OCRObservation(
                        frame_path=str(frame_path),
                        text=" ".join(words),
                        confidence=sum(confidences) / len(confidences) / 100 if confidences else None,
                    )
                )
        return observations


class EvidenceVisionProvider(VisionProvider):
    """Safe local fallback: it describes only supplied textual evidence, never invents visuals."""

    def analyze_video(self, frame_paths: list[Path], transcript: str, ocr_text: str) -> VisionResult:
        evidence = []
        if transcript:
            evidence.append("speech transcript available")
        if ocr_text:
            evidence.append("on-screen text detected")
        description = f"{len(frame_paths)} sampled frames; " + (", ".join(evidence) if evidence else "no textual evidence detected")
        return VisionResult(description=description, analysis={"frame_count": len(frame_paths)}, model="local-evidence-v1")


class RuleBasedExtractionProvider(ExtractionProvider):
    """Deterministic key points without a model: the evidence's own words, never a paraphrase.

    On-screen text lines and the opening sentences of the transcript become the key points, each
    attributed to where it was read. Nothing is inferred, so there is no topic and no entity list;
    a model provider fills those. The confidence sits between the review thresholds, so secondary
    verification checks every point against the stored evidence before the record is accepted.
    """

    def extract(self, vision: VisionResult, transcript: str, ocr_text: str) -> list[ExtractionResult]:
        points: list[str] = []
        sources: list[str] = []
        for line in _distinct(ocr_text.splitlines()):
            points.append(line)
            sources.append("ocr")
        for sentence in _distinct(_SENTENCE_END.split(transcript)):
            if len(points) >= MAX_KEY_POINTS:
                break
            points.append(sentence)
            sources.append("transcript")
        points = points[:MAX_KEY_POINTS]
        if not points:
            return []
        evidence = "ocr" if sources[0] == "ocr" else "transcript"
        data = {"topic": None, "key_points": points, "entities": [], "evidence": {"key_points": evidence}}
        return [
            ExtractionResult(
                content_type=KEY_POINTS_CONTENT_TYPE,
                data=data,
                text_representation=key_points_text(data),
                confidence=0.7,
            )
        ]


MAX_KEY_POINTS = 8
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _distinct(lines: list[str]) -> list[str]:
    """Non-trivial lines in first-seen order; a caption repeated on every frame counts once."""
    seen: dict[str, None] = {}
    for line in lines:
        text = " ".join(line.split())
        if len(text) >= 4 and text.lower() not in {existing.lower() for existing in seen}:
            seen.setdefault(text, None)
    return list(seen)
