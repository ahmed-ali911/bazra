from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

TaskStatus = Literal["open", "done"]
TaskPriority = Literal["low", "normal", "high"]


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    due_at: datetime | None = None
    life_area_id: int | None = None
    # Checkpoint 4.1 — defaults to "normal" at the schema boundary itself,
    # not merely at the DB column level, so every caller (REST, chat's own
    # propose_create_task handler) sees the same explicit default whether
    # or not the column-level server_default would also have applied.
    priority: TaskPriority = "normal"


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
    # Checkpoint 4.1 — omitted (the default None) means "leave unchanged",
    # exactly like every other optional field here; there is no "clear
    # priority back to unset" concept the way due_at/life_area_id have,
    # since priority is never nullable on Task itself — a proposal that
    # explicitly names priority="normal" is still a real, visible change
    # (see chat/service.py's own confirmation-rendering distinction).
    priority: TaskPriority | None = None


class ProposedTaskUpdate(TaskUpdate):
    """Checkpoint 3.10 — the model-facing argument shape for
    propose_update_task: TaskUpdate's own optional fields, plus the
    task_id being referenced. Deliberately does NOT add a
    completed_at field — TaskUpdate has none, and this subclasses it
    rather than redeclaring its fields, so there is no parallel
    completion-timestamp path here; completed_at remains derived
    exclusively inside tasks_service.update_task, exactly as the
    direct REST path already behaves. A status="done" proposal carries
    only the status change — never a model-supplied timestamp.

    _require_at_least_one_change rejects a proposal that names a
    task_id but changes nothing — model_fields_set (not the dumped
    values) is checked so an explicitly-provided null still counts as
    a real, intentional change (e.g. clearing due_at).
    """

    task_id: int

    @model_validator(mode="after")
    def _require_at_least_one_change(self) -> "ProposedTaskUpdate":
        if not (self.model_fields_set - {"task_id"}):
            raise ValueError("at least one field to change must be provided")
        return self


class ProposedTaskDelete(BaseModel):
    """Checkpoint 3.13 — the model-facing argument shape for
    propose_delete_task: a stable, explicit task_id only. Deliberately
    minimal — no title, no fuzzy reference, no additional delete
    metadata; task_id is the ONLY thing this proposal ever needs, and
    the only thing tasks_service.delete_task itself takes beyond
    space_id.
    """

    task_id: int


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_at: datetime | None
    completed_at: datetime | None
    life_area_id: int | None
    created_at: datetime
    updated_at: datetime
