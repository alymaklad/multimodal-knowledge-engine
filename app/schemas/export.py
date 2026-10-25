from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CollectionExportReel(BaseModel):
    id: int
    instagram_url: str
    status: str
    creator_username: str | None
    caption: str | None
    extracted_content: list[dict[str, Any]]


class CollectionExport(BaseModel):
    collection_id: int
    name: str
    exported_at: datetime
    reels: list[CollectionExportReel]
