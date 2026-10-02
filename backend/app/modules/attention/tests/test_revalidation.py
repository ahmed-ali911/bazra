"""Checkpoint 4.5e — Selected-Candidate Revalidation. One test per
signal type's stale/valid transition, per the locked examples in the
4.5e brief (TASK_OVERDUE/TASK_DUE_TODAY/TASK_DUE_SOON/EVENT_UPCOMING/
INBOX_NEEDS_ATTENTION), plus the "source no longer exists at all"
(archived/deleted) case shared by all five.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.modules.attention import revalidation
from app.modules.attention.schemas import AttentionCandidate, GateResult, ScoreComponent, Signal
from app.modules.auth import service as auth_service
from app.modules.calendar import service as calendar_service
from app.modules.calendar.schemas import CalendarEventCreate, CalendarEventUpdate
from app.modules.inbox import service as inbox_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate, TaskUpdate

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_CAIRO = "Africa/Cairo"


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("revalidation-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    owner = _owner(db_session)
    space = Space(name="Revalidation test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _candidate(signal_type, source_type, source_id, priority=None) -> AttentionCandidate:
    signal = Signal(
        signal_type=signal_type,
        source_type=source_type,
        source_id=source_id,
        title="x",
        relevant_timestamp=_NOW,
        priority=priority,
        measurement_seconds=3600,
        snapshot={},
    )
    return AttentionCandidate(
        signal=signal, score=75, reason_codes=(ScoreComponent("BASE", None, 75),),
        suppression=GateResult(suppressed=False, reason_code=None),
    )


# ==================================================
# TASK_OVERDUE
# ==================================================


def test_task_overdue_still_open_and_overdue_is_valid(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title="t", due_at=_NOW - timedelta(days=1), priority="high")
    )
    candidate = _candidate("TASK_OVERDUE", "task", task.id, "high")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is True


def test_task_overdue_marked_done_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title="t", due_at=_NOW - timedelta(days=1), priority="high")
    )
    tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(status="done"))
    candidate = _candidate("TASK_OVERDUE", "task", task.id, "high")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


def test_task_overdue_archived_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title="t", due_at=_NOW - timedelta(days=1), priority="high")
    )
    tasks_service.delete_task(db_session, space.id, task.id)
    candidate = _candidate("TASK_OVERDUE", "task", task.id, "high")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


def test_task_overdue_rescheduled_to_future_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title="t", due_at=_NOW - timedelta(days=1), priority="high")
    )
    tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(due_at=_NOW + timedelta(days=5)))
    candidate = _candidate("TASK_OVERDUE", "task", task.id, "high")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


# ==================================================
# TASK_DUE_TODAY
# ==================================================


def test_task_due_today_still_due_today_is_valid(db_session: Session) -> None:
    space = _space(db_session)
    due_at = _NOW + timedelta(hours=2)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=due_at, priority="normal"))
    candidate = _candidate("TASK_DUE_TODAY", "task", task.id, "normal")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is True


def test_task_due_today_completed_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    due_at = _NOW + timedelta(hours=2)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=due_at, priority="normal"))
    tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(status="done"))
    candidate = _candidate("TASK_DUE_TODAY", "task", task.id, "normal")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


def test_task_due_today_pushed_to_tomorrow_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    due_at = _NOW + timedelta(hours=2)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=due_at, priority="normal"))
    tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(due_at=_NOW + timedelta(days=2)))
    candidate = _candidate("TASK_DUE_TODAY", "task", task.id, "normal")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


# ==================================================
# TASK_DUE_SOON
# ==================================================


def test_task_due_soon_still_eligible_is_valid(db_session: Session) -> None:
    space = _space(db_session)
    due_at = _NOW + timedelta(hours=2)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=due_at, priority="normal"))
    candidate = _candidate("TASK_DUE_SOON", "task", task.id, "normal")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is True


def test_task_due_soon_no_longer_eligible_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    due_at = _NOW + timedelta(hours=2)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=due_at, priority="normal"))
    tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(due_at=_NOW + timedelta(days=3)))
    candidate = _candidate("TASK_DUE_SOON", "task", task.id, "normal")
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


# ==================================================
# EVENT_UPCOMING
# ==================================================


def test_event_upcoming_still_within_window_is_valid(db_session: Session) -> None:
    space = _space(db_session)
    event = calendar_service.create_calendar_event(
        db_session, space.id, CalendarEventCreate(title="e", starts_at=_NOW + timedelta(minutes=30))
    )
    candidate = _candidate("EVENT_UPCOMING", "calendar_event", event.id)
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is True


def test_event_upcoming_archived_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    event = calendar_service.create_calendar_event(
        db_session, space.id, CalendarEventCreate(title="e", starts_at=_NOW + timedelta(minutes=30))
    )
    calendar_service.delete_calendar_event(db_session, space.id, event.id)
    candidate = _candidate("EVENT_UPCOMING", "calendar_event", event.id)
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


def test_event_upcoming_rescheduled_far_out_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    event = calendar_service.create_calendar_event(
        db_session, space.id, CalendarEventCreate(title="e", starts_at=_NOW + timedelta(minutes=30))
    )
    calendar_service.update_calendar_event(
        db_session, space.id, event.id, CalendarEventUpdate(starts_at=_NOW + timedelta(days=3)),
    )
    candidate = _candidate("EVENT_UPCOMING", "calendar_event", event.id)
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


# ==================================================
# INBOX_NEEDS_ATTENTION
# ==================================================


def test_inbox_unread_is_valid(db_session: Session) -> None:
    space = _space(db_session)
    item = inbox_service.create_item(db_session, space.id, task_id=None, title="i")
    candidate = _candidate("INBOX_NEEDS_ATTENTION", "inbox_item", item.id)
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is True


def test_inbox_read_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    item = inbox_service.create_item(db_session, space.id, task_id=None, title="i")
    inbox_service.mark_read(db_session, space.id, item.id, True)
    candidate = _candidate("INBOX_NEEDS_ATTENTION", "inbox_item", item.id)
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


def test_inbox_archived_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    item = inbox_service.create_item(db_session, space.id, task_id=None, title="i")
    inbox_service.dismiss_item(db_session, space.id, item.id)
    candidate = _candidate("INBOX_NEEDS_ATTENTION", "inbox_item", item.id)
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False


def test_inbox_nonexistent_is_stale(db_session: Session) -> None:
    space = _space(db_session)
    candidate = _candidate("INBOX_NEEDS_ATTENTION", "inbox_item", 999999999)
    assert revalidation.candidate_is_still_valid(db_session, space.id, candidate, _NOW, _CAIRO) is False
