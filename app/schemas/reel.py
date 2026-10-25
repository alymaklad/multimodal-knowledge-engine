from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.models.reel import ReelStatus


class ReelCreate(BaseModel):
    collection_id: int = Field(gt=0)
    instagram_url: AnyHttpUrl

    @field_validator("instagram_url")
    @classmethod
    def require_instagram_reel(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.host not in {"instagram.com", "www.instagram.com"} or "/reel/" not in value.path:
            raise ValueError("instagram_url must be an Instagram Reel URL")
        return value


class ReelRead(BaseModel):
    id: int
    collection_id: int
    instagram_url: str
    creator_username: str | None
    creator_name: str | None
    caption: str | None
    duration: float | None
    media_path: str | None
    status: ReelStatus
    processing_version: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReelIngestionResponse(BaseModel):
    reel_id: int
    status: ReelStatus
    message: str


class ReelMetadataUpdate(BaseModel):
    creator_username: str | None = Field(default=None, max_length=255)
    creator_name: str | None = Field(default=None, max_length=255)
    caption: str | None = Field(default=None, max_length=10_000)
