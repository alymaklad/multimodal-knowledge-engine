"""Durable media storage, separate from the working files the processors read.

FFmpeg, Tesseract, and the vision model all need real local files, so the object store is not a
drop-in replacement for `MEDIA_ROOT`: it is the durable copy. Working files stay on disk for the
duration of processing; published objects are what survives, what gets served, and what a second
worker on another host can fetch.

Layout follows architecture section 8:

    users/{user_id}/sources/{source_id}/video.mp4
    users/{user_id}/sources/{source_id}/audio.wav
    users/{user_id}/sources/{source_id}/frames/frame_00001.jpg
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.config import get_settings

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".wav": "audio/wav",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


@dataclass(frozen=True)
class StoredObjectInfo:
    key: str
    size_bytes: int
    content_type: str
    backend: str


def source_prefix(user_id: str, source_id: int) -> str:
    return f"users/{user_id}/sources/{source_id}"


def object_key(user_id: str, source_id: int, name: str) -> str:
    return f"{source_prefix(user_id, source_id)}/{name}"


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


class ObjectStore(ABC):
    """Storage boundary so S3 or MinIO replaces the local backend without touching callers."""

    backend: str

    @abstractmethod
    def put(self, local_path: Path, key: str) -> StoredObjectInfo: ...

    @abstractmethod
    def open(self, key: str) -> BinaryIO: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int: ...

    @abstractmethod
    def signed_url(self, key: str, expires_in: int | None = None) -> str | None:
        """A time-limited URL, or None when the backend cannot issue one."""


class LocalObjectStore(ObjectStore):
    """Filesystem backend using the same key layout, so keys are portable to S3 unchanged."""

    backend = "local"

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root or get_settings().object_store_root)

    def _resolve(self, key: str) -> Path:
        target = (self._root / key).resolve()
        root = self._root.resolve()
        # Keys are built internally, but a traversal here would write outside the store.
        if not target.is_relative_to(root):
            raise ValueError(f"Object key escapes the store root: {key}")
        return target

    def put(self, local_path: Path, key: str) -> StoredObjectInfo:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, target)
        return StoredObjectInfo(
            key=key,
            size_bytes=target.stat().st_size,
            content_type=content_type_for(target),
            backend=self.backend,
        )

    def open(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def delete_prefix(self, prefix: str) -> int:
        directory = self._resolve(prefix)
        if not directory.is_dir():
            return 0
        removed = 0
        for child in sorted(directory.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
                removed += 1
            elif child.is_dir():
                child.rmdir()
        directory.rmdir()
        return removed

    def signed_url(self, key: str, expires_in: int | None = None) -> str | None:
        # No signing key exists locally; the API streams these objects through its own auth instead.
        return None


class S3ObjectStore(ObjectStore):
    """S3 or any S3-compatible service such as MinIO, per architecture section 8."""

    backend = "s3"

    def __init__(self, client=None) -> None:  # pragma: no cover - requires a bucket
        settings = get_settings()
        if not settings.s3_bucket:
            raise ValueError("Set S3_BUCKET to use the S3 object store")
        self._bucket = settings.s3_bucket
        self._prefix = settings.s3_prefix.strip("/")
        self._ttl = settings.signed_url_ttl_seconds
        if client is not None:
            self._client = client
        else:
            try:
                import boto3
            except ImportError as error:
                raise RuntimeError(
                    "Install boto3 to enable S3 object storage, or set OBJECT_STORE=local"
                ) from error
            self._client = boto3.client(
                "s3", endpoint_url=settings.s3_endpoint_url or None, region_name=settings.s3_region or None
            )

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def put(self, local_path: Path, key: str) -> StoredObjectInfo:  # pragma: no cover - requires a bucket
        content_type = content_type_for(local_path)
        self._client.upload_file(
            str(local_path), self._bucket, self._full_key(key), ExtraArgs={"ContentType": content_type}
        )
        return StoredObjectInfo(
            key=key,
            size_bytes=local_path.stat().st_size,
            content_type=content_type,
            backend=self.backend,
        )

    def open(self, key: str) -> BinaryIO:  # pragma: no cover - requires a bucket
        return self._client.get_object(Bucket=self._bucket, Key=self._full_key(key))["Body"]

    def delete_prefix(self, prefix: str) -> int:  # pragma: no cover - requires a bucket
        paginator = self._client.get_paginator("list_objects_v2")
        removed = 0
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._full_key(prefix)):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
                removed += len(keys)
        return removed

    def signed_url(self, key: str, expires_in: int | None = None) -> str | None:  # pragma: no cover
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": self._full_key(key)},
            ExpiresIn=expires_in or self._ttl,
        )


def get_object_store() -> ObjectStore:
    name = get_settings().object_store.lower()
    if name == "local":
        return LocalObjectStore()
    if name in {"s3", "minio"}:
        return S3ObjectStore()
    raise ValueError(f"Unsupported object store: {name}")
