from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

TaskStatus = Literal["open", "done"]


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    due_at: datetime | None = None
    life_area_id: int | None = None


class TaskUpdate(BaseModel):
    """All fields optional — the router/service apply this with
    exclude_unset=True so an omitted field stays unchanged, while a field
    explicitly sent as null clears it. Status transitions are handled here
    too; completed_at is never client-supplied, only derived server-side
    from a status change.
    """

    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    due_at: datetime | None = None
    life_area_id: int | None = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: TaskStatus
    due_at: datetime | None
    completed_at: datetime | None
    life_area_id: int | None
    created_at: datetime
    updated_at: datetime
