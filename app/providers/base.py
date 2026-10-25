from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# The one structured record a video yields: what it is about, what it says, and who or what it names.
KEY_POINTS_CONTENT_TYPE = "key_points"
KEY_POINT_FIELDS = ("topic", "key_points", "entities")


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
    chunk_key: str = ""
    # A readable location within the source, such as "p. 4" or "12:30-13:45", when one is known.
    citation: str | None = None


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


@dataclass(frozen=True)
class ChatTurn:
    """One earlier turn of a conversation, as the model is shown it."""

    role: str  # "user" or "assistant"
    content: str


class ChatProviderError(RuntimeError):
    """The chat model could not answer. The message is safe to show the user."""


class ChatRefusedError(ChatProviderError):
    """The model declined to answer; any text it already streamed must be discarded."""


class ChatProvider(ABC):
    """A conversational model that answers only from numbered evidence passages.

    Answers cite passages inline as [1], [2], ... by their position in `evidence`; the chat service
    turns those markers into citations and drops any that point at nothing.
    """

    model: str

    @abstractmethod
    def rewrite_query(self, history: list[ChatTurn], question: str) -> str:
        """A standalone search query for a follow-up question, such as "and the second one?"."""

    @abstractmethod
    def stream_answer(
        self, history: list[ChatTurn], question: str, evidence: list[EvidenceChunk]
    ) -> Iterator[str]:
        """Yield the answer as text fragments, in order."""
