from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel, SpaceScopedMixin


class Memory(BaseModel, SpaceScopedMixin):
    """Checkpoint 3.4. Scoped by BOTH space_id (SpaceScopedMixin) AND an
    explicit user_id — the exact ownership pattern ChatMessage's own
    docstring already committed to for Memory ("extending the same
    reasoning already applied to Memory's ownership") and AiTrace's
    docstring anticipated by name ("Memory (Phase 3, later checkpoint),
    which DOES need explicit per-user ownership since it stores personal
    facts that must never leak across users").

    type/content are plain typed columns, not a JSONB blob — unlike
    ProposedAction.arguments (which must hold TaskCreate's several
    optional fields), Memory's shape is small and fixed, so real columns
    are simpler and directly queryable/indexable.

    content is immutable once created: a correction NEVER rewrites this
    row — it creates a new Memory row and flips this one to
    status="superseded" (see superseded_by_id). Memory is not
    append-only truth (two contradictory rows can't both stay "active"
    by design — see memory/service.py's supersede_memory), but it is
    also never mutated in place; "supersede" always means "create new,
    then retire old", matching the same event-sourcing-flavored
    discipline as Task's archived_at soft-delete precedent, generalized
    to support a replacement pointer instead of a bare tombstone.

    No confidence score, no separate `source` provenance enum, no
    life_area_id, no last_used_at: each was evaluated during 3.4's
    architecture review and deferred for having no current consumer in
    this checkpoint's design (the same "no field without a current
    consumer" standard already applied elsewhere in this codebase) —
    each is a trivial additive migration later if a real need appears.
    """

    __tablename__ = "memories"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    source_chat_message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), nullable=False, index=True)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("memories.id"), nullable=True, index=True)
