"""Splitting text into segments at headings and paragraphs.

Markdown is the common format: DOCX documents and web pages are converted to it, so one splitter
gives every text source heading-aware segments. Plain text is simply Markdown without headings.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import get_settings
from app.processors.base import ProcessingError, ProcessorResult, SegmentDraft

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def read_text_file(path: str | Path, encoding: str | None = None) -> str:
    try:
        return Path(path).read_bytes().decode(encoding or "utf-8-sig", errors="replace")
    except (OSError, LookupError) as error:
        raise ProcessingError(f"The text could not be read ({type(error).__name__})") from error


def process_text(text: str) -> ProcessorResult:
    segments = split_markdown(text)
    if not segments:
        raise ProcessingError("The source contains no text")
    return ProcessorResult(
        segments=segments, models=["text-splitter-v1"], details={"word_count": len(text.split())}
    )


def split_markdown(text: str, *, base_locator: dict | None = None) -> list[SegmentDraft]:
    """Segments that never cross a heading, each labelled with its heading path.

    A section longer than the size limit is split between paragraphs; the locator then also records
    the number of the section's first paragraph in that segment.
    """
    sections: list[tuple[list[str], list[str]]] = []
    path: list[str] = []
    lines: list[str] = []
    in_fence = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            sections.append((list(path), lines))
            level = len(heading.group(1))
            path = path[: level - 1] + [heading.group(2).strip()]
            lines = []
        else:
            lines.append(line)
    sections.append((list(path), lines))

    segments: list[SegmentDraft] = []
    for heading_path, section_lines in sections:
        paragraphs = _paragraphs(section_lines)
        locator = dict(base_locator or {})
        if heading_path:
            locator["section"] = " > ".join(heading_path)
        for text_part, first_paragraph in pack_paragraphs(paragraphs, get_settings().segment_max_characters):
            part_locator = dict(locator)
            if len(paragraphs) > 1:
                part_locator["paragraph"] = first_paragraph + 1
            # The heading is part of what the passage is about, so it travels with the text.
            body = f"{heading_path[-1]}\n\n{text_part}" if heading_path else text_part
            segments.append(SegmentDraft(text=body, modality="text", locator=part_locator))
    return segments


def pack_paragraphs(paragraphs: list[str], max_characters: int) -> list[tuple[str, int]]:
    """Join consecutive paragraphs into parts of at most `max_characters`.

    Returns each part with the index of its first paragraph. A single paragraph longer than the limit
    is split at sentence boundaries, and a sentence longer than the limit at the limit itself.
    """
    parts: list[tuple[str, int]] = []
    current: list[str] = []
    current_start = 0
    size = 0
    for index, paragraph in enumerate(paragraphs):
        for piece in _fit(paragraph, max_characters):
            added = len(piece) + (2 if current else 0)
            if current and size + added > max_characters:
                parts.append(("\n\n".join(current), current_start))
                current, size = [], 0
            if not current:
                current_start = index
                added = len(piece)
            current.append(piece)
            size += added
    if current:
        parts.append(("\n\n".join(current), current_start))
    return parts


def _paragraphs(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if line.strip():
            buffer.append(line.rstrip())
        elif buffer:
            paragraphs.append("\n".join(buffer).strip())
            buffer = []
    if buffer:
        paragraphs.append("\n".join(buffer).strip())
    return [paragraph for paragraph in paragraphs if paragraph]


def _fit(paragraph: str, max_characters: int) -> list[str]:
    if len(paragraph) <= max_characters:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > max_characters:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:max_characters])
            sentence = sentence[max_characters:]
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_characters:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces
