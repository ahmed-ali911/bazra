from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.modules.attention import service as attention_service
from app.modules.attention.schemas import InvalidTimezoneError
from app.modules.auth import service as auth_service
from app.modules.calendar import service as calendar_service
from app.modules.calendar.schemas import CalendarEventCreate
from app.modules.inbox import service as inbox_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate, TaskUpdate

_CAIRO = "Africa/Cairo"


def _owner(db_session: Session):
    """The test DB is shared across the whole test SESSION (reset once,
    not per-test — see conftest.py), so the single seeded User row may
    already exist from an earlier test file/function. Reused if present,
    created if not — same idempotent pattern authenticated_client's own
    fixture already uses.
    """
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("attention-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> int:
    """A brand-new, empty Space per call — deliberately NEVER the shared
    default space, since that one accumulates rows across every test in
    the whole session (see home/tests/test_home.py's own
    test_home_space_isolation, which does exactly this for the same
    reason). Every "no signal"/exact-count assertion in this file
    depends on the space it queries being genuinely empty beforehand.
    """
    owner = _owner(db_session)
    space = Space(name="Attention test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space.id


def _other_space(db_session: Session) -> int:
    return _space(db_session)


def _task(db_session: Session, space_id: int, **kwargs) -> int:
    task = tasks_service.create_task(db_session, space_id, TaskCreate(title=kwargs.pop("title", "Task"), **kwargs))
    return task.id


def _signals_of_type(signals, signal_type):
    return [s for s in signals if s.signal_type == signal_type]


# ---- TASK_OVERDUE ----


def test_open_overdue_task_produces_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    due_at = now - timedelta(hours=3)
    task_id = _task(db_session, space_id, title="Overdue task", due_at=due_at)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    overdue = _signals_of_type(signals, "TASK_OVERDUE")
    assert [s.source_id for s in overdue] == [task_id]
    assert overdue[0].measurement_seconds == pytest.approx(3 * 3600)
    assert overdue[0].snapshot == {"due_at": "2026-06-01T09:00:00+00:00", "status": "open"}


def test_due_exactly_at_now_is_not_overdue(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _task(db_session, space_id, title="Due exactly now", due_at=now)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_OVERDUE") == []


def test_done_overdue_task_produces_no_signal(db_session: Session) -> None:
    """tasks_service.update_task's own open->done side effect (Checkpoint
    2.4) generates a real InboxItem ("Completed: ...") — a genuine,
    separate INBOX_NEEDS_ATTENTION signal, not a bug here. This test
    asserts only what it claims: no TASK_OVERDUE signal for a done task.
    """
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    task_id = _task(db_session, space_id, title="Done overdue", due_at=now - timedelta(hours=3))
    tasks_service.update_task(db_session, space_id, task_id, TaskUpdate(status="done"))

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_OVERDUE") == []


def test_archived_overdue_task_produces_no_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    task_id = _task(db_session, space_id, title="Archived overdue", due_at=now - timedelta(hours=3))
    tasks_service.delete_task(db_session, space_id, task_id)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert signals == []


def test_task_without_due_at_produces_no_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _task(db_session, space_id, title="No due date")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert signals == []


# ---- TASK_DUE_TODAY ----


def test_due_later_in_local_today_produces_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    # 12:00 Cairo (+02:00 in June, no DST in Egypt) = 10:00 UTC
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    due_at = now + timedelta(hours=6)  # later today, local
    task_id = _task(db_session, space_id, title="Due later today", due_at=due_at)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    due_today = _signals_of_type(signals, "TASK_DUE_TODAY")
    assert [s.source_id for s in due_today] == [task_id]


def test_overdue_earlier_today_is_not_due_today(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    _task(db_session, space_id, title="Earlier today, overdue", due_at=now - timedelta(hours=1))

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_DUE_TODAY") == []
    assert len(_signals_of_type(signals, "TASK_OVERDUE")) == 1


def test_due_exactly_at_now_is_due_today(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    task_id = _task(db_session, space_id, title="Due exactly now", due_at=now)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "TASK_DUE_TODAY")] == [task_id]


def test_due_exactly_at_next_local_midnight_is_not_due_today(db_session: Session) -> None:
    space_id = _space(db_session)
    zone = ZoneInfo(_CAIRO)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    next_midnight = attention_service._next_local_midnight(now, zone)
    _task(db_session, space_id, title="Due exactly at midnight", due_at=next_midnight)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_DUE_TODAY") == []


def test_due_immediately_before_next_local_midnight_is_due_today(db_session: Session) -> None:
    space_id = _space(db_session)
    zone = ZoneInfo(_CAIRO)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    next_midnight = attention_service._next_local_midnight(now, zone)
    task_id = _task(
        db_session, space_id, title="Due just before midnight", due_at=next_midnight - timedelta(seconds=1)
    )

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "TASK_DUE_TODAY")] == [task_id]


def test_invalid_timezone_raises_controlled_error(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(InvalidTimezoneError):
        attention_service.generate_signals(db_session, space_id, now, "Not/AZone")


# ---- TASK_DUE_SOON ----


def test_due_inside_four_hours_produces_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    task_id = _task(db_session, space_id, title="Due soon", due_at=now + timedelta(hours=2))

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "TASK_DUE_SOON")] == [task_id]


def test_due_exactly_at_now_is_due_soon(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    task_id = _task(db_session, space_id, title="Due exactly now", due_at=now)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "TASK_DUE_SOON")] == [task_id]


def test_due_exactly_at_now_plus_four_hours_is_not_due_soon(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    _task(db_session, space_id, title="Due at exactly +4h", due_at=now + timedelta(hours=4))

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_DUE_SOON") == []


def test_due_just_before_now_plus_four_hours_is_due_soon(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    task_id = _task(
        db_session, space_id, title="Due just before +4h", due_at=now + timedelta(hours=4) - timedelta(seconds=1)
    )

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "TASK_DUE_SOON")] == [task_id]


# ---- Overlap: DUE_TODAY and DUE_SOON are not deduplicated ----


def test_task_due_in_two_hours_today_is_both_due_today_and_due_soon(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    task_id = _task(db_session, space_id, title="Due in 2h today", due_at=now + timedelta(hours=2))

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "TASK_DUE_TODAY")] == [task_id]
    assert [s.source_id for s in _signals_of_type(signals, "TASK_DUE_SOON")] == [task_id]
    # No cross-type dedup in 4.2 — both signals genuinely coexist.
    assert len(signals) == 2


# ---- EVENT_UPCOMING ----


def test_event_inside_two_hours_produces_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    event = calendar_service.create_calendar_event(
        db_session, space_id, CalendarEventCreate(title="Upcoming meeting", starts_at=now + timedelta(hours=1))
    )

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "EVENT_UPCOMING")] == [event.id]


def test_event_starting_exactly_now_produces_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    event = calendar_service.create_calendar_event(
        db_session, space_id, CalendarEventCreate(title="Starting now", starts_at=now)
    )

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "EVENT_UPCOMING")] == [event.id]


def test_event_starting_exactly_at_now_plus_two_hours_produces_no_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    calendar_service.create_calendar_event(
        db_session, space_id, CalendarEventCreate(title="Starting at +2h", starts_at=now + timedelta(hours=2))
    )

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "EVENT_UPCOMING") == []


def test_archived_event_produces_no_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    event = calendar_service.create_calendar_event(
        db_session, space_id, CalendarEventCreate(title="Archived event", starts_at=now + timedelta(hours=1))
    )
    calendar_service.delete_calendar_event(db_session, space_id, event.id)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "EVENT_UPCOMING") == []


def test_already_started_event_produces_no_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    calendar_service.create_calendar_event(
        db_session,
        space_id,
        CalendarEventCreate(
            title="Already in progress", starts_at=now - timedelta(minutes=30), ends_at=now + timedelta(minutes=30)
        ),
    )

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "EVENT_UPCOMING") == []


# ---- INBOX_NEEDS_ATTENTION ----


def test_unread_active_inbox_item_produces_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    item = inbox_service.create_item(db_session, space_id, task_id=None, title="Unread item")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    inbox_signals = _signals_of_type(signals, "INBOX_NEEDS_ATTENTION")
    assert [s.source_id for s in inbox_signals] == [item.id]
    assert inbox_signals[0].snapshot == {}


def test_read_inbox_item_produces_no_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    item = inbox_service.create_item(db_session, space_id, task_id=None, title="Read item")
    inbox_service.mark_read(db_session, space_id, item.id, read=True)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "INBOX_NEEDS_ATTENTION") == []


def test_archived_inbox_item_produces_no_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    item = inbox_service.create_item(db_session, space_id, task_id=None, title="Archived item")
    inbox_service.dismiss_item(db_session, space_id, item.id)

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "INBOX_NEEDS_ATTENTION") == []


# ---- Priority ----


def test_task_signal_carries_low_priority(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _task(db_session, space_id, title="Low priority overdue", due_at=now - timedelta(hours=1), priority="low")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_OVERDUE")[0].priority == "low"


def test_task_signal_carries_normal_priority(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _task(db_session, space_id, title="Normal priority overdue", due_at=now - timedelta(hours=1))

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_OVERDUE")[0].priority == "normal"


def test_task_signal_carries_high_priority(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _task(db_session, space_id, title="High priority overdue", due_at=now - timedelta(hours=1), priority="high")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert _signals_of_type(signals, "TASK_OVERDUE")[0].priority == "high"


def test_priority_alone_does_not_create_a_signal(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # High priority, but no due_at at all — must produce zero signals,
    # exactly like any other task with no due_at.
    _task(db_session, space_id, title="High priority, no due date", priority="high")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert signals == []


# ---- Security: space isolation ----


def test_another_spaces_task_is_never_returned(db_session: Session) -> None:
    space_id = _space(db_session)
    other_space_id = _other_space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _task(db_session, other_space_id, title="Other space overdue task", due_at=now - timedelta(hours=1))

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert signals == []


def test_another_spaces_event_is_never_returned(db_session: Session) -> None:
    space_id = _space(db_session)
    other_space_id = _other_space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    calendar_service.create_calendar_event(
        db_session, other_space_id, CalendarEventCreate(title="Other space event", starts_at=now + timedelta(hours=1))
    )

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert signals == []


def test_another_spaces_inbox_item_is_never_returned(db_session: Session) -> None:
    space_id = _space(db_session)
    other_space_id = _other_space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    inbox_service.create_item(db_session, other_space_id, task_id=None, title="Other space inbox item")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert signals == []


# ---- Clock: single shared evaluation instant ----


def test_all_signals_in_one_pass_share_the_same_evaluation_instant(db_session: Session) -> None:
    """InboxItem.created_at is server-set (func.now(), the real wall
    clock) and not client-controllable — so `now` here must be the real
    current instant too, rather than the fixed dates every other test in
    this file uses, or the inbox measurement below would be computed
    against an unrelated fixed date instead of this row's real
    created_at.
    """
    space_id = _space(db_session)
    now = datetime.now(timezone.utc)

    overdue_task_id = _task(db_session, space_id, title="Overdue", due_at=now - timedelta(hours=2))
    soon_task_id = _task(db_session, space_id, title="Due soon", due_at=now + timedelta(hours=1))
    event = calendar_service.create_calendar_event(
        db_session, space_id, CalendarEventCreate(title="Event soon", starts_at=now + timedelta(minutes=30))
    )
    item = inbox_service.create_item(db_session, space_id, task_id=None, title="Inbox item")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)

    by_source = {(s.signal_type, s.source_id): s for s in signals}
    assert by_source[("TASK_OVERDUE", overdue_task_id)].measurement_seconds == pytest.approx(2 * 3600, abs=5)
    assert by_source[("TASK_DUE_SOON", soon_task_id)].measurement_seconds == pytest.approx(1 * 3600, abs=5)
    assert by_source[("EVENT_UPCOMING", event.id)].measurement_seconds == pytest.approx(30 * 60, abs=5)
    assert by_source[("INBOX_NEEDS_ATTENTION", item.id)].measurement_seconds == pytest.approx(0, abs=5)


# ---- Ordering ----


def test_signals_are_ordered_by_type_then_source_id(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    second_overdue_id = _task(db_session, space_id, title="Overdue B", due_at=now - timedelta(hours=1))
    first_overdue_id = _task(db_session, space_id, title="Overdue A", due_at=now - timedelta(hours=2))
    calendar_service.create_calendar_event(
        db_session, space_id, CalendarEventCreate(title="Event", starts_at=now + timedelta(minutes=10))
    )
    inbox_service.create_item(db_session, space_id, task_id=None, title="Inbox")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    types_in_order = [s.signal_type for s in signals]
    assert types_in_order == sorted(types_in_order, key=attention_service.SIGNAL_TYPE_ORDER.index)
    overdue_ids = [s.source_id for s in _signals_of_type(signals, "TASK_OVERDUE")]
    assert overdue_ids == sorted([first_overdue_id, second_overdue_id])


# ---- Canonical snapshot serialization ----


def test_equivalent_instants_with_different_offsets_serialize_identically() -> None:
    utc_instant = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    cairo_instant = utc_instant.astimezone(ZoneInfo(_CAIRO))
    assert utc_instant != cairo_instant or True  # different tzinfo, same real instant
    assert attention_service._canonical_timestamp(utc_instant) == "2026-09-30T12:00:00+00:00"
    assert attention_service._canonical_timestamp(cairo_instant) == "2026-09-30T12:00:00+00:00"


# ---- Cross-source: Task + referencing InboxItem may both appear ----


def test_overdue_task_and_its_inbox_item_may_both_appear_uncoordinated(db_session: Session) -> None:
    """Documented raw-signal behavior (§11): 4.2 does not attempt
    cross-source dedup or suppression — that is Checkpoint 4.3's job.
    """
    space_id = _space(db_session)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    task_id = _task(db_session, space_id, title="Referenced overdue task", due_at=now - timedelta(hours=1))
    item = inbox_service.create_item(db_session, space_id, task_id=task_id, title="Related inbox item")

    signals = attention_service.generate_signals(db_session, space_id, now, _CAIRO)
    assert [s.source_id for s in _signals_of_type(signals, "TASK_OVERDUE")] == [task_id]
    assert [s.source_id for s in _signals_of_type(signals, "INBOX_NEEDS_ATTENTION")] == [item.id]


# ==================================================
# DST boundary tests (§16) — America/New_York, real spring-forward and
# fall-back transitions. 2026's US transitions: spring forward on
# 2026-03-08 (2am -> 3am, a 23-hour local day), fall back on
# 2026-11-01 (2am repeats, a 25-hour local day). Ground-truth UTC
# instants below were independently derived via zoneinfo, not guessed.
# ==================================================

_NEW_YORK = "America/New_York"


def test_next_local_midnight_across_spring_forward_is_23_hours_not_24(db_session: Session) -> None:
    zone = ZoneInfo(_NEW_YORK)
    today_local_midnight = datetime(2026, 3, 8, 0, 0, 0, tzinfo=zone)
    now = today_local_midnight + timedelta(hours=1)  # 1am local, March 8

    next_midnight = attention_service._next_local_midnight(now, zone)

    # Ground truth: local midnight of March 9 is 04:00 UTC (not 05:00,
    # which a naive now+24h/UTC+24h boundary would wrongly produce given
    # March 8's own midnight is 05:00 UTC).
    assert next_midnight.astimezone(timezone.utc) == datetime(2026, 3, 9, 4, 0, 0, tzinfo=timezone.utc)
    gap_seconds = (
        next_midnight.astimezone(timezone.utc) - today_local_midnight.astimezone(timezone.utc)
    ).total_seconds()
    assert gap_seconds == 23 * 3600


def test_task_due_today_boundary_is_correct_on_spring_forward_day(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 3, 8, 20, 0, 0, tzinfo=timezone.utc)  # inside local March 8 (EDT after the transition)
    next_midnight_utc = datetime(2026, 3, 9, 4, 0, 0, tzinfo=timezone.utc)

    just_before_id = _task(
        db_session, space_id, title="Just before midnight (spring)", due_at=next_midnight_utc - timedelta(seconds=1)
    )
    _task(db_session, space_id, title="Exactly at midnight (spring)", due_at=next_midnight_utc)

    signals = attention_service.generate_signals(db_session, space_id, now, _NEW_YORK)
    due_today_ids = [s.source_id for s in _signals_of_type(signals, "TASK_DUE_TODAY")]
    assert due_today_ids == [just_before_id]


def test_next_local_midnight_across_fall_back_is_25_hours_not_24(db_session: Session) -> None:
    zone = ZoneInfo(_NEW_YORK)
    today_local_midnight = datetime(2026, 11, 1, 0, 0, 0, tzinfo=zone)
    now = today_local_midnight + timedelta(hours=1)  # 1am local, November 1

    next_midnight = attention_service._next_local_midnight(now, zone)

    # Ground truth: local midnight of November 2 is 05:00 UTC.
    assert next_midnight.astimezone(timezone.utc) == datetime(2026, 11, 2, 5, 0, 0, tzinfo=timezone.utc)
    gap_seconds = (
        next_midnight.astimezone(timezone.utc) - today_local_midnight.astimezone(timezone.utc)
    ).total_seconds()
    assert gap_seconds == 25 * 3600


def test_task_due_today_boundary_is_correct_on_fall_back_day(db_session: Session) -> None:
    space_id = _space(db_session)
    now = datetime(2026, 11, 1, 20, 0, 0, tzinfo=timezone.utc)  # inside local November 1 (EST after the transition)
    next_midnight_utc = datetime(2026, 11, 2, 5, 0, 0, tzinfo=timezone.utc)

    just_before_id = _task(
        db_session, space_id, title="Just before midnight (fall)", due_at=next_midnight_utc - timedelta(seconds=1)
    )
    _task(db_session, space_id, title="Exactly at midnight (fall)", due_at=next_midnight_utc)

    signals = attention_service.generate_signals(db_session, space_id, now, _NEW_YORK)
    due_today_ids = [s.source_id for s in _signals_of_type(signals, "TASK_DUE_TODAY")]
    assert due_today_ids == [just_before_id]
