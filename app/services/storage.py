import codecs
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.services.source_types import (
    TEXT_SUFFIXES,
    FileType,
    UnsupportedFileError,
    check_complete_file,
    check_signature,
    resolve_file_type,
)


@dataclass(frozen=True)
class SavedUpload:
    path: str
    sha256: str
    file_type: FileType | None = None


async def save_uploaded_media(upload: UploadFile) -> SavedUpload:
    """Stream a user-supplied file to a generated path beneath MEDIA_ROOT, verifying its type.

    The type is checked on the first chunk, so a mislabelled file is refused before the rest of it is
    written, and text is decoded as it streams, so it is never held in memory whole.
    """
    try:
        suffix, file_type = resolve_file_type(upload.filename, upload.content_type)
    except UnsupportedFileError as error:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error)) from error

    settings = get_settings()
    media_root = Path(settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    target = media_root / f"{uuid4().hex}{suffix}"
    bytes_written = 0
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")() if suffix in TEXT_SUFFIXES else None
    try:
        with target.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                if bytes_written == 0:
                    check_signature(suffix, chunk)
                bytes_written += len(chunk)
                if bytes_written > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload exceeds the {settings.max_upload_bytes} byte limit",
                    )
                if decoder is not None:
                    _check_text_chunk(decoder, chunk, suffix)
                digest.update(chunk)
                destination.write(chunk)
        if bytes_written == 0:
            raise HTTPException(status_code=422, detail="The uploaded file is empty")
        if decoder is not None:
            _check_text_chunk(decoder, b"", suffix, final=True)
        check_complete_file(suffix, target)
    except UnsupportedFileError as error:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error)) from error
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return SavedUpload(path=str(target), sha256=digest.hexdigest(), file_type=file_type)


def save_pasted_text(text: str) -> SavedUpload:
    """Store pasted text as a UTF-8 file, so every source kind is processed from a file on disk."""
    data = text.encode("utf-8")
    media_root = Path(get_settings().media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    target = media_root / f"{uuid4().hex}.txt"
    target.write_bytes(data)
    return SavedUpload(path=str(target), sha256=hashlib.sha256(data).hexdigest())


def _check_text_chunk(decoder: codecs.IncrementalDecoder, chunk: bytes, suffix: str, *, final: bool = False) -> None:
    if b"\x00" in chunk:
        raise UnsupportedFileError(f"This {suffix} file contains binary data, not text")
    try:
        decoder.decode(chunk, final=final)
    except UnicodeDecodeError as error:
        raise UnsupportedFileError(f"This {suffix} file is not UTF-8 text") from error
