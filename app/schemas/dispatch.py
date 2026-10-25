from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TaskDispatchRead(BaseModel):
    id: int
    job_id: int
    task_id: str
    task_name: str
    state: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
