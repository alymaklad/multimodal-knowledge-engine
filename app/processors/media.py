"""Transcripts (YouTube captions, transcribed audio) and standalone images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.processors.base import ProcessingError, ProcessorContext, ProcessorResult, SegmentDraft
from app.services.video import FFmpegVideoProcessor, VideoProcessingError


def window_transcript(timed: list[dict[str, Any]]) -> list[SegmentDraft]:
    """Group timed transcript lines into windows, each citing the span of time it covers.

    A window closes once it spans TRANSCRIPT_WINDOW_SECONDS or reaches the segment size limit, so a
    citation points at roughly a minute of the recording rather than one caption line or the whole
    hour. Each line needs `text` and `start`, plus `end` or `duration`.
    """
    settings = get_settings()
    segments: list[SegmentDraft] = []
    texts: list[str] = []
    window_start = window_end = 0.0
    size = 0
    for line in timed:
        text = " ".join(str(line.get("text", "")).split())
        if not text:
            continue
        start = float(line.get("start", 0.0))
        end = float(line["end"]) if "end" in line else start + float(line.get("duration", 0.0))
        too_long = texts and (end - window_start > settings.transcript_window_seconds)
        too_large = texts and size + len(text) + 1 > settings.segment_max_characters
        if too_long or too_large:
            segments.append(_window(texts, window_start, window_end))
            texts, size = [], 0
        if not texts:
            window_start = start
        texts.append(text)
        size += len(text) + 1
        window_end = max(window_end, end)
    if texts:
        segments.append(_window(texts, window_start, window_end))
    return segments


def _window(texts: list[str], start: float, end: float) -> SegmentDraft:
    return SegmentDraft(
        text=" ".join(texts), modality="transcript", locator={"start": round(start, 2), "end": round(end, 2)}
    )


def process_youtube_transcript(path: str | Path) -> ProcessorResult:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProcessingError("The stored transcript could not be read; fetch the video again") from error
    segments = window_transcript(payload.get("segments", []))
    if not segments:
        raise ProcessingError("The video's transcript is empty")
    return ProcessorResult(
        segments=segments,
        models=["youtube-captions"],
        details={"language": payload.get("language")},
        duration=max((segment.locator["end"] for segment in segments), default=None),
    )


def process_audio(
    path: str | Path,
    output_directory: Path,
    context: ProcessorContext,
    media_processor: FFmpegVideoProcessor | None = None,
) -> ProcessorResult:
    speech = context.speech
    if speech is None:
        raise ProcessingError("Audio needs a speech model: set SPEECH_PROVIDER=faster_whisper to transcribe it")
    try:
        audio_path, duration = (media_processor or FFmpegVideoProcessor()).extract_audio(path, output_directory)
    except VideoProcessingError as error:
        raise ProcessingError(str(error)) from error
    result = speech.transcribe(Path(audio_path))
    timed = result.segments or ([{"text": result.text, "start": 0.0, "end": duration or 0.0}] if result.text else [])
    segments = window_transcript(timed)
    if not segments:
        raise ProcessingError("No speech was found in this recording")
    return ProcessorResult(
        segments=segments, models=[result.model], details={"language": result.language}, duration=duration
    )


def process_image(path: str | Path, context: ProcessorContext) -> ProcessorResult:
    """Text read from the image plus, when a vision model is configured, a description of it.

    Either one alone is enough. An image with neither has nothing searchable, which is reported
    rather than stored as an empty source.
    """
    image_path = Path(path)
    segments: list[SegmentDraft] = []
    warnings: list[str] = []
    models: list[str] = []

    ocr_text = ""
    try:
        observations = context.ocr.extract([image_path])
        ocr_text = "\n".join(observation.text for observation in observations if observation.text.strip())
        models.append(type(context.ocr).__name__)
    except Exception as error:
        warnings.append(f"OCR failed: {error or type(error).__name__}")
    if ocr_text.strip():
        segments.append(SegmentDraft(text=ocr_text.strip(), modality="ocr", locator={"region": "image"}))

    describer = context.image_describer
    if describer is not None:
        try:
            description = describer.describe(image_path, ocr_text)
            if description.text.strip():
                segments.append(
                    SegmentDraft(text=description.text.strip(), modality="visual", locator={"region": "image"})
                )
                models.append(description.model)
        except Exception as error:
            warnings.append(f"Image description failed: {error or type(error).__name__}")

    if not segments:
        hint = "" if describer is not None else "; set VISION_PROVIDER=claude to describe images"
        raise ProcessingError(f"No text or description could be extracted from this image{hint}")
    return ProcessorResult(segments=segments, models=models, warnings=warnings)
