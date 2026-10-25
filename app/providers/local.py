import re
from pathlib import Path

from app.core.config import get_settings
from app.providers.base import (
    ExtractionProvider,
    ExtractionResult,
    OCRObservation,
    OCRProvider,
    VisionProvider,
    VisionResult,
)


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
    """Deterministic evidence-first MVP extraction; stronger LLM providers implement the same contract."""

    def extract(self, vision: VisionResult, transcript: str, ocr_text: str) -> list[ExtractionResult]:
        evidence = "\n".join(part for part in [transcript, ocr_text, vision.description] if part)
        results: list[ExtractionResult] = []
        workout = _extract_workout(evidence)
        if workout:
            results.append(
                ExtractionResult(
                    content_type="workout_exercise",
                    data=workout,
                    text_representation=_workout_text(workout),
                    confidence=0.7,
                )
            )
        ai_knowledge = _extract_ai_knowledge(evidence)
        if ai_knowledge:
            results.append(
                ExtractionResult(
                    content_type="ai_knowledge",
                    data=ai_knowledge,
                    text_representation=evidence[:2000],
                    confidence=0.6,
                )
            )
        return results


def _extract_workout(evidence: str) -> dict[str, object] | None:
    exercise_match = re.search(r"(?:exercise|workout)\s*[:\-]?\s*([A-Za-z][A-Za-z ]{2,60})", evidence, re.I)
    sets_reps = re.search(r"\b(\d{1,2})\s*(?:sets?|x)\s*(\d{1,3}(?:\s*[-–]\s*\d{1,3})?(?:\s*(?:reps?|each leg))?)", evidence, re.I)
    if not exercise_match and not sets_reps:
        return None
    # Every value below is read literally out of the evidence text, so its source is recorded.
    data: dict[str, object] = {"exercise": exercise_match.group(1).strip() if exercise_match else "Unspecified exercise"}
    evidence_map = {"exercise": "ocr" if exercise_match else "inferred"}
    if sets_reps:
        data["sets"] = int(sets_reps.group(1))
        data["reps"] = sets_reps.group(2).strip()
        evidence_map["sets"] = "ocr"
        evidence_map["reps"] = "ocr"
    data["evidence"] = evidence_map
    return data


def _workout_text(data: dict[str, object]) -> str:
    return ". ".join(f"{key}: {value}" for key, value in data.items() if key != "evidence")


def _extract_ai_knowledge(evidence: str) -> dict[str, object] | None:
    # Word boundaries, not substrings: plain containment matched "RAG" inside "leverage" on a real
    # Reel and invented a topic the video never mentioned.
    terms = [
        term
        for term in ("RAG", "BM25", "Qdrant", "LoRA", "embeddings", "hybrid search")
        if re.search(rf"\b{re.escape(term)}\b", evidence, re.I)
    ]
    if not terms:
        return None
    topic = "hybrid search" if "hybrid search" in [term.lower() for term in terms] else terms[0]
    return {"topic": topic, "technologies": terms, "evidence": {"topic": "ocr", "technologies": "ocr"}}
