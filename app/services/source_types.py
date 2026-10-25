"""Which files can become sources, and how a file proves it is what its name claims.

The extension decides the kind, because the browser-supplied content type is whatever the client
says it is. The file's leading bytes then have to match that extension: a renamed executable must not
reach a PDF parser, and a mislabelled file should fail at upload with a clear message rather than deep
inside a processor.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.models.source import SourceKind

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class FileType:
    kind: SourceKind
    mime_type: str
    label: str


FILE_TYPES: dict[str, FileType] = {
    ".pdf": FileType(SourceKind.document, "application/pdf", "PDF"),
    ".docx": FileType(SourceKind.document, DOCX_MIME, "Word document"),
    ".txt": FileType(SourceKind.document, "text/plain", "text"),
    ".md": FileType(SourceKind.document, "text/markdown", "Markdown"),
    ".markdown": FileType(SourceKind.document, "text/markdown", "Markdown"),
    ".png": FileType(SourceKind.image, "image/png", "PNG image"),
    ".jpg": FileType(SourceKind.image, "image/jpeg", "JPEG image"),
    ".jpeg": FileType(SourceKind.image, "image/jpeg", "JPEG image"),
    ".webp": FileType(SourceKind.image, "image/webp", "WebP image"),
    ".mp3": FileType(SourceKind.audio, "audio/mpeg", "MP3 audio"),
    ".wav": FileType(SourceKind.audio, "audio/wav", "WAV audio"),
    ".m4a": FileType(SourceKind.audio, "audio/mp4", "M4A audio"),
    ".ogg": FileType(SourceKind.audio, "audio/ogg", "Ogg audio"),
    ".flac": FileType(SourceKind.audio, "audio/flac", "FLAC audio"),
    ".mp4": FileType(SourceKind.video, "video/mp4", "MP4 video"),
    ".mov": FileType(SourceKind.video, "video/quicktime", "QuickTime video"),
    ".webm": FileType(SourceKind.video, "video/webm", "WebM video"),
}

TEXT_SUFFIXES = {".txt", ".md", ".markdown"}

# For an upload with no usable extension, the declared type picks one. Content is still verified.
_SUFFIX_FOR_MIME = {file_type.mime_type: suffix for suffix, file_type in reversed(FILE_TYPES.items())}
_SUFFIX_FOR_MIME.update({"audio/x-wav": ".wav", "audio/wave": ".wav", "audio/x-m4a": ".m4a", "audio/mp3": ".mp3"})

# ISO base media files (MP4, M4A, MOV) carry a box type at offset 4. Older QuickTime files can start
# with a box other than "ftyp".
_ISO_BOXES = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"}


class UnsupportedFileError(ValueError):
    """The file is not one the library accepts, or its content contradicts its name."""


def accepted_suffixes() -> list[str]:
    return sorted(FILE_TYPES)


def resolve_file_type(filename: str | None, declared_mime: str | None) -> tuple[str, FileType]:
    """Pick the file type from the extension, falling back to the declared type."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in FILE_TYPES:
        suffix = _SUFFIX_FOR_MIME.get((declared_mime or "").split(";")[0].strip().lower(), suffix)
    if suffix not in FILE_TYPES:
        raise UnsupportedFileError(
            f"Unsupported file type{f' {suffix}' if suffix else ''}. Supported: {', '.join(accepted_suffixes())}"
        )
    return suffix, FILE_TYPES[suffix]


def check_signature(suffix: str, head: bytes) -> None:
    """Reject a file whose first bytes do not match its extension. Text has no signature."""
    if suffix in TEXT_SUFFIXES:
        return
    if not _signature_matches(suffix, head):
        raise UnsupportedFileError(f"The file's content does not match its {suffix} extension")


def check_complete_file(suffix: str, path: Path) -> None:
    """Checks that need the whole file, run once it is on disk. Text is checked while streaming."""
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise UnsupportedFileError("This .docx file has no document body")
        except zipfile.BadZipFile as error:
            raise UnsupportedFileError("This .docx file is damaged") from error


def _signature_matches(suffix: str, head: bytes) -> bool:
    if suffix == ".pdf":
        # The header may follow a few junk bytes; readers search the first kilobyte for it.
        return b"%PDF-" in head[:1024]
    if suffix == ".docx":
        return head.startswith(b"PK\x03\x04")
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if suffix == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if suffix == ".mp3":
        return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if suffix in {".mp4", ".m4a", ".mov"}:
        return head[4:8] in _ISO_BOXES
    if suffix == ".ogg":
        return head.startswith(b"OggS")
    if suffix == ".flac":
        return head.startswith(b"fLaC")
    if suffix == ".webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    return False


def describe_accepted_types() -> dict[str, list[str]]:
    """Accepted extensions grouped by source kind, for the capabilities endpoint."""
    grouped: dict[str, list[str]] = {}
    for suffix, file_type in FILE_TYPES.items():
        grouped.setdefault(file_type.kind.value, []).append(suffix)
    return grouped
