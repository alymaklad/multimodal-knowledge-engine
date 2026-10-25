from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CollectionExportSource(BaseModel):
    id: int
    kind: str
    title: str
    origin_url: str | None
    status: str
    author: str | None
    description: str | None
    extracted_content: list[dict[str, Any]]


class CollectionExport(BaseModel):
    collection_id: int
    name: str
    exported_at: datetime
    sources: list[CollectionExportSource]
