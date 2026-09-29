"""Checkpoint 4.2 — the deterministic Attention Signal Engine.

Derives Signals fresh from live Task/CalendarEvent/InboxItem state.
Zero persistence (no Signal table, no migration), zero LLM/Model
Router/Anthropic calls, zero narration. See Signal's own docstring in
schemas.py for what a Signal is and is not.

Everything downstream of generate_signals — scoring, ranking, dedup,
thresholds, cooldown, snooze, dismiss, attention_feedback,
ACTED_ON, APP_OPENED, Daily Brief — is explicitly OUT OF SCOPE here
(Architecture Contract 4.0b / 4.0b-R1 own those checkpoints, not this
one).
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.attention.schemas import SIGNAL_TYPE_ORDER, InvalidTimezoneError, Signal
from app.modules.calendar.models import CalendarEvent
from app.modules.inbox.models import InboxItem
from app.modules.tasks.models import Task

# Locked Phase 4 v1 policy (Architecture Contract 4.0b) — not
# user-configurable in this checkpoint.
_TASK_DUE_SOON_WINDOW = timedelta(hours=4)
_EVENT_UPCOMING_WINDOW = timedelta(hours=2)


def _resolve_zone(timezone_name: str) -> ZoneInfo:
    """The only place Attention trusts a caller-supplied timezone
    string — validated via real zoneinfo, never a silent UTC fallback.
    An unresolvable name is a controlled InvalidTimezoneError, exactly
    the same convention chat/service.py and weather/service.py already
    each independently established for their own callers."""
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTimezoneError(timezone_name) from exc


def _next_local_midnight(now: datetime, zone: ZoneInfo) -> datetime:
    """DST-safe: constructed from the LOCAL CALENDAR DATE (date arithmetic,
    never datetime + fixed hours), then combined with a naive midnight
    and the zone attached — the same datetime.combine(date, time(...),
    tzinfo=zone) convention weather/service.py's own period_start/
    period_end construction already uses. This is precisely what
    Architecture Contract 4.0b-R1 correction #1 requires: never a naive
    UTC +timedelta(days=1) boundary, which would be wrong by exactly one
    hour on a DST-transition day.
    """
    local_now = now.astimezone(zone)
    tomorrow_local_date: date = local_now.date() + timedelta(days=1)
    return datetime.combine(tomorrow_local_date, time(0, 0), tzinfo=zone)


def _canonical_timestamp(dt: datetime) -> str:
    """One canonical serialization for every timestamp that ends up in a
    Signal's snapshot or relevant_timestamp-derived fields — always
    normalized to UTC first, so two equivalent instants that merely
    arrived with different UTC-offset representations always serialize
    identically. Checkpoint 4.4's dismiss-invalidation comparison
    (structural equality, never fuzzy time comparison — §9) depends on
    this being exact and stable.
    """
    return dt.astimezone(timezone.utc).isoformat()


def _list_open_tasks_with_due_at(db: Session, space_id: int) -> list[Task]:
    """The single shared query behind TASK_OVERDUE/TASK_DUE_TODAY/
    TASK_DUE_SOON — all three share the identical eligibility
    precondition (open, not archived, due_at set) and differ only by
    which time window due_at falls into, so one query classified in
    Python avoids three near-identical round trips for the same rows.
    Space-scoped via scoped_query, matching tasks_service's own
    convention.
    """
    query = (
        scoped_query(Task, space_id)
        .where(Task.archived_at.is_(None))
        .where(Task.status == "open")
        .where(Task.due_at.is_not(None))
    )
    return list(db.execute(query).scalars().all())


def _list_upcoming_calendar_events(db: Session, space_id: int, now: datetime, window_end: datetime) -> list[CalendarEvent]:
    query = (
        scoped_query(CalendarEvent, space_id)
        .where(CalendarEvent.archived_at.is_(None))
        .where(CalendarEvent.starts_at >= now, CalendarEvent.starts_at < window_end)
    )
    return list(db.execute(query).scalars().all())


def _list_unread_inbox_items(db: Session, space_id: int) -> list[InboxItem]:
    query = (
        scoped_query(InboxItem, space_id)
        .where(InboxItem.archived_at.is_(None))
        .where(InboxItem.read_at.is_(None))
    )
    return list(db.execute(query).scalars().all())


def _task_signals(
    tasks: list[Task], now: datetime, next_midnight: datetime
) -> tuple[list[Signal], list[Signal], list[Signal]]:
    overdue: list[Signal] = []
    due_today: list[Signal] = []
    due_soon: list[Signal] = []

    for task in tasks:
        due_at = task.due_at
        assert due_at is not None  # guaranteed by _list_open_tasks_with_due_at's own filter

        snapshot = {"due_at": _canonical_timestamp(due_at), "status": task.status}

        if due_at < now:
            overdue.append(
                Signal(
                    signal_type="TASK_OVERDUE",
                    source_type="task",
                    source_id=task.id,
                    title=task.title,
                    relevant_timestamp=due_at,
                    priority=task.priority,
                    measurement_seconds=(now - due_at).total_seconds(),
                    snapshot=snapshot,
                )
            )
            # Overdue is disjoint from DUE_TODAY/DUE_SOON by construction
            # (both require now <= due_at) — never both for the same task.
            continue

        if due_at < next_midnight:
            due_today.append(
                Signal(
                    signal_type="TASK_DUE_TODAY",
                    source_type="task",
                    source_id=task.id,
                    title=task.title,
                    relevant_timestamp=due_at,
                    priority=task.priority,
                    measurement_seconds=(due_at - now).total_seconds(),
                    snapshot=snapshot,
                )
            )

        if due_at < now + _TASK_DUE_SOON_WINDOW:
            # Deliberately NOT deduplicated against TASK_DUE_TODAY above
            # (§5) — a task due in 2h today legitimately produces both.
            # Checkpoint 4.3 owns scoring/dedup.
            due_soon.append(
                Signal(
                    signal_type="TASK_DUE_SOON",
                    source_type="task",
                    source_id=task.id,
                    title=task.title,
                    relevant_timestamp=due_at,
                    priority=task.priority,
                    measurement_seconds=(due_at - now).total_seconds(),
                    snapshot=snapshot,
                )
            )

    return overdue, due_today, due_soon


def _event_signals(events: list[CalendarEvent], now: datetime) -> list[Signal]:
    return [
        Signal(
            signal_type="EVENT_UPCOMING",
            source_type="calendar_event",
            source_id=event.id,
            title=event.title,
            relevant_timestamp=event.starts_at,
            priority=None,
            measurement_seconds=(event.starts_at - now).total_seconds(),
            snapshot={"starts_at": _canonical_timestamp(event.starts_at)},
        )
        for event in events
    ]


def _inbox_signals(items: list[InboxItem], now: datetime) -> list[Signal]:
    return [
        Signal(
            signal_type="INBOX_NEEDS_ATTENTION",
            source_type="inbox_item",
            source_id=item.id,
            title=item.title,
            relevant_timestamp=item.created_at,
            priority=None,
            measurement_seconds=(now - item.created_at).total_seconds(),
            # Empty snapshot is intentional and locked (§7) — Attention
            # reads existing InboxItem state only, never re-derives its
            # generation logic.
            snapshot={},
        )
        for item in items
    ]


def generate_signals(db: Session, space_id: int, now: datetime, timezone_name: str) -> list[Signal]:
    """The single public entry point (Checkpoint 4.2). Every Signal
    returned by one call is evaluated against exactly this SAME `now`
    instant — callers (production: the request boundary, obtaining it
    from the real server clock; tests: any frozen instant) supply it
    explicitly, so nothing in this module ever calls datetime.now()
    itself. `timezone_name` must be a valid IANA name — validated here,
    never trusted or defaulted (see _resolve_zone).

    Returns signals grouped by SIGNAL_TYPE_ORDER, then by source_id
    within each type — a neutral, stable technical ordering, NOT an
    urgency ordering (Checkpoint 4.3 owns that).
    """
    zone = _resolve_zone(timezone_name)
    next_midnight = _next_local_midnight(now, zone)

    overdue, due_today, due_soon = _task_signals(_list_open_tasks_with_due_at(db, space_id), now, next_midnight)
    upcoming_events = _event_signals(
        _list_upcoming_calendar_events(db, space_id, now, now + _EVENT_UPCOMING_WINDOW), now
    )
    inbox_needs_attention = _inbox_signals(_list_unread_inbox_items(db, space_id), now)

    by_type: dict[str, list[Signal]] = {
        "TASK_OVERDUE": overdue,
        "TASK_DUE_TODAY": due_today,
        "TASK_DUE_SOON": due_soon,
        "EVENT_UPCOMING": upcoming_events,
        "INBOX_NEEDS_ATTENTION": inbox_needs_attention,
    }

    signals: list[Signal] = []
    for signal_type in SIGNAL_TYPE_ORDER:
        signals.extend(sorted(by_type[signal_type], key=lambda s: s.source_id))
    return signals
