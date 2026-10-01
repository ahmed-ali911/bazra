from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel, SpaceScopedMixin


class AttentionExposure(BaseModel, SpaceScopedMixin):
    """Checkpoint 4.4a — durable evidence that an AttentionCandidate
    (Checkpoint 4.3) was ACTUALLY DELIVERED to the user through a real
    surface. Selection/ranking alone never creates a row here — only
    history.record_exposure, called by a future surfacing consumer
    after it genuinely delivers something, does. Signal (4.2) and
    AttentionCandidate (4.3) both remain fully live-derived and
    unpersisted; this is the one place Attention state becomes durable.

    Scoped by BOTH space_id (SpaceScopedMixin) and an explicit user_id —
    the same dual-scoping convention ProposedAction/Memory already
    established for personal content.

    Typed nullable FKs (task_id/event_id/inbox_item_id, exactly one
    non-null, CHECK-enforced) rather than a generic (source_type,
    source_id) pair — the same concrete-FK-over-generic-reference
    convention ProposedAction.executed_task_id/executed_memory_id
    already established (see that model's own docstring).

    Column groups, by mutability:

    IMMUTABLE core (write-once at INSERT, the authoritative historical
    fact — never rewritten by anything in this checkpoint):
      task_id / event_id / inbox_item_id, signal_type, surface,
      policy_version, score, reason_codes, exposure_snapshot,
      surfaced_at, timezone_name (Checkpoint 4.4c-1).

    MUTABLE feedback (nullable; Checkpoint 4.4a declares these columns
    as part of the accepted table shape but NEVER writes them — no
    mutation service exists yet):
      snoozed_until (4.4b), dismissed_at (4.4b), acted_on_at (4.4c).

    No separate `dismissed_snapshot` column: a dismiss, once 4.4b
    implements it, is always keyed by this row's own id — the snapshot
    to compare against future suppression is simply THIS row's own
    exposure_snapshot (what was actually shown), never a separately
    accepted client-supplied value.

    BaseModel's own updated_at is ordinary bookkeeping ONLY — it bumps
    on every feedback write the same way it would on any other table,
    but is NEVER itself consulted as attention evidence, a user-action
    signal, dismissal-invalidation evidence, or future learning
    evidence. Only the explicit semantic columns above (surfaced_at,
    snoozed_until, dismissed_at, acted_on_at) carry those meanings —
    see history.load_suppression_states, which never reads updated_at.
    """

    __tablename__ = "attention_exposures"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(task_id, event_id, inbox_item_id) = 1",
            name="ck_attention_exposures_exactly_one_source",
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_events.id"), nullable=True, index=True)
    inbox_item_id: Mapped[int | None] = mapped_column(ForeignKey("inbox_items.id"), nullable=True, index=True)

    signal_type: Mapped[str] = mapped_column(String, nullable=False)
    surface: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    exposure_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    surfaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Checkpoint 4.4c-1 — the validated IANA timezone context that gave
    # TASK_DUE_TODAY (and, in principle, any future local-calendar-day
    # signal) its temporal meaning AT THIS EXPOSURE — exposure-time
    # provenance, never a permanent user preference, never Ahmed's
    # "current location". Nullable because every pre-existing 4.4a/4.4b
    # row has no such provenance at all and is NEVER backfilled with a
    # guessed value — NULL means "historical timezone provenance
    # unavailable", which a future ACTED_ON evaluator must treat as
    # unknown/skip, never a reason to guess. Two exposures for the SAME
    # source may legitimately carry DIFFERENT timezone_name values (the
    # user's own request context at each exposure's own time), so this
    # is deliberately per-row, never looked up from Space/User.
    timezone_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # Declared now (accepted table shape), deliberately never written by
    # 4.4a — see this class's own docstring and history.py's module
    # docstring for exactly which later checkpoint owns each.
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acted_on_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
