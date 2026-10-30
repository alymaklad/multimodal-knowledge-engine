"""Single place where configuration chooses concrete providers.

Keeping resolution here means the worker never imports a specific model implementation, so an
install without the optional model packages still runs the whole pipeline on its local defaults.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.providers.answering import ExtractiveAnswerProvider, ExtractiveChatProvider
from app.providers.base import (
    AnswerProvider,
    ChatProvider,
    ExtractionProvider,
    GraphExtractor,
    ImageDescriber,
    OCRProvider,
    Summarizer,
    VisionProvider,
)
from app.providers.local import (
    EvidenceVisionProvider,
    RuleBasedExtractionProvider,
    TesseractOCRProvider,
)


def get_ocr_provider() -> OCRProvider:
    name = get_settings().ocr_provider.lower()
    if name == "tesseract":
        return TesseractOCRProvider()
    if name in {"paddleocr", "paddle", "rapidocr", "ppocr"}:
        from app.providers.paddle_ocr import PaddleOCRProvider

        return PaddleOCRProvider()
    raise ValueError(f"Unsupported OCR provider: {name}")


def get_vision_provider() -> VisionProvider:
    settings = get_settings()
    name = settings.vision_provider.lower()
    if name in {"evidence", "local", "none"}:
        return EvidenceVisionProvider()
    if name in {"qwen", "local_vlm"}:
        from app.providers.qwen_vision import QwenVisionProvider

        return QwenVisionProvider()
    if name in {"claude", "anthropic"}:
        from app.providers.claude import ClaudeVisionProvider

        if not settings.vision_routing:
            return ClaudeVisionProvider()
        from app.providers.routing import RoutingVisionProvider

        return RoutingVisionProvider(
            small_provider=ClaudeVisionProvider(model=settings.anthropic_small_model),
            strong_provider=ClaudeVisionProvider(model=settings.anthropic_model),
        )
    raise ValueError(f"Unsupported vision provider: {name}")


def get_image_describer() -> ImageDescriber | None:
    """Image descriptions follow VISION_PROVIDER. Only Claude describes standalone images so far."""
    if get_settings().vision_provider.lower() in {"claude", "anthropic"}:
        from app.providers.claude import ClaudeImageDescriber

        return ClaudeImageDescriber()
    return None


def get_extraction_provider() -> ExtractionProvider:
    name = get_settings().extraction_provider.lower()
    if name in {"rule_based", "rules", "local"}:
        return RuleBasedExtractionProvider()
    if name in {"vision_fields", "structured"}:
        from app.providers.structured_extraction import VisionFieldExtractionProvider

        # The regex extractor stays as the fallback: a vision provider that returns only a
        # description still needs something to read.
        return VisionFieldExtractionProvider(fallback=RuleBasedExtractionProvider())
    if name in {"claude", "anthropic"}:
        from app.providers.claude import ClaudeExtractionProvider

        return ClaudeExtractionProvider()
    raise ValueError(f"Unsupported extraction provider: {name}")


def get_answer_provider() -> AnswerProvider:
    name = get_settings().answer_provider.lower()
    if name in {"extractive", "local"}:
        return ExtractiveAnswerProvider()
    if name in {"claude", "anthropic"}:
        from app.providers.claude_answering import ClaudeAnswerProvider

        return ClaudeAnswerProvider()
    if name == "ollama":
        from app.providers.ollama import OllamaAnswerProvider

        return OllamaAnswerProvider()
    raise ValueError(f"Unsupported answer provider: {name}")


def get_chat_provider() -> ChatProvider:
    name = get_settings().chat_provider.lower()
    if name in {"extractive", "local"}:
        return ExtractiveChatProvider()
    if name in {"claude", "anthropic"}:
        from app.providers.claude_chat import ClaudeChatProvider

        return ClaudeChatProvider()
    if name == "ollama":
        from app.providers.ollama import OllamaChatProvider

        return OllamaChatProvider()
    raise ValueError(f"Unsupported chat provider: {name}")


def get_graph_extractor() -> GraphExtractor | None:
    name = get_settings().graph_provider.lower()
    if name == "none":
        return None
    if name in {"local", "rules"}:
        from app.providers.graph_local import RuleGraphExtractor

        return RuleGraphExtractor()
    if name in {"claude", "anthropic"}:
        from app.providers.graph_models import ClaudeGraphExtractor

        return ClaudeGraphExtractor()
    if name == "ollama":
        from app.providers.graph_models import OllamaGraphExtractor

        return OllamaGraphExtractor()
    raise ValueError(f"Unsupported graph provider: {name}")


def get_summarizer() -> Summarizer | None:
    """Summaries follow GRAPH_PROVIDER, since both are written at indexing time."""
    name = get_settings().graph_provider.lower()
    if name == "none":
        return None
    if name in {"local", "rules"}:
        from app.providers.graph_local import ExtractiveSummarizer

        return ExtractiveSummarizer()
    if name in {"claude", "anthropic"}:
        from app.providers.graph_models import ClaudeSummarizer

        return ClaudeSummarizer()
    if name == "ollama":
        from app.providers.graph_models import OllamaSummarizer

        return OllamaSummarizer()
    raise ValueError(f"Unsupported graph provider: {name}")
