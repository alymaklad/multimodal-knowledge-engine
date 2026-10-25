from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CorrectionCreate(BaseModel):
    content_type: str = Field(min_length=1, max_length=64)
    field_name: str = Field(min_length=1, max_length=128)
    corrected_value: Any
    note: str | None = Field(default=None, max_length=2_000)


class CorrectionRead(CorrectionCreate):
    id: int
    source_id: int
    previous_value: Any | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
