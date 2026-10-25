"""Retrieval chunks, built from a source's located evidence.

Every source is first reduced to an ordered list of located passages: a document's segments as its
processor produced them, or, for a video, its timed transcript, its on-screen text, and its visual
description. Passages are then packed into chunks of at most CHUNK_MAX_CHARACTERS, and each chunk
starts with the last CHUNK_OVERLAP_CHARACTERS of the one before, so a sentence that straddles a
boundary is still retrievable whole.

Packing never crosses a stream (transcript and on-screen text are separate evidence) or a heading
(a section is a unit of meaning), but it does cross pages and time windows; the chunk's locator
then spans them, as "pp. 3-4" or "12:30-13:45".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.models.source import Source

SUMMARY_CHUNK_KEY = "summary"


@dataclass(frozen=True)
class Passage:
    stream: str
    text: str
    locator: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    chunk_key: str
    content_type: str | None
    text: str
    locator: dict[str, Any]
    position: int


def source_passages(source: Source) -> list[Passage]:
    """The source's evidence as ordered, located passages, whichever pipeline produced it."""
    passages = [
        Passage(segment.modality, segment.text.strip(), dict(segment.locator or {}))
        for segment in source.segments
        if segment.text.strip()
    ]
    if source.transcript and source.transcript.text.strip():
        timed = [item for item in source.transcript.segments or [] if str(item.get("text", "")).strip()]
        if timed:
            passages += [
                Passage(
                    "transcript",
                    str(item["text"]).strip(),
                    {"start": float(item.get("start", 0.0)), "end": float(item.get("end", item.get("start", 0.0)))},
                )
                for item in timed
            ]
        else:
            passages.append(Passage("transcript", source.transcript.text.strip()))
    passages += _ocr_spans(source)
    if source.visual_analysis and source.visual_analysis.description.strip():
        passages.append(Passage("visual", source.visual_analysis.description.strip()))
    return passages


def _ocr_spans(source: Source) -> list[Passage]:
    """On-screen text, with a caption that stays up across frames collapsed into one timed span."""
    spans: list[Passage] = []
    for result in sorted(source.ocr_results, key=lambda item: (item.timestamp_seconds or 0.0, item.id or 0)):
        text = result.text.strip()
        if not text:
            continue
        moment = result.timestamp_seconds
        previous = spans[-1] if spans else None
        if previous is not None and previous.text == text:
            locator = dict(previous.locator)
            if moment is not None:
                locator["end"] = moment
            spans[-1] = Passage("ocr", text, locator)
            continue
        spans.append(Passage("ocr", text, {"start": moment, "end": moment} if moment is not None else {}))
    return spans


def build_chunks(source: Source) -> list[Chunk]:
    """All retrieval chunks for a source: packed evidence, extracted records, and a summary.

    The summary chunk exists for whole-source comparisons such as near-duplicate detection. It
    repeats evidence the other chunks already hold, so retrieval does not use it.
    """
    settings = get_settings()
    chunks: list[Chunk] = []
    counters: dict[str, int] = {}
    for stream, group in _groups(source_passages(source)):
        for text, locator in _pack(group, settings.chunk_max_characters, settings.chunk_overlap_characters):
            counters[stream] = counters.get(stream, 0) + 1
            chunks.append(Chunk(f"{stream}:{counters[stream]}", stream, text, locator, len(chunks) + 1))

    for item in source.extracted_content:
        text = (item.text_representation or "").strip()
        if text:
            chunks.append(Chunk(f"extracted:{item.content_type}", item.content_type, text, {}, len(chunks) + 1))

    summary = "\n".join(
        part
        for part in [
            f"Title: {source.title}" if source.title else "",
            f"Author: {source.author}" if source.author else "",
            f"Description: {source.description}" if source.description else "",
            *(chunk.text for chunk in chunks),
        ]
        if part
    ).strip()
    if summary:
        chunks.append(Chunk(SUMMARY_CHUNK_KEY, SUMMARY_CHUNK_KEY, summary[:4000], {}, len(chunks) + 1))
    return chunks


def _groups(passages: list[Passage]) -> list[tuple[str, list[Passage]]]:
    """Runs of consecutive passages that may be packed together: same stream, same section."""
    groups: list[tuple[str, list[Passage]]] = []
    for passage in passages:
        if groups:
            stream, group = groups[-1]
            if stream == passage.stream and group[-1].locator.get("section") == passage.locator.get("section"):
                group.append(passage)
                continue
        groups.append((passage.stream, [passage]))
    return groups


def _pack(group: list[Passage], max_characters: int, overlap: int) -> list[tuple[str, dict[str, Any]]]:
    pieces: list[Passage] = []
    for passage in group:
        pieces += [Passage(passage.stream, text, passage.locator) for text in _windows(passage.text, max_characters, overlap)]

    chunks: list[tuple[str, dict[str, Any]]] = []
    current: list[Passage] = []
    size = 0
    for piece in pieces:
        if current and size + len(piece.text) + 1 > max_characters:
            chunks.append(_close(current))
            tail = _tail(current[-1].text, overlap)
            # The overlap is carried only when it leaves room; otherwise it would exceed the limit.
            current = [Passage(piece.stream, tail, current[-1].locator)] if tail and len(tail) + len(piece.text) + 1 <= max_characters else []
            size = len(tail) if current else 0
        current.append(piece)
        size += len(piece.text) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append(_close(current))
    return chunks


def _close(parts: list[Passage]) -> tuple[str, dict[str, Any]]:
    separator = "\n\n" if parts[0].stream == "text" else " " if parts[0].stream == "transcript" else "\n"
    return separator.join(part.text for part in parts), merge_locators([part.locator for part in parts])


def _windows(text: str, max_characters: int, overlap: int) -> list[str]:
    """Split one overlong passage into overlapping windows that end at word boundaries."""
    if len(text) <= max_characters:
        return [text]
    windows: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_characters)
        if end < len(text):
            boundary = text.rfind(" ", start + max_characters // 2, end)
            end = boundary if boundary > start else end
        windows.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = max(end - overlap, start + 1)
        space = text.find(" ", next_start, end)
        start = space + 1 if space != -1 else next_start
    return [window for window in windows if window]


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :].strip() if space != -1 else tail.strip()


def merge_locators(locators: list[dict[str, Any]]) -> dict[str, Any]:
    """One locator spanning several: first-to-last page, earliest start to latest end."""
    merged: dict[str, Any] = {}
    pages = [locator["page"] for locator in locators if locator.get("page") is not None]
    if pages:
        merged["page"] = min(pages)
        if max(pages) != min(pages):
            merged["page_end"] = max(pages)
    starts = [locator["start"] for locator in locators if locator.get("start") is not None]
    ends = [locator["end"] for locator in locators if locator.get("end") is not None]
    if starts:
        merged["start"] = min(starts)
        merged["end"] = max(ends) if ends else max(starts)
    for key in ("section", "paragraph", "region"):
        value = next((locator[key] for locator in locators if locator.get(key) is not None), None)
        if value is not None:
            merged[key] = value
    return merged


def describe_locator(locator: dict[str, Any] | None) -> str | None:
    """A short human label for where a passage is: "p. 4", "pp. 3-4", "12:30-13:45", "§ Setup"."""
    if not locator:
        return None
    parts: list[str] = []
    if locator.get("page") is not None:
        end = locator.get("page_end")
        parts.append(f"pp. {locator['page']}–{end}" if end else f"p. {locator['page']}")
    if locator.get("start") is not None:
        start, end = _clock(locator["start"]), _clock(locator.get("end", locator["start"]))
        parts.append(start if start == end else f"{start}–{end}")
    if locator.get("section"):
        parts.append(f"§ {locator['section']}")
    elif locator.get("paragraph") is not None and not parts:
        parts.append(f"¶ {locator['paragraph']}")
    if locator.get("region") == "image" and not parts:
        parts.append("image")
    return " · ".join(parts) or None


def _clock(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
