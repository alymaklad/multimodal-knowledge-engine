from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10_000)


class CollectionUpdate(CollectionCreate):
    pass


class CollectionRead(CollectionCreate):
    id: int
    user_id: str
    created_at: datetime
    updated_at: datetime
    # Included so a client listing collections does not need one count request per collection.
    reel_count: int = 0

    model_config = ConfigDict(from_attributes=True)
