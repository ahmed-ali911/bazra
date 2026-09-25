from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel, SpaceScopedMixin


class ProposedAction(BaseModel, SpaceScopedMixin):
    """A structured, persisted, write-time-generated proposal — separate
    from execution by construction, per Checkpoint 3.3's whole reason
    for existing. Nothing about a row existing here means anything was
    ever mutated in Tasks/CalendarEvents/LifeAreas/InboxItems; only
    actions_service.confirm_and_execute, gated on an explicit user
    confirmation matching THIS row's own state, may ever call
    tasks_service.create_task.

    Scoped by BOTH space_id (SpaceScopedMixin) and an explicit user_id —
    the same dual-scoping reasoning already applied to ChatMessage:
    defense in depth, not relying solely on whatever the Space->User
    ownership fix enforces elsewhere.

    arguments is the ONLY place in this whole system a semi-structured
    JSON blob is stored as domain data, and it is narrowly justified:
    this is ephemeral, short-lived proposal data (not permanent domain
    content), and its shape is ALWAYS validated against a concrete
    Pydantic schema (TaskCreate, for action_type="create_task") before
    this row is ever written — never trusted as raw model output at
    rest.

    status is a plain string (Task.status's own convention, not a
    native DB enum) from a closed, code-level set:
    pending | confirmed | executed | rejected | expired | superseded.
    Deliberately no 'failed' value — see confirm_and_execute's own
    docstring for why a failed execution attempt rolls back to
    'pending' rather than reaching a terminal failure state.

    expires_at bounds how long a "yes" can retroactively execute
    something the user might have forgotten about. executed_task_id is
    set only after real execution — the concrete link
    "verify after execution" checks against.
    """

    __tablename__ = "proposed_actions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source_chat_message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
