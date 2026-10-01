import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.attention import resolution
from app.modules.attention.schemas import (
    ActedOnResolutionError,
    EventMutationState,
    InboxMutationState,
    TaskMutationState,
)

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _task(status="open", due_at=None, archived_at=None) -> TaskMutationState:
    return TaskMutationState(status=status, due_at=due_at, archived_at=archived_at)


def _event(starts_at, archived_at=None) -> EventMutationState:
    return EventMutationState(starts_at=starts_at, archived_at=archived_at)


def _inbox(read_at=None, archived_at=None) -> InboxMutationState:
    return InboxMutationState(read_at=read_at, archived_at=archived_at)


# ==================================================
# TASK_OVERDUE (1-5)
# ==================================================


def test_task_overdue_done_is_resolved():
    state = _task(status="done", due_at=_NOW - timedelta(days=1))
    assert resolution.evaluate_acted_on("TASK_OVERDUE", state, _NOW) == "RESOLVED"


def test_task_overdue_archived_is_resolved():
    state = _task(status="open", due_at=_NOW - timedelta(days=1), archived_at=_NOW)
    assert resolution.evaluate_acted_on("TASK_OVERDUE", state, _NOW) == "RESOLVED"


def test_task_overdue_due_at_future_is_resolved():
    state = _task(due_at=_NOW + timedelta(hours=1))
    assert resolution.evaluate_acted_on("TASK_OVERDUE", state, _NOW) == "RESOLVED"


def test_task_overdue_due_at_still_past_is_not_resolved():
    state = _task(due_at=_NOW - timedelta(hours=1))
    assert resolution.evaluate_acted_on("TASK_OVERDUE", state, _NOW) == "NOT_RESOLVED"


def test_task_overdue_due_at_null_is_resolved():
    state = _task(due_at=None)
    assert resolution.evaluate_acted_on("TASK_OVERDUE", state, _NOW) == "RESOLVED"


# ==================================================
# TASK_DUE_TODAY (6-16)
# ==================================================

_TZ = "Africa/Cairo"


def test_task_due_today_done_is_resolved():
    state = _task(status="done", due_at=_NOW + timedelta(hours=1))
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, _TZ) == "RESOLVED"


def test_task_due_today_archived_is_resolved():
    state = _task(due_at=_NOW + timedelta(hours=1), archived_at=_NOW)
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, _TZ) == "RESOLVED"


def test_task_due_today_later_same_local_day_is_not_resolved():
    state = _task(due_at=_NOW + timedelta(hours=2))
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, _TZ) == "NOT_RESOLVED"


def test_task_due_today_tomorrow_is_resolved():
    state = _task(due_at=_NOW + timedelta(days=1))
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, _TZ) == "RESOLVED"


def test_task_due_today_due_at_null_is_resolved():
    state = _task(due_at=None)
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, _TZ) == "RESOLVED"


def test_task_due_today_becomes_overdue_is_not_resolved():
    """Concern-continuity: the narrow TASK_DUE_TODAY predicate goes
    false, but this is a transition into the MORE SEVERE TASK_OVERDUE
    concern, never a resolution."""
    state = _task(due_at=_NOW - timedelta(minutes=1))
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, _TZ) == "NOT_RESOLVED"


def test_task_due_today_null_timezone_is_unknown():
    state = _task(due_at=_NOW + timedelta(days=1))
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, None) == "UNKNOWN"


def test_task_due_today_invalid_timezone_is_unknown():
    state = _task(due_at=_NOW + timedelta(days=1))
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", state, _NOW, "Not/AZone") == "UNKNOWN"


def test_task_due_today_kuwait_boundary_exactness():
    zone_now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    next_midnight_utc = datetime(2026, 6, 1, 21, 0, 0, tzinfo=timezone.utc)  # independently derived

    just_before = _task(due_at=next_midnight_utc - timedelta(seconds=1))
    at_midnight = _task(due_at=next_midnight_utc)

    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", just_before, zone_now, "Asia/Kuwait") == "NOT_RESOLVED"
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", at_midnight, zone_now, "Asia/Kuwait") == "RESOLVED"


def test_task_due_today_new_york_spring_forward_boundary():
    """2026-03-08 is the real US spring-forward transition (23-hour
    local day) — ground truth independently derived via zoneinfo, the
    same instants already proven in Checkpoint 4.2's own DST tests."""
    zone_now = datetime(2026, 3, 8, 20, 0, 0, tzinfo=timezone.utc)
    next_midnight_utc = datetime(2026, 3, 9, 4, 0, 0, tzinfo=timezone.utc)

    just_before = _task(due_at=next_midnight_utc - timedelta(seconds=1))
    at_midnight = _task(due_at=next_midnight_utc)

    assert (
        resolution.evaluate_acted_on("TASK_DUE_TODAY", just_before, zone_now, "America/New_York") == "NOT_RESOLVED"
    )
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", at_midnight, zone_now, "America/New_York") == "RESOLVED"


def test_task_due_today_new_york_fall_back_boundary():
    """2026-11-01 is the real US fall-back transition (25-hour local
    day)."""
    zone_now = datetime(2026, 11, 1, 20, 0, 0, tzinfo=timezone.utc)
    next_midnight_utc = datetime(2026, 11, 2, 5, 0, 0, tzinfo=timezone.utc)

    just_before = _task(due_at=next_midnight_utc - timedelta(seconds=1))
    at_midnight = _task(due_at=next_midnight_utc)

    assert (
        resolution.evaluate_acted_on("TASK_DUE_TODAY", just_before, zone_now, "America/New_York") == "NOT_RESOLVED"
    )
    assert resolution.evaluate_acted_on("TASK_DUE_TODAY", at_midnight, zone_now, "America/New_York") == "RESOLVED"


# ==================================================
# TASK_DUE_SOON (17-22)
# ==================================================


def test_task_due_soon_done_is_resolved():
    state = _task(status="done", due_at=_NOW + timedelta(hours=1))
    assert resolution.evaluate_acted_on("TASK_DUE_SOON", state, _NOW) == "RESOLVED"


def test_task_due_soon_archived_is_resolved():
    state = _task(due_at=_NOW + timedelta(hours=1), archived_at=_NOW)
    assert resolution.evaluate_acted_on("TASK_DUE_SOON", state, _NOW) == "RESOLVED"


def test_task_due_soon_plus_6h_is_resolved():
    state = _task(due_at=_NOW + timedelta(hours=6))
    assert resolution.evaluate_acted_on("TASK_DUE_SOON", state, _NOW) == "RESOLVED"


def test_task_due_soon_plus_3h_is_not_resolved():
    state = _task(due_at=_NOW + timedelta(hours=3))
    assert resolution.evaluate_acted_on("TASK_DUE_SOON", state, _NOW) == "NOT_RESOLVED"


def test_task_due_soon_due_at_null_is_resolved():
    state = _task(due_at=None)
    assert resolution.evaluate_acted_on("TASK_DUE_SOON", state, _NOW) == "RESOLVED"


def test_task_due_soon_becomes_overdue_is_not_resolved():
    state = _task(due_at=_NOW - timedelta(minutes=1))
    assert resolution.evaluate_acted_on("TASK_DUE_SOON", state, _NOW) == "NOT_RESOLVED"


# ==================================================
# EVENT_UPCOMING (23-26)
# ==================================================


def test_event_upcoming_archive_is_resolved():
    state = _event(starts_at=_NOW + timedelta(minutes=30), archived_at=_NOW)
    assert resolution.evaluate_acted_on("EVENT_UPCOMING", state, _NOW) == "RESOLVED"


def test_event_upcoming_plus_4h_is_resolved():
    state = _event(starts_at=_NOW + timedelta(hours=4))
    assert resolution.evaluate_acted_on("EVENT_UPCOMING", state, _NOW) == "RESOLVED"


def test_event_upcoming_plus_90m_is_not_resolved():
    state = _event(starts_at=_NOW + timedelta(minutes=90))
    assert resolution.evaluate_acted_on("EVENT_UPCOMING", state, _NOW) == "NOT_RESOLVED"


def test_event_upcoming_starts_at_before_now_is_not_resolved():
    """Conservative: no EVENT_OVERDUE concept exists; a backward move
    into the past is never automatically treated as success."""
    state = _event(starts_at=_NOW - timedelta(minutes=1))
    assert resolution.evaluate_acted_on("EVENT_UPCOMING", state, _NOW) == "NOT_RESOLVED"


# ==================================================
# INBOX_NEEDS_ATTENTION (27-30)
# ==================================================


def test_inbox_read_is_resolved():
    state = _inbox(read_at=_NOW)
    assert resolution.evaluate_acted_on("INBOX_NEEDS_ATTENTION", state, _NOW) == "RESOLVED"


def test_inbox_archive_is_resolved():
    state = _inbox(archived_at=_NOW)
    assert resolution.evaluate_acted_on("INBOX_NEEDS_ATTENTION", state, _NOW) == "RESOLVED"


def test_inbox_still_unread_nonarchived_is_not_resolved():
    state = _inbox()
    assert resolution.evaluate_acted_on("INBOX_NEEDS_ATTENTION", state, _NOW) == "NOT_RESOLVED"


def test_inbox_marked_unread_again_is_not_resolved():
    """Marking unread again is a real mutation but RE-OPENS the
    concern — the general recipe handles this with zero special-casing."""
    state = _inbox(read_at=None)
    assert resolution.evaluate_acted_on("INBOX_NEEDS_ATTENTION", state, _NOW) == "NOT_RESOLVED"


# ==================================================
# SAFETY (31-37)
# ==================================================


def test_mismatched_signal_source_cannot_return_resolved():
    event_state = _event(starts_at=_NOW + timedelta(minutes=30))
    with pytest.raises(ActedOnResolutionError):
        resolution.evaluate_acted_on("TASK_OVERDUE", event_state, _NOW)

    task_state = _task(due_at=_NOW + timedelta(hours=1))
    with pytest.raises(ActedOnResolutionError):
        resolution.evaluate_acted_on("EVENT_UPCOMING", task_state, _NOW)

    with pytest.raises(ActedOnResolutionError):
        resolution.evaluate_acted_on("INBOX_NEEDS_ATTENTION", task_state, _NOW)


def test_unsupported_signal_type_raises():
    with pytest.raises(ActedOnResolutionError):
        resolution.evaluate_acted_on("SOMETHING_ELSE", _task(due_at=_NOW), _NOW)  # type: ignore[arg-type]


def test_caller_supplied_now_controls_result():
    state = _task(due_at=_NOW + timedelta(hours=1))
    # Same state, two different `now` values -> two different results.
    assert resolution.evaluate_acted_on("TASK_OVERDUE", state, _NOW) == "RESOLVED"
    assert resolution.evaluate_acted_on("TASK_OVERDUE", state, _NOW + timedelta(hours=2)) == "NOT_RESOLVED"


def _code_lines(module) -> list[str]:
    """Actual executable lines only — excludes module/function/class
    docstrings and comment-only lines, which legitimately discuss (in
    prose) the very things this module's code must never do."""
    lines = []
    for _name, obj in inspect.getmembers(module, lambda o: inspect.isfunction(o) or inspect.isclass(o)):
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        for line in inspect.getsource(obj).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(stripped)
    return lines


def test_no_datetime_now_inside_evaluator():
    lines = _code_lines(resolution)
    docstring_free = [line for line in lines if '"""' not in line]
    joined = "\n".join(docstring_free)
    assert "datetime.now(" not in joined
    assert ".now()" not in joined


def test_no_db_imports_or_operations():
    import_lines = [
        line.strip()
        for line in inspect.getsource(resolution).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("Session", "sqlalchemy"):
        assert not any(forbidden in line for line in import_lines), f"unexpected import of {forbidden!r}"
    for line in _code_lines(resolution):
        for forbidden in ("db.execute", "db.commit", "db.add"):
            assert forbidden not in line, f"unexpected {forbidden!r} in: {line!r}"


def test_no_llm_or_chat_imports():
    import_lines = [
        line.strip()
        for line in inspect.getsource(resolution).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("anthropic", "model_router", "orchestrator", "chat"):
        assert not any(forbidden in line for line in import_lines), (
            f"unexpected import referencing {forbidden!r}: {import_lines}"
        )


def test_module_never_writes_acted_on_at():
    for line in _code_lines(resolution):
        assert "acted_on_at" not in line, f"unexpected acted_on_at reference in code: {line!r}"


def test_production_mutation_services_untouched():
    """Structural proof against accidental scope creep: resolution.py
    has no IMPORT of any domain service module — it consumes only the
    plain TaskMutationState/EventMutationState/InboxMutationState
    dataclasses, never tasks_service/calendar_service/inbox_service
    directly."""
    import_lines = [
        line.strip()
        for line in inspect.getsource(resolution).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("tasks.service", "calendar.service", "inbox.service", "tasks_service", "calendar_service", "inbox_service"):
        assert not any(forbidden in line for line in import_lines), (
            f"unexpected import referencing {forbidden!r}: {import_lines}"
        )
