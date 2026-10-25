"""PP-OCR based text detection and recognition.

Tesseract is tuned for scanned documents and does poorly on much of the text this project meets:
stylised, animated, low-contrast captions burned into video, and text in photos and slides. PP-OCR
is trained on scene text and handles rotation, colour, and decorative fonts far better - which
matters because in a silent video the on-screen text is the *only* record of what it says.

Two backends behind one provider, because installing PaddlePaddle on Windows is often painful:

- `rapidocr` runs the same PP-OCR models through ONNX Runtime. No PaddlePaddle, pip-installable
  everywhere, CPU by default.
- `paddleocr` uses the official runtime when it is available.

The provider prefers whichever is installed, so the choice is an availability question rather than a
configuration burden.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.gpu import prefer_torch_cudnn
from app.providers.base import OCRObservation, OCRProvider

logger = logging.getLogger(__name__)

# Recognition below this is usually decorative artefacts rather than readable text, and passing it
# downstream would give the extractor confident-looking noise to work with.
MIN_LINE_CONFIDENCE = 0.5


class PaddleOCRProvider(OCRProvider):
    """Scene-text OCR over sampled frames, one observation per frame."""

    def __init__(self, language: str = "en") -> None:
        self._language = language
        self._engine, self._backend = _load_engine(language)

    @property
    def backend(self) -> str:
        return self._backend

    def extract(self, frame_paths: list[Path]) -> list[OCRObservation]:
        observations: list[OCRObservation] = []
        for frame_path in frame_paths:
            lines = self._read(frame_path)
            kept = [line for line in lines if line["confidence"] >= MIN_LINE_CONFIDENCE]
            if not kept:
                continue
            observations.append(
                OCRObservation(
                    frame_path=str(frame_path),
                    # Newline-joined: caption lines are usually separate statements
                    # ("BULGARIAN SPLIT SQUAT" / "3 x 10 EACH LEG"), not one sentence.
                    text="\n".join(line["text"] for line in kept),
                    confidence=sum(line["confidence"] for line in kept) / len(kept),
                    bounding_boxes=[{"box": line["box"], "text": line["text"]} for line in kept],
                )
            )
        return observations

    def _read(self, frame_path: Path) -> list[dict[str, Any]]:
        if self._backend == "rapidocr":
            return _read_rapidocr(self._engine, frame_path)
        return _read_paddleocr(self._engine, frame_path)


def _load_engine(language: str) -> tuple[Any, str]:
    # onnxruntime brings its own CUDA libraries; torch must claim cuDNN first or a later
    # vision-model call aborts the process. See app/core/gpu.py.
    prefer_torch_cudnn()
    try:
        from rapidocr_onnxruntime import RapidOCR

        return RapidOCR(), "rapidocr"
    except ImportError:
        pass
    try:
        from paddleocr import PaddleOCR

        return PaddleOCR(use_angle_cls=True, lang=language, show_log=False), "paddleocr"
    except ImportError as error:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Install rapidocr-onnxruntime (recommended on Windows) or paddleocr to use "
            "OCR_PROVIDER=paddleocr, or set OCR_PROVIDER=tesseract"
        ) from error


def _read_rapidocr(engine: Any, frame_path: Path) -> list[dict[str, Any]]:
    result, _elapsed = engine(str(frame_path))
    if not result:
        return []
    # RapidOCR returns [box, text, score] per detected line.
    return [
        {"box": _as_box(box), "text": str(text).strip(), "confidence": float(score)}
        for box, text, score in result
        if str(text).strip()
    ]


def _read_paddleocr(engine: Any, frame_path: Path) -> list[dict[str, Any]]:  # pragma: no cover
    result = engine.ocr(str(frame_path), cls=True)
    if not result or not result[0]:
        return []
    lines = []
    for box, (text, score) in result[0]:
        if str(text).strip():
            lines.append({"box": _as_box(box), "text": str(text).strip(), "confidence": float(score)})
    return lines


def _as_box(box: Any) -> list[list[float]]:
    """Normalise the polygon to plain floats so it serialises into the JSON column."""
    try:
        return [[float(x), float(y)] for x, y in box]
    except (TypeError, ValueError):
        return []
