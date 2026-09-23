from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel, LifeAreaScopedMixin, SpaceScopedMixin


class CalendarEvent(BaseModel, SpaceScopedMixin, LifeAreaScopedMixin):
    """A real persisted calendar-native item (meetings, appointments, blocked
    time) — distinct from Task, which stays the source of truth for task
    data. The agenda read-model (calendar/service.py's build_agenda) merges
    this with Task-with-due_at at query time; nothing here duplicates Task
    data, and Task's own rows are never written to from this module.

    starts_at is required — unlike Task.due_at (optional), a calendar event
    isn't meaningfully "on the calendar" without one. ends_at is optional:
    some events are a clean range, some are a single point in time.

    archived_at: soft delete, for the same concrete future-reference
    integrity reason as Task.archived_at — a future Inbox item could
    reference a CalendarEvent the same way it might reference a Task.
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ck_calendar_events_ends_after_starts"),
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
