from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InboxItemUpdate(BaseModel):
    """The only client-editable transition in this checkpoint: marking an
    item read. title/task_id are set at generation time and never
    client-supplied; dismissal is its own endpoint (DELETE), not a field
    here, matching Task/CalendarEvent's delete-is-a-separate-verb
    convention.
    """

    read: bool


class InboxItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    task_id: int | None
    read_at: datetime | None
    created_at: datetime
    updated_at: datetime
