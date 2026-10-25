from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.job import JobStatus


class JobRead(BaseModel):
    id: int
    reel_id: int
    status: JobStatus
    current_stage: str
    attempt_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
