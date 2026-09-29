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


# ==================================================
# Checkpoint 4.3 — Deterministic Attention Scoring, Dedup & Suppression
# ==================================================

#: The two locked v1 consumer surfaces, each with its own locked
#: threshold (Architecture Contract 4.0b) — scoring.py's
#: _THRESHOLDS maps each to its constant. Neither surface's own
#: endpoint is implemented yet (that is Phase 4.5+); this only exists
#: so the pure ranking engine can select a threshold policy.
AttentionSurface = Literal["app_opened", "daily_brief"]

#: The four machine-readable suppression outcomes, in the LOCKED
#: precedence order scoring.py's evaluate_gates/rank_for_surface
#: apply them in (§20). Never prose — these are for programmatic
#: consumers/audit, not display text.
SuppressionReasonCode = Literal["SNOOZED", "DISMISSED_UNCHANGED", "COOLDOWN", "BELOW_THRESHOLD"]


class ScoringError(Exception):
    """Raised when a Signal's own data is structurally impossible given
    the Checkpoint 4.2 Signal contract (e.g. a task-sourced signal with
    no priority, a negative measurement where the contract guarantees
    non-negative, an unrecognized signal_type, or a signal_type/
    source_type combination that cannot legitimately occur) — scoring
    fails loudly rather than silently producing a nonsense score (§10).
    Never raised for the LOCKED formula caps themselves (e.g. the
    overdue/inbox-age min() ceilings) — those are policy, not
    corruption, and are applied unconditionally.
    """


@dataclass(frozen=True)
class ScoreComponent:
    """One deterministic, audit-friendly line item behind a Candidate's
    total score — e.g. ScoreComponent("TASK_OVERDUE_BASE", None, 50) or
    ScoreComponent("OVERDUE_DAYS", 5, 25). `value` is the raw input the
    component was computed from (a priority string, a day/hour count,
    etc.) purely for explainability — the score itself is
    `score_delta`, never re-derived from `value` by a reader. Never
    holds LLM prose (§2).
    """

    code: str
    value: str | int | float | None
    score_delta: int


@dataclass(frozen=True)
class GateResult:
    """The outcome of evaluating the suppression gates (snooze / dismiss
    / cooldown) plus, when applied by rank_for_surface, the surface's
    own threshold — exactly one reason_code when suppressed (the FIRST
    one that matched, per the locked §20 precedence), never a
    combination. suppressed=False, reason_code=None means fully
    eligible.
    """

    suppressed: bool
    reason_code: SuppressionReasonCode | None


@dataclass(frozen=True)
class SuppressionState:
    """The smallest deterministic read-model input the pure suppression
    gates need for one (source_type, source_id) identity — Checkpoint
    4.4 owns constructing this FROM its own attention_feedback table;
    nothing here is itself persisted, queried, or written by this
    module (§15). All fields default to unset/None, meaning "no
    feedback recorded yet for this source" — evaluate_gates then
    returns a fully-eligible GateResult.

    acted_on_at is accepted here for completeness/future audit use, but
    is deliberately NEVER consulted by evaluate_gates — per the locked
    §19 contract, a historical acted-on exposure does not itself
    suppress a new, current Signal; only snooze/dismiss/cooldown do.
    """

    surfaced_at: datetime | None = None
    snoozed_until: datetime | None = None
    dismissed_at: datetime | None = None
    dismissed_snapshot: dict | None = None
    acted_on_at: datetime | None = None


@dataclass(frozen=True)
class AttentionCandidate:
    """The immutable output of scoring one deduped Signal. `suppression`
    is None until a surface-scoped ranking pass (rank_for_surface)
    evaluates it — a candidate produced by score_signal/dedup alone
    always carries suppression=None, never a guessed/default eligible
    value, so "not yet evaluated" is never confused with "evaluated and
    found eligible."
    """

    signal: Signal
    score: int
    reason_codes: tuple[ScoreComponent, ...]
    suppression: GateResult | None = None
