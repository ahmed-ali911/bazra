import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.attention import history, scoring
from app.modules.attention.history import AttentionExposureError, AttentionOwnershipError
from app.modules.attention.models import AttentionExposure
from app.modules.attention.schemas import Signal
from app.modules.auth import service as auth_service
from app.modules.calendar import service as calendar_service
from app.modules.calendar.schemas import CalendarEventCreate
from app.modules.inbox import service as inbox_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("history-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    """A brand-new, empty Space per call — the test DB persists across
    the whole session, so (as in test_attention.py) every test needs
    its own isolated space rather than the shared default."""
    owner = _owner(db_session)
    space = Space(name="History test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _task_signal(task, priority="normal", measurement_seconds=2 * 24 * 3600):
    return Signal(
        signal_type="TASK_OVERDUE",
        source_type="task",
        source_id=task.id,
        title=task.title,
        relevant_timestamp=task.due_at,
        priority=priority,
        measurement_seconds=measurement_seconds,
        snapshot={"due_at": "2026-05-30T12:00:00+00:00", "status": "open"},
    )


def _event_signal(event, measurement_seconds=30 * 60):
    return Signal(
        signal_type="EVENT_UPCOMING",
        source_type="calendar_event",
        source_id=event.id,
        title=event.title,
        relevant_timestamp=event.starts_at,
        priority=None,
        measurement_seconds=measurement_seconds,
        snapshot={"starts_at": "2026-06-01T12:30:00+00:00"},
    )


def _inbox_signal(item, measurement_seconds=3 * 3600):
    return Signal(
        signal_type="INBOX_NEEDS_ATTENTION",
        source_type="inbox_item",
        source_id=item.id,
        title=item.title,
        relevant_timestamp=item.created_at,
        priority=None,
        measurement_seconds=measurement_seconds,
        snapshot={},
    )


# ---- 1/2: migration / constraint ----


def test_migration_created_table_with_expected_columns(db_session: Session) -> None:
    row = db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'attention_exposures' ORDER BY column_name"
        )
    ).scalars().all()
    expected = {
        "id", "space_id", "user_id", "task_id", "event_id", "inbox_item_id",
        "signal_type", "surface", "policy_version", "score", "reason_codes",
        "exposure_snapshot", "surfaced_at", "snoozed_until", "dismissed_at",
        "acted_on_at", "created_at", "updated_at",
    }
    assert expected.issubset(set(row))


def test_exactly_one_source_fk_constraint_blocks_zero_sources(db_session: Session) -> None:
    space = _space(db_session)
    exposure = AttentionExposure(
        space_id=space.id,
        user_id=space.user_id,
        task_id=None,
        event_id=None,
        inbox_item_id=None,
        signal_type="TASK_OVERDUE",
        surface="app_opened",
        policy_version="test",
        score=50,
        reason_codes=[],
        exposure_snapshot={},
        surfaced_at=_NOW,
    )
    db_session.add(exposure)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_exactly_one_source_fk_constraint_blocks_two_sources(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    event = calendar_service.create_calendar_event(
        db_session, space.id, CalendarEventCreate(title="e", starts_at=_NOW)
    )
    exposure = AttentionExposure(
        space_id=space.id,
        user_id=space.user_id,
        task_id=task.id,
        event_id=event.id,
        inbox_item_id=None,
        signal_type="TASK_OVERDUE",
        surface="app_opened",
        policy_version="test",
        score=50,
        reason_codes=[],
        exposure_snapshot={},
        surfaced_at=_NOW,
    )
    db_session.add(exposure)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---- 3/4/5: valid persistence per source type ----


def test_valid_task_exposure_persistence(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Overdue task", due_at=_NOW, priority="high"))
    candidate = scoring.score_signal(_task_signal(task, priority="high"))

    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)

    assert exposure.task_id == task.id
    assert exposure.event_id is None
    assert exposure.inbox_item_id is None


def test_valid_event_exposure_persistence(db_session: Session) -> None:
    space = _space(db_session)
    event = calendar_service.create_calendar_event(
        db_session, space.id, CalendarEventCreate(title="Meeting", starts_at=_NOW + timedelta(minutes=30))
    )
    candidate = scoring.score_signal(_event_signal(event))

    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "daily_brief", _NOW)

    assert exposure.event_id == event.id
    assert exposure.task_id is None
    assert exposure.inbox_item_id is None


def test_valid_inbox_exposure_persistence(db_session: Session) -> None:
    space = _space(db_session)
    item = inbox_service.create_item(db_session, space.id, task_id=None, title="Unread item")
    candidate = scoring.score_signal(_inbox_signal(item))

    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "daily_brief", _NOW)

    assert exposure.inbox_item_id == item.id
    assert exposure.task_id is None
    assert exposure.event_id is None


# ---- 6/7: ownership ----


def test_cross_space_source_cannot_be_recorded(db_session: Session) -> None:
    owner = _owner(db_session)
    space_a = _space(db_session)
    space_b = _space(db_session)
    task_in_b = tasks_service.create_task(db_session, space_b.id, TaskCreate(title="Belongs to B", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task_in_b))

    with pytest.raises(AttentionOwnershipError):
        history.record_exposure(db_session, space_a.id, owner.id, candidate, "app_opened", _NOW)


def test_cross_user_ownership_mismatch_cannot_be_recorded(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))

    from app.modules.auth.models import User

    impostor = User(password_hash="not-a-real-hash")
    db_session.add(impostor)
    db_session.commit()
    db_session.refresh(impostor)

    with pytest.raises(AttentionOwnershipError):
        history.record_exposure(db_session, space.id, impostor.id, candidate, "app_opened", _NOW)


def test_unsupported_surface_rejected(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))

    with pytest.raises(AttentionExposureError):
        history.record_exposure(db_session, space.id, space.user_id, candidate, "push_notification", _NOW)  # type: ignore[arg-type]


# ---- 8-13: exact field preservation ----


def test_candidate_score_and_provenance_preserved_exactly(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, priority="high"))
    signal = _task_signal(task, priority="high", measurement_seconds=5 * 24 * 3600)
    candidate = scoring.score_signal(signal)

    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)

    assert exposure.score == candidate.score == 95  # 50 base + 20 high-priority + 25 overdue (5 days, capped at 30)
    expected_reason_codes = [
        {"code": c.code, "value": c.value, "score_delta": c.score_delta} for c in candidate.reason_codes
    ]
    assert exposure.reason_codes == expected_reason_codes
    assert exposure.exposure_snapshot == signal.snapshot
    assert exposure.policy_version == scoring.POLICY_VERSION
    assert exposure.surface == "app_opened"
    assert exposure.surfaced_at == _NOW
    assert exposure.signal_type == "TASK_OVERDUE"


def test_surfaced_at_is_exactly_the_caller_supplied_instant_not_now(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))
    explicit_delivery_instant = _NOW - timedelta(hours=3)

    exposure = history.record_exposure(
        db_session, space.id, space.user_id, candidate, "app_opened", explicit_delivery_instant
    )

    assert exposure.surfaced_at == explicit_delivery_instant
    assert exposure.surfaced_at != _NOW


# ---- 14: repeated real deliveries ----


def test_repeated_real_deliveries_create_distinct_rows(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))

    first = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)
    second = history.record_exposure(
        db_session, space.id, space.user_id, candidate, "app_opened", _NOW + timedelta(hours=12)
    )

    assert first.id != second.id
    count = db_session.execute(
        text("SELECT count(*) FROM attention_exposures WHERE task_id = :id"), {"id": task.id}
    ).scalar_one()
    assert count == 2


# ---- 15: ranking alone creates no row ----


def test_scoring_and_ranking_alone_creates_no_row(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    signal = _task_signal(task)

    scoring.score_signal(signal)
    scoring.rank_for_surface([signal], _NOW, "app_opened")

    count = db_session.execute(
        text("SELECT count(*) FROM attention_exposures WHERE task_id = :id"), {"id": task.id}
    ).scalar_one()
    assert count == 0


# ---- 16/17: load_suppression_states ----


def test_load_suppression_states_chooses_latest_by_surfaced_at_then_id(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))

    history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW - timedelta(hours=20))
    latest = history.record_exposure(db_session, space.id, space.user_id, candidate, "daily_brief", _NOW - timedelta(hours=1))

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    assert states[("task", task.id)].surfaced_at == latest.surfaced_at


def test_suppression_state_is_global_across_surfaces(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))

    history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW - timedelta(hours=2))

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    assert states[("task", task.id)].surfaced_at == _NOW - timedelta(hours=2)


def test_load_suppression_states_batches_without_n_plus_one(db_session: Session) -> None:
    space = _space(db_session)
    tasks = [tasks_service.create_task(db_session, space.id, TaskCreate(title=f"t{i}", due_at=_NOW)) for i in range(5)]
    for task in tasks:
        candidate = scoring.score_signal(_task_signal(task))
        history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW - timedelta(hours=1))

    identities = [("task", t.id) for t in tasks]
    states = history.load_suppression_states(db_session, space.id, identities)
    assert len(states) == 5
    for task in tasks:
        assert ("task", task.id) in states


def test_unknown_identity_is_simply_absent_from_result(db_session: Session) -> None:
    space = _space(db_session)
    states = history.load_suppression_states(db_session, space.id, [("task", 999999999)])
    assert states == {}


# ---- 18: cooldown integration proof ----


def test_persisted_surfaced_at_drives_cooldown_11h59_suppressed(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, priority="normal"))
    signal = _task_signal(task, priority="normal", measurement_seconds=5 * 24 * 3600)  # score 75
    candidate = scoring.score_signal(signal)
    history.record_exposure(
        db_session, space.id, space.user_id, candidate, "app_opened", _NOW - timedelta(hours=11, minutes=59)
    )

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    ranked = scoring.rank_for_surface([signal], _NOW, "app_opened", states)
    assert ranked[0].suppression.suppressed is True
    assert ranked[0].suppression.reason_code == "COOLDOWN"


def test_persisted_surfaced_at_drives_cooldown_exactly_12h_eligible(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, priority="normal"))
    signal = _task_signal(task, priority="normal", measurement_seconds=5 * 24 * 3600)  # score 75
    candidate = scoring.score_signal(signal)
    history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW - timedelta(hours=12))

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    ranked = scoring.rank_for_surface([signal], _NOW, "app_opened", states)
    assert ranked[0].suppression.suppressed is False


# ---- 19: feedback fields map into SuppressionState (fixture-populated, no mutation API) ----


def test_snoozed_until_populated_in_fixture_maps_into_suppression_state(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))
    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)

    exposure.snoozed_until = _NOW + timedelta(hours=2)
    db_session.commit()

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    assert states[("task", task.id)].snoozed_until == _NOW + timedelta(hours=2)


def test_dismissed_at_populated_in_fixture_maps_dismissed_snapshot_from_exposure_snapshot(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    signal = _task_signal(task)
    candidate = scoring.score_signal(signal)
    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)

    exposure.dismissed_at = _NOW + timedelta(minutes=5)
    db_session.commit()

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    state = states[("task", task.id)]
    assert state.dismissed_at == _NOW + timedelta(minutes=5)
    assert state.dismissed_snapshot == signal.snapshot
    assert state.dismissed_snapshot == exposure.exposure_snapshot


def test_acted_on_at_populated_in_fixture_maps_into_suppression_state(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))
    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)

    exposure.acted_on_at = _NOW + timedelta(minutes=10)
    db_session.commit()

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    assert states[("task", task.id)].acted_on_at == _NOW + timedelta(minutes=10)


# ---- 20: updated_at is never consulted ----


def test_updated_at_is_not_consulted_by_suppression_state_construction(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))
    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)

    # Bump updated_at via an UNRELATED touch (not any of the 4 semantic
    # feedback fields) and confirm suppression state is unaffected.
    db_session.execute(
        text("UPDATE attention_exposures SET updated_at = :ts WHERE id = :id"),
        {"ts": _NOW + timedelta(days=1), "id": exposure.id},
    )
    db_session.commit()

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    state = states[("task", task.id)]
    assert state.snoozed_until is None
    assert state.dismissed_at is None
    assert state.acted_on_at is None


def test_source_contains_no_code_reference_to_updated_at() -> None:
    """Checks actual CODE, not the module's own prose docstrings (which
    legitimately discuss updated_at to explain why it's excluded) — a
    real reference would be an attribute access like `.updated_at` or
    `"updated_at"` used as a dict/column key in a non-comment line."""
    for name, func in inspect.getmembers(history, inspect.isfunction):
        if func.__module__ != history.__name__:
            continue
        source_lines = inspect.getsource(func).splitlines()
        code_lines = [line for line in source_lines if not line.strip().startswith(("#", '"""', "'''"))]
        for line in code_lines:
            assert ".updated_at" not in line, f"unexpected .updated_at reference in {name}: {line!r}"


# ---- 21/22: structural zero-LLM / zero-chat proof ----


def test_source_has_no_llm_or_chat_imports() -> None:
    """Checks actual import statements, not prose — this module's own
    docstrings legitimately mention "ChatMessage"/"chat_service" by name
    to explain what Checkpoint 4.4a deliberately does NOT touch."""
    import_lines = [
        line.strip()
        for line in inspect.getsource(history).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("anthropic", "model_router", "orchestrator", "chat"):
        assert not any(forbidden in line for line in import_lines), (
            f"unexpected import referencing {forbidden!r} in history.py: {import_lines}"
        )


# ---- 24/25: no ACTED_ON evaluation, no snooze/dismiss mutation API ----


def test_record_exposure_never_sets_acted_on_at(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    candidate = scoring.score_signal(_task_signal(task))

    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW)

    assert exposure.acted_on_at is None
    assert exposure.snoozed_until is None
    assert exposure.dismissed_at is None


def test_no_acted_on_mutation_functions_exist_yet() -> None:
    """record_snooze/record_dismiss are now implemented (Checkpoint
    4.4b) — this guard narrows to just the still-deferred ACTED_ON
    evaluator (Checkpoint 4.4c)."""
    assert not hasattr(history, "evaluate_and_record_acted_on")
    assert not hasattr(history, "mark_acted_on")
