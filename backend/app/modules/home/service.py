from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.calendar import service as calendar_service
from app.modules.calendar.schemas import AgendaItem
from app.modules.home.schemas import HomeSummary
from app.modules.inbox import service as inbox_service
from app.modules.inbox.schemas import InboxItemResponse
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskResponse


def build_home_summary(db: Session, space_id: int, tomorrow_start: datetime, window_end: datetime) -> HomeSummary:
    """Aggregates Tasks, Calendar, and Inbox into Home's four sections.

    tomorrow_start and window_end are LOCAL calendar-day boundaries and
    must be computed by the CLIENT, never derived here from a fixed
    24-hour addition — a UTC instant plus exactly 24h is not reliably
    "the next local day": on a DST transition, the caller's local day is
    23 or 25 hours long, not 24, so server-side +timedelta(days=1) would
    silently compute the wrong boundary on those two days a year. The
    server treats both purely as opaque instants and does no day
    arithmetic of its own. "now" (the Coming Up event lower bound) IS
    computed here, safely — it's an absolute instant, not a calendar-day
    boundary, so DST doesn't affect it.

    Focus Today's upper bound (tomorrow_start) is exactly Coming Up's
    task lower bound, so the two ranges are disjoint by construction —
    nothing here needs a separate dedup check.

    This runs four separate SELECTs under this Session's default READ
    COMMITTED isolation (no isolation-level override) — it is NOT a
    guaranteed-consistent snapshot across all four buckets; a write
    landing between two of these queries could in principle appear in
    one bucket and not another. For a single local user this is an
    accepted, extremely unlikely edge case, not a solved one. Bundling
    these into one request exists only to avoid four separate round
    trips and keep the bucketing logic server-side and testable, not to
    claim atomicity.
    """
    if window_end <= tomorrow_start:
        raise ValueError("window_end must be after tomorrow_start")

    now = datetime.now(timezone.utc)

    focus_today = tasks_service.list_open_tasks_due_before(db, space_id, tomorrow_start)
    anytime = tasks_service.list_open_tasks_without_due_date(db, space_id)
    coming_up_tasks = tasks_service.list_open_tasks_due_between(db, space_id, tomorrow_start, window_end)
    coming_up_events = calendar_service.list_events_starting_between(db, space_id, now, window_end)

    coming_up = [
        AgendaItem(
            source="task",
            id=task.id,
            title=task.title,
            starts_at=task.due_at,  # type: ignore[arg-type]
            ends_at=None,
            life_area_id=task.life_area_id,
        )
        for task in coming_up_tasks
    ] + [
        AgendaItem(
            source="event",
            id=event.id,
            title=event.title,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            life_area_id=event.life_area_id,
        )
        for event in coming_up_events
    ]
    coming_up.sort(key=lambda item: item.starts_at)

    needs_attention = inbox_service.list_items(db, space_id, unread=True)

    return HomeSummary(
        focus_today=[TaskResponse.model_validate(task) for task in focus_today],
        coming_up=coming_up,
        needs_attention=[InboxItemResponse.model_validate(item) for item in needs_attention],
        anytime=[TaskResponse.model_validate(task) for task in anytime],
    )
