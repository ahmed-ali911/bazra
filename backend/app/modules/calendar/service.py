from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.attention import history as attention_history
from app.modules.attention.schemas import EventMutationState
from app.modules.calendar.models import CalendarEvent
from app.modules.calendar.schemas import AgendaItem, CalendarEventCreate, CalendarEventUpdate
from app.modules.tasks import service as tasks_service


def count_events_by_life_area(db: Session, life_area_id: int) -> int:
    """GLOBAL count (no space_id filter), regardless of archived_at —
    see tasks_service.count_tasks_by_life_area's docstring for why this
    is deliberately unscoped: it must match exactly what the database's
    own FK constraint would block life-area deletion on.
    """
    return db.execute(
        select(func.count()).select_from(CalendarEvent).where(CalendarEvent.life_area_id == life_area_id)
    ).scalar_one()


def get_calendar_event(db: Session, space_id: int, event_id: int) -> CalendarEvent | None:
    query = scoped_query(CalendarEvent, space_id).where(
        CalendarEvent.id == event_id, CalendarEvent.archived_at.is_(None)
    )
    return db.execute(query).scalar_one_or_none()


def create_calendar_event(db: Session, space_id: int, data: CalendarEventCreate, commit: bool = True) -> CalendarEvent:
    """commit=True by default (direct REST). Checkpoint 3.H2 — commit=False
    lets actions_service.confirm_and_execute create this event as part of
    its own single authoritative transaction, the same escape hatch
    tasks_service.create_task already established."""
    event = CalendarEvent(space_id=space_id, **data.model_dump())
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        # Flush assigns the PK and every server-generated column without
        # ending the transaction — proven sufficient (Checkpoint 3.H1's
        # real-Postgres experiment); refresh is deliberately not called.
        db.flush()
    return event


def update_calendar_event(
    db: Session, space_id: int, event_id: int, data: CalendarEventUpdate, commit: bool = True
) -> CalendarEvent | None:
    """commit=True by default (direct REST). Checkpoint 3.H2 —
    commit=False folds this into confirm_and_execute's own single
    authoritative transaction."""
    event = get_calendar_event(db, space_id, event_id)
    if event is None:
        return None

    now = datetime.now(timezone.utc)
    previous_starts_at = event.starts_at

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(event, field, value)

    # Validated against the MERGED result, not the raw payload — a partial
    # update may only include one of starts_at/ends_at.
    if event.ends_at is not None and event.ends_at < event.starts_at:
        raise ValueError("ends_at must be >= starts_at")

    # Checkpoint 4.4c-3 — attempted ONLY when starts_at's VALUE actually
    # changed (never merely because it was present in the payload), and
    # only after the validation above has already passed (a request
    # that's about to raise and never commit must never attribute).
    if event.starts_at != previous_starts_at:
        attention_history.attempt_acted_on_attribution(
            db, space_id, "calendar_event",
            event.id,
            EventMutationState(starts_at=event.starts_at, archived_at=event.archived_at),
            now,
        )

    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return event


def delete_calendar_event(db: Session, space_id: int, event_id: int, commit: bool = True) -> bool:
    """Soft delete: sets archived_at, same reasoning as Task.delete_task —
    protects a future Inbox reference from dangling.

    commit=True by default (direct REST). Checkpoint 3.H2 — commit=False
    folds this into confirm_and_execute's own single authoritative
    transaction."""
    event = get_calendar_event(db, space_id, event_id)
    if event is None:
        return False
    now = datetime.now(timezone.utc)
    event.archived_at = now

    # Checkpoint 4.4c-3 — archive is always a qualifying, real mutation
    # here (get_calendar_event's own archived_at IS NULL filter above
    # guarantees this is a genuine first-time archive).
    attention_history.attempt_acted_on_attribution(
        db, space_id, "calendar_event",
        event.id,
        EventMutationState(starts_at=event.starts_at, archived_at=event.archived_at),
        now,
    )

    if commit:
        db.commit()
    else:
        db.flush()
    return True


def _list_calendar_events_overlapping(
    db: Session, space_id: int, from_at: datetime, to_at: datetime
) -> list[CalendarEvent]:
    """Interval-overlap semantics against the half-open agenda range
    [from_at, to_at) — not "starts_at is inside the range". A ranged event
    [starts_at, ends_at] (closed at both its own ends) overlaps the agenda
    range if starts_at < to_at AND ends_at >= from_at. A point event
    (ends_at is null) uses the same half-open point semantics as
    Task.due_at: from_at <= starts_at < to_at.
    """
    overlap = or_(
        and_(
            CalendarEvent.ends_at.is_not(None),
            CalendarEvent.starts_at < to_at,
            CalendarEvent.ends_at >= from_at,
        ),
        and_(
            CalendarEvent.ends_at.is_(None),
            CalendarEvent.starts_at >= from_at,
            CalendarEvent.starts_at < to_at,
        ),
    )
    query = scoped_query(CalendarEvent, space_id).where(CalendarEvent.archived_at.is_(None)).where(overlap)
    return list(db.execute(query).scalars().all())


def list_events_starting_between(db: Session, space_id: int, from_at: datetime, to_at: datetime) -> list[CalendarEvent]:
    """Narrow, single-purpose query for Home's Coming Up section — a plain
    point check on starts_at only (half-open [from_at, to_at)), NOT the
    interval-overlap semantics _list_calendar_events_overlapping above
    uses. Those two functions deliberately answer different questions:
    "what's happening during this window" (agenda) vs. "what's starting
    soon" (Coming Up). Reusing the overlap function here would silently
    pull in an event that's already in progress (started before from_at,
    still running) — a real, deliberate simplification: Coming Up shows
    what's STARTING next, not everything currently underway.
    """
    query = (
        scoped_query(CalendarEvent, space_id)
        .where(CalendarEvent.archived_at.is_(None))
        .where(CalendarEvent.starts_at >= from_at, CalendarEvent.starts_at < to_at)
        .order_by(CalendarEvent.starts_at.asc())
    )
    return list(db.execute(query).scalars().all())


def build_agenda(db: Session, space_id: int, from_at: datetime, to_at: datetime) -> list[AgendaItem]:
    """The read-model: CalendarEvent rows + Task rows with due_at in range,
    merged and sorted in Python (not a SQL UNION — that would mean reaching
    into Task's table/columns directly, breaking the "cross-module reads go
    through the other module's service.py" convention). Nothing here
    duplicates or stores Task data; both sources are queried fresh.
    """
    if to_at <= from_at:
        raise ValueError("to must be after from")

    events = _list_calendar_events_overlapping(db, space_id, from_at, to_at)
    tasks = tasks_service.list_tasks_due_between(db, space_id, from_at, to_at)

    items = [
        AgendaItem(
            source="event",
            id=event.id,
            title=event.title,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            life_area_id=event.life_area_id,
        )
        for event in events
    ] + [
        AgendaItem(
            source="task",
            id=task.id,
            title=task.title,
            # due_at is guaranteed non-null here — list_tasks_due_between
            # only returns tasks whose due_at falls in [from_at, to_at).
            starts_at=task.due_at,  # type: ignore[arg-type]
            ends_at=None,
            life_area_id=task.life_area_id,
        )
        for task in tasks
    ]
    items.sort(key=lambda item: item.starts_at)
    return items
