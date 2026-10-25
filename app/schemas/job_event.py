from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobEventRead(BaseModel):
    id: int
    job_id: int
    stage: str
    event_type: str
    message: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
