from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel, LifeAreaScopedMixin, SpaceScopedMixin


class Task(BaseModel, SpaceScopedMixin, LifeAreaScopedMixin):
    """The first real space-owned domain entity (Checkpoint 2.2) — source of
    truth for task data. Calendar (2.3) and Inbox (2.4) read from this via
    tasks.service, never duplicate it.

    status is a plain string, not a native Postgres ENUM — validated at the
    Pydantic schema boundary instead, consistent with keeping schema changes
    (adding a status later) a code+migration change, not an ALTER TYPE.

    completed_at/archived_at are persisted, not derived: the service layer
    sets them as a side effect of a status/delete transition — clients never
    supply them directly.

    priority (Checkpoint 4.1, Phase 4's own first domain prerequisite) is
    the same plain-string, code-validated convention as status — a
    server_default so every pre-existing row backfills to "normal" with no
    separate data migration, and a client-settable field like title/due_at
    (never server-derived the way completed_at is). Explicit-only by
    product policy (see chat/service.py's propose_create_task/
    propose_update_task tool descriptions) — that policy is prompt-level
    guidance, not something this column enforces or could enforce.
    """

    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="normal", server_default="normal")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Soft delete: "deleting" a task sets this rather than removing the row,
    # so a future Inbox item referencing this task never dangles. Default
    # queries filter archived_at IS NULL.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
