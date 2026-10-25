import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings

ALLOWED_VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}


@dataclass(frozen=True)
class SavedUpload:
    path: str
    sha256: str


async def save_uploaded_media(upload: UploadFile) -> SavedUpload:
    """Stream user-provided media to a generated path beneath MEDIA_ROOT."""
    suffix = Path(upload.filename or "").suffix.lower()
    if upload.content_type not in ALLOWED_VIDEO_CONTENT_TYPES and suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload a supported video file (.mp4, .mov, or .webm)")

    settings = get_settings()
    media_root = Path(settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    target = media_root / f"{uuid4().hex}{suffix or '.mp4'}"
    bytes_written = 0
    digest = hashlib.sha256()
    try:
        with target.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                bytes_written += len(chunk)
                if bytes_written > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload exceeds the {settings.max_upload_bytes} byte limit",
                    )
                digest.update(chunk)
                destination.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return SavedUpload(path=str(target), sha256=digest.hexdigest())
