from datetime import datetime

from pydantic import BaseModel


class StoredObjectRead(BaseModel):
    id: int
    kind: str
    storage_key: str
    backend: str
    content_type: str
    size_bytes: int
    sequence_number: int | None
    created_at: datetime
    url: str
    signed: bool


class PublishResponse(BaseModel):
    reel_id: int
    backend: str
    published: int
    skipped: int
