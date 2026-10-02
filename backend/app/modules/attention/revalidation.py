"""Checkpoint 4.5e — Selected-Candidate Revalidation.

Answers exactly one narrow question, for the single already-selected
4.5c winner and nothing else: "is the factual concern this candidate
represents still true, right now, against the real current database
state?" This is deliberately NOT a rerank and NOT a second selection —
it never consults another source, never produces a different winner,
and never re-scores anything. A stale answer here means SILENCE, never
"pick the runner-up" (see attention/surfacing.py's own docstring for
why a cascade to another candidate is explicitly rejected).

Reuses the existing, unmodified, already-accepted
resolution.evaluate_acted_on (Checkpoint 4.4c-2) rather than
duplicating its per-signal-type lifecycle predicates — that function
already answers precisely "given this signal_type and this post-state,
does the original concern still hold," which is exactly this
checkpoint's own question, just asked at a different moment (routine
revalidation before surfacing, rather than after a confirmed mutation).

Each source's own scoped getter (tasks_service.get_task,
calendar_service.get_calendar_event, inbox_service.get_item) already
filters `archived_at IS NULL` and therefore returns None for an
archived/deleted row — that None IS this module's own "stale, source
is gone" signal, handled before evaluate_acted_on is ever reached (that
function has no way to represent "the row doesn't exist anymore" on
its own, since it only ever receives a state snapshot of an existing
row).
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.attention import resolution
from app.modules.attention.schemas import (
    AttentionCandidate,
    EventMutationState,
    InboxMutationState,
    TaskMutationState,
)
from app.modules.calendar import service as calendar_service
from app.modules.inbox import service as inbox_service
from app.modules.tasks import service as tasks_service


def candidate_is_still_valid(
    db: Session, space_id: int, candidate: AttentionCandidate, now: datetime, timezone_name: str
) -> bool:
    """True = the concern still holds, safe to surface. False = stale
    (source gone/resolved) — the caller's only correct response is
    SILENCE, never selecting a different candidate.

    `timezone_name` is the SAME validated IANA name already used for
    this whole evaluation (never re-derived, never defaulted) — only
    consulted by evaluate_acted_on for TASK_DUE_TODAY's own local-day
    boundary, exactly as that function's own contract already
    specifies.
    """
    signal = candidate.signal

    if signal.source_type == "task":
        task = tasks_service.get_task(db, space_id, signal.source_id)
        if task is None:
            return False
        state = TaskMutationState(status=task.status, due_at=task.due_at, archived_at=task.archived_at)
    elif signal.source_type == "calendar_event":
        event = calendar_service.get_calendar_event(db, space_id, signal.source_id)
        if event is None:
            return False
        state = EventMutationState(starts_at=event.starts_at, archived_at=event.archived_at)
    else:
        item = inbox_service.get_item(db, space_id, signal.source_id)
        if item is None:
            return False
        state = InboxMutationState(read_at=item.read_at, archived_at=item.archived_at)

    result = resolution.evaluate_acted_on(signal.signal_type, state, now, timezone_name)
    # RESOLVED: the concern is gone (done/archived/no longer due/read) -> stale.
    # UNKNOWN: only reachable for TASK_DUE_TODAY with a missing/invalid
    # timezone_name, which cannot happen here since this is the same
    # already-validated name the whole evaluation used — kept as an
    # explicit, conservative "not confirmed valid" rather than assumed
    # unreachable.
    # NOT_RESOLVED: the only case that is still genuinely valid.
    return result == "NOT_RESOLVED"
