from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.providers.base import ImageDescriber, OCRProvider, SpeechProvider


class ProcessingError(RuntimeError):
    """The source's content could not be turned into evidence. The message is shown to the user."""


@dataclass(frozen=True)
class SegmentDraft:
    text: str
    modality: str
    locator: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessorResult:
    segments: list[SegmentDraft]
    # Model or library names that produced the segments, recorded with the stage metric.
    models: list[str] = field(default_factory=list)
    # Problems that did not stop processing, such as one unreadable page in a long PDF.
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    duration: float | None = None


class ProcessorContext:
    """The providers a processor may need, resolved only when one asks.

    Building an OCR or speech provider can load model weights, so a Markdown file must not pay for
    them. Each provider is created at most once per processing run.
    """

    def __init__(
        self,
        *,
        ocr: Callable[[], OCRProvider],
        speech: Callable[[], SpeechProvider | None],
        image_describer: Callable[[], ImageDescriber | None],
    ) -> None:
        self._factories = {"ocr": ocr, "speech": speech, "image_describer": image_describer}
        self._resolved: dict[str, Any] = {}

    def _get(self, name: str) -> Any:
        if name not in self._resolved:
            self._resolved[name] = self._factories[name]()
        return self._resolved[name]

    @property
    def ocr(self) -> OCRProvider:
        return self._get("ocr")

    @property
    def speech(self) -> SpeechProvider | None:
        return self._get("speech")

    @property
    def image_describer(self) -> ImageDescriber | None:
        return self._get("image_describer")
