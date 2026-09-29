from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Checkpoint 4.2 — the locked v1 signal set. Memory is deliberately
# excluded (Architecture Contract 4.0b): every other future signal type
# is a distinct, later architectural decision, not an oversight here.
SignalType = Literal[
    "TASK_OVERDUE",
    "TASK_DUE_TODAY",
    "TASK_DUE_SOON",
    "EVENT_UPCOMING",
    "INBOX_NEEDS_ATTENTION",
]

SourceType = Literal["task", "calendar_event", "inbox_item"]

# Fixed, documented technical ordering for generate_signals' returned list —
# NOT an urgency order (that is Checkpoint 4.3's job). Signals are grouped
# by type in this order, then by source_id within each type.
SIGNAL_TYPE_ORDER: tuple[SignalType, ...] = (
    "TASK_OVERDUE",
    "TASK_DUE_TODAY",
    "TASK_DUE_SOON",
    "EVENT_UPCOMING",
    "INBOX_NEEDS_ATTENTION",
)


@dataclass(frozen=True)
class Signal:
    """A deterministic fact about current Task/CalendarEvent/InboxItem
    state — never a message, notification, persisted row, priority
    decision, recommendation, or LLM judgment. Nothing in this shape is
    persisted (Checkpoint 4.2 §13); every Signal is derived fresh, on
    every call, from live domain state.

    Deliberately carries no score/rank/surfaced-state/LLM prose — those
    are Checkpoint 4.3+ concerns layered ON TOP of a Signal, never
    inside one (§2).

    measurement_seconds is a single raw, signed-by-convention duration
    whose meaning depends on signal_type — never a precomputed score:
      - TASK_OVERDUE: seconds since due_at (positive)
      - TASK_DUE_TODAY / TASK_DUE_SOON: seconds until due_at (>= 0)
      - EVENT_UPCOMING: seconds until starts_at (>= 0)
      - INBOX_NEEDS_ATTENTION: seconds since created_at (age, >= 0)

    priority is only ever populated for task-sourced signals (None for
    calendar_event/inbox_item sources) — carrying it here lets
    Checkpoint 4.3's scoring engine read it directly off the Signal
    without re-querying Task.

    snapshot is the exact relevant-state snapshot later reused by
    Checkpoint 4.4's dismiss-invalidation comparison — a narrow dict of
    only the fields that matter to THIS signal's own predicate, using
    the canonical timestamp serialization from
    service.py's _canonical_timestamp (never a bare updated_at, per the
    4.0b-R1 correction). Structural equality only — never fuzzy time
    comparison.
    """

    signal_type: SignalType
    source_type: SourceType
    source_id: int
    title: str
    relevant_timestamp: datetime | None
    priority: Literal["low", "normal", "high"] | None
    measurement_seconds: float
    snapshot: dict


class InvalidTimezoneError(Exception):
    """Raised when the caller-supplied IANA timezone name does not
    resolve via zoneinfo — a controlled domain error, never a silent
    UTC fallback (Architecture Contract 4.0b-R1 correction #1: Attention's
    own boundary derives local-day semantics server-side from ONLY a
    validated IANA name)."""

    def __init__(self, timezone_name: str):
        self.timezone_name = timezone_name
        super().__init__(f"Invalid timezone: {timezone_name}")
