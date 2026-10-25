from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.models.source import Source, SourceKind
from app.processors.base import ProcessingError, ProcessorContext, ProcessorResult
from app.processors.documents import process_docx, process_pdf
from app.processors.media import process_audio, process_image, process_youtube_transcript
from app.processors.text import process_text, read_text_file
from app.processors.web import process_html
from app.services.source_types import DOCX_MIME
from app.services.video import FFmpegVideoProcessor

TEXT_TYPES = {"text/plain", "text/markdown"}


def default_context() -> ProcessorContext:
    """Providers chosen by configuration, each built only if a processor asks for it."""
    from app.providers.factory import get_image_describer, get_ocr_provider
    from app.providers.speech import get_speech_provider

    return ProcessorContext(ocr=get_ocr_provider, speech=get_speech_provider, image_describer=get_image_describer)


def process_source(
    source: Source, context: ProcessorContext, *, media_processor: FFmpegVideoProcessor | None = None
) -> ProcessorResult:
    """Run the processor for this source's kind and content type over its stored file.

    Video is not handled here: it has its own multi-stage pipeline of frame sampling and analysis.
    """
    if not source.media_path or not Path(source.media_path).is_file():
        raise ProcessingError("The source's stored file is missing; add it again or reprocess it")
    path = Path(source.media_path)
    mime_type = (source.mime_type or "").split(";")[0].strip().lower()

    if source.kind is SourceKind.youtube:
        return process_youtube_transcript(path)
    if source.kind is SourceKind.image:
        return process_image(path, context)
    if source.kind is SourceKind.audio:
        work_dir = Path(get_settings().media_root) / "prepared" / str(source.id)
        return process_audio(path, work_dir, context, media_processor)
    if source.kind in {SourceKind.document, SourceKind.web, SourceKind.text}:
        if mime_type == "application/pdf":
            return process_pdf(path, context)
        if mime_type == DOCX_MIME:
            return process_docx(path)
        if mime_type in {"text/html", "application/xhtml+xml"}:
            return process_html(path, (source.details or {}).get("charset"))
        if mime_type in TEXT_TYPES or source.kind is SourceKind.text:
            return process_text(read_text_file(path, (source.details or {}).get("charset")))
        raise ProcessingError(f"There is no processor for {mime_type or 'this type of'} content")
    raise ProcessingError(f"{source.kind.value.capitalize()} sources are processed by their own pipeline")
