"""Checkpoint 4.4c-2 — the pure, deterministic ACTED_ON resolution
evaluator.

Answers exactly one narrow question: "given an exposure's original
signal_type, and the source's own state immediately AFTER a qualifying
mutation, does the concern represented by that original signal still
hold at this mutation-time instant?" It sees only a post-mutation
snapshot plus the mutation's own authoritative `now` — it does NOT
prove the mutation CAUSED the resolution, only that the two are
associated at that instant (state association, never causal credit).

This module is 100% DB-free and has no side effects whatsoever:
- No Session, no SQL, no ORM objects, no commit.
- No datetime.now()/DB now() — `now` is always the caller's own
  explicit, already-resolved instant (same discipline as every other
  Attention module).
- Never writes acted_on_at — Checkpoint 4.4c-3 owns deciding WHEN to
  call this (only after a real qualifying mutation) and persisting
  its result; this module cannot do either.

Not implemented here (later checkpoints/slices own these): any
mutation hook into tasks_service/calendar_service/inbox_service, any
SAVEPOINT/nested-transaction isolation, and the decision of which
exposure (if any) a given mutation should even be evaluated against —
all Checkpoint 4.4c-3.

CONCERN CONTINUITY (locked, conservative by design): a mutation that
transitions an active concern into a MORE SEVERE one is never RESOLVED
merely because the ORIGINAL narrow predicate became false —
TASK_DUE_TODAY/TASK_DUE_SOON transitioning into overdue, or an Event's
starts_at moving into the past, are both NOT_RESOLVED. Future Evolution
metrics must prefer false negatives (under-attribution) over
false-positive "successful attention".
"""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.attention.schemas import (
    ActedOnResolution,
    ActedOnResolutionError,
    EventMutationState,
    InboxMutationState,
    SignalType,
    TaskMutationState,
)
from app.modules.attention.service import (
    _EVENT_UPCOMING_WINDOW,
    _TASK_DUE_SOON_WINDOW,
    _next_local_midnight,
)

_EXPECTED_STATE_TYPE: dict[SignalType, type] = {
    "TASK_OVERDUE": TaskMutationState,
    "TASK_DUE_TODAY": TaskMutationState,
    "TASK_DUE_SOON": TaskMutationState,
    "EVENT_UPCOMING": EventMutationState,
    "INBOX_NEEDS_ATTENTION": InboxMutationState,
}


def _task_lifecycle_resolved(state: TaskMutationState) -> bool:
    return state.status != "open" or state.archived_at is not None


def _task_overdue(state: TaskMutationState, now: datetime) -> ActedOnResolution:
    if _task_lifecycle_resolved(state):
        return "RESOLVED"
    if state.due_at is None:
        return "RESOLVED"
    if state.due_at < now:
        # Still, factually, overdue — "less overdue" is not resolution.
        return "NOT_RESOLVED"
    return "RESOLVED"


def _task_due_today(state: TaskMutationState, now: datetime, timezone_name: str | None) -> ActedOnResolution:
    if _task_lifecycle_resolved(state):
        return "RESOLVED"
    if state.due_at is None:
        return "RESOLVED"

    if timezone_name is None:
        return "UNKNOWN"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return "UNKNOWN"

    if state.due_at < now:
        # Concern-continuity protection: the narrow TASK_DUE_TODAY
        # predicate is technically false, but this is a transition into
        # the MORE SEVERE TASK_OVERDUE concern, never a resolution.
        return "NOT_RESOLVED"

    next_midnight = _next_local_midnight(now, zone)
    if state.due_at < next_midnight:
        return "NOT_RESOLVED"
    return "RESOLVED"


def _task_due_soon(state: TaskMutationState, now: datetime) -> ActedOnResolution:
    if _task_lifecycle_resolved(state):
        return "RESOLVED"
    if state.due_at is None:
        return "RESOLVED"
    if state.due_at < now:
        # Concern-continuity protection: transitioned into TASK_OVERDUE.
        return "NOT_RESOLVED"
    if state.due_at < now + _TASK_DUE_SOON_WINDOW:
        return "NOT_RESOLVED"
    return "RESOLVED"


def _event_upcoming(state: EventMutationState, now: datetime) -> ActedOnResolution:
    if state.archived_at is not None:
        return "RESOLVED"
    if state.starts_at < now:
        # Conservative: no EVENT_OVERDUE concept exists, and a backward
        # move into the past is suspicious, never auto-treated as success.
        return "NOT_RESOLVED"
    if state.starts_at < now + _EVENT_UPCOMING_WINDOW:
        return "NOT_RESOLVED"
    return "RESOLVED"


def _inbox_needs_attention(state: InboxMutationState) -> ActedOnResolution:
    if state.read_at is not None or state.archived_at is not None:
        return "RESOLVED"
    return "NOT_RESOLVED"


def evaluate_acted_on(
    signal_type: SignalType,
    state: TaskMutationState | EventMutationState | InboxMutationState,
    now: datetime,
    timezone_name: str | None = None,
) -> ActedOnResolution:
    """The single public entry point. `state` must be the matching
    *Mutation­State dataclass for `signal_type` (TaskMutationState for
    the three TASK_* types, EventMutationState for EVENT_UPCOMING,
    InboxMutationState for INBOX_NEEDS_ATTENTION) — this is the whole
    of this module's source-type safety mechanism: there is no separate
    `source_type` string parameter to independently get wrong, since a
    Python `isinstance` check on the required dataclass type already
    makes "Event state handed to a Task signal" structurally impossible
    to pass silently. Any mismatch raises ActedOnResolutionError — it
    can never produce RESOLVED (or any other value) by accident.

    `timezone_name` is only consulted for TASK_DUE_TODAY (the one
    signal type whose predicate depends on a local-calendar-day
    boundary) — `None` or an unresolvable name both return UNKNOWN,
    never a guess.
    """
    expected_state_type = _EXPECTED_STATE_TYPE.get(signal_type)
    if expected_state_type is None:
        raise ActedOnResolutionError(f"Unsupported signal_type: {signal_type!r}")
    if not isinstance(state, expected_state_type):
        raise ActedOnResolutionError(
            f"signal_type={signal_type!r} requires {expected_state_type.__name__}, "
            f"got {type(state).__name__}"
        )

    if signal_type == "TASK_OVERDUE":
        return _task_overdue(state, now)
    if signal_type == "TASK_DUE_TODAY":
        return _task_due_today(state, now, timezone_name)
    if signal_type == "TASK_DUE_SOON":
        return _task_due_soon(state, now)
    if signal_type == "EVENT_UPCOMING":
        return _event_upcoming(state, now)
    return _inbox_needs_attention(state)
