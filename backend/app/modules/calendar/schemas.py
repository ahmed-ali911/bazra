from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class CalendarEventCreate(BaseModel):
    title: str
    description: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    life_area_id: int | None = None

    @model_validator(mode="after")
    def _validate_range(self) -> "CalendarEventCreate":
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("ends_at must be >= starts_at")
        return self


class CalendarEventUpdate(BaseModel):
    """All fields optional, applied with exclude_unset=True — same partial-
    update convention as TaskUpdate. ends_at >= starts_at is validated in
    the service layer against the MERGED result, not here, since a partial
    payload may only include one of the two fields.
    """

    title: str | None = None
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    life_area_id: int | None = None


class CalendarEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    life_area_id: int | None
    created_at: datetime
    updated_at: datetime


class AgendaItem(BaseModel):
    """The normalized shape for the agenda read-model — preserves source
    identity (source + the SOURCE entity's own id, never a synthetic
    combined id) so a future UI can route back to the correct owning
    endpoint. ends_at is always null for source="task" — tasks have no
    end time, only a due_at (mapped to starts_at here).
    """

    source: Literal["event", "task"]
    id: int
    title: str
    starts_at: datetime
    ends_at: datetime | None
    life_area_id: int | None
