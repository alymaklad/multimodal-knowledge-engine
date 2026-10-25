from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    segments: list[dict[str, Any]] = field(default_factory=list)
    model: str = "unknown"


@dataclass(frozen=True)
class OCRObservation:
    frame_path: str
    text: str
    confidence: float | None = None
    bounding_boxes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class VisionResult:
    description: str
    analysis: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    model: str = "unknown"


@dataclass(frozen=True)
class ExtractionResult:
    content_type: str
    data: dict[str, Any]
    text_representation: str
    confidence: float | None = None


@dataclass(frozen=True)
class ImageDescription:
    text: str
    model: str = "unknown"


@dataclass(frozen=True)
class EvidenceChunk:
    source_id: int
    title: str
    origin_url: str | None
    content_type: str | None
    text: str
    score: float


@dataclass(frozen=True)
class GroundedAnswer:
    answer: str
    grounded: bool
    model: str
    cited_source_ids: list[int] = field(default_factory=list)


class SpeechProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path) -> TranscriptionResult: ...


class OCRProvider(ABC):
    @abstractmethod
    def extract(self, frame_paths: list[Path]) -> list[OCRObservation]: ...


class VisionProvider(ABC):
    @abstractmethod
    def analyze_video(self, frame_paths: list[Path], transcript: str, ocr_text: str) -> VisionResult: ...


class ImageDescriber(ABC):
    """Describes a standalone image (a chart, a diagram, a photo of a whiteboard) as searchable text."""

    @abstractmethod
    def describe(self, image_path: Path, ocr_text: str) -> ImageDescription: ...


class ExtractionProvider(ABC):
    @abstractmethod
    def extract(self, vision: VisionResult, transcript: str, ocr_text: str) -> list[ExtractionResult]: ...


class AnswerProvider(ABC):
    """Question answering that must be grounded in supplied evidence, never in model priors."""

    @abstractmethod
    def answer(self, question: str, evidence: list[EvidenceChunk]) -> GroundedAnswer: ...
