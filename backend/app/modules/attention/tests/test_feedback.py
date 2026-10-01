import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.modules.attention import history, scoring
from app.modules.attention.history import AttentionOwnershipError, InvalidSnoozeInstantError
from app.modules.attention.schemas import Signal
from app.modules.auth import service as auth_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("feedback-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    owner = _owner(db_session)
    space = Space(name="Feedback test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _exposure(db_session: Session, space: Space, due_at=_NOW, measurement_seconds=2 * 24 * 3600):
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Feedback task", due_at=due_at))
    signal = Signal(
        signal_type="TASK_OVERDUE",
        source_type="task",
        source_id=task.id,
        title=task.title,
        relevant_timestamp=due_at,
        priority="normal",
        measurement_seconds=measurement_seconds,
        snapshot={"due_at": "2026-05-30T12:00:00+00:00", "status": "open"},
    )
    candidate = scoring.score_signal(signal)
    exposure = history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW, "Africa/Cairo")
    return task, signal, exposure


# ==================================================
# DISMISS
# ==================================================


def test_first_dismiss_stores_exactly_supplied_now(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    dismiss_instant = _NOW + timedelta(minutes=5)

    result = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, dismiss_instant)

    assert result.dismissed_at == dismiss_instant


def test_duplicate_dismiss_is_true_noop(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    first_instant = _NOW + timedelta(minutes=5)

    first = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, first_instant)
    second = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, first_instant + timedelta(minutes=1))

    assert first.dismissed_at == second.dismissed_at == first_instant


def test_dismissed_at_unchanged_on_duplicate(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    first_instant = _NOW + timedelta(minutes=5)

    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, first_instant)
    result = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=3))

    assert result.dismissed_at == first_instant


def test_updated_at_unchanged_on_duplicate_dismiss(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    first = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))
    updated_at_after_first = first.updated_at

    second = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=3))

    assert second.updated_at == updated_at_after_first


def test_dismiss_cross_space_rejected(db_session: Session) -> None:
    space_a = _space(db_session)
    space_b = _space(db_session)
    _, _, exposure = _exposure(db_session, space_a)

    with pytest.raises(AttentionOwnershipError):
        history.record_dismiss(db_session, space_b.id, space_b.user_id, exposure.id, _NOW)


def test_dismiss_cross_user_rejected(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    from app.modules.auth.models import User

    impostor = User(password_hash="not-a-real-hash")
    db_session.add(impostor)
    db_session.commit()
    db_session.refresh(impostor)

    with pytest.raises(AttentionOwnershipError):
        history.record_dismiss(db_session, space.id, impostor.id, exposure.id, _NOW)


def test_dismiss_nonexistent_exposure_rejected(db_session: Session) -> None:
    space = _space(db_session)

    with pytest.raises(AttentionOwnershipError):
        history.record_dismiss(db_session, space.id, space.user_id, 999999999, _NOW)


def test_concurrent_duplicate_dismiss_has_exactly_one_first_write(db_session: Session) -> None:
    """Real two-thread Postgres test, mirroring test_actions_concurrency.py's
    own pattern — proves the conditional UPDATE actually serializes via a
    real row lock, not merely in application code."""
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    SessionLocal = sessionmaker(bind=db_session.get_bind())
    space_id, user_id, exposure_id = space.id, space.user_id, exposure.id

    results: list = [None, None]

    def _attempt(index: int, instant: datetime) -> None:
        session: Session = SessionLocal()
        try:
            result = history.record_dismiss(session, space_id, user_id, exposure_id, instant)
            results[index] = result.dismissed_at  # read while still attached, before closing
        finally:
            session.close()

    t1 = threading.Thread(target=_attempt, args=(0, _NOW + timedelta(seconds=1)))
    t2 = threading.Thread(target=_attempt, args=(1, _NOW + timedelta(seconds=2)))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results[0] == results[1]
    assert results[0] in (_NOW + timedelta(seconds=1), _NOW + timedelta(seconds=2))


# ==================================================
# SNOOZE
# ==================================================


def test_first_snooze_stores_exact_future_timestamp(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    snoozed_until = _NOW + timedelta(hours=2)

    result = history.record_snooze(db_session, space.id, space.user_id, exposure.id, snoozed_until, _NOW)

    assert result.snoozed_until == snoozed_until


def test_exact_duplicate_snooze_is_true_noop(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    snoozed_until = _NOW + timedelta(hours=2)

    history.record_snooze(db_session, space.id, space.user_id, exposure.id, snoozed_until, _NOW)
    second = history.record_snooze(
        db_session, space.id, space.user_id, exposure.id, snoozed_until, _NOW + timedelta(minutes=1)
    )

    assert second.snoozed_until == snoozed_until


def test_updated_at_unchanged_on_exact_duplicate_snooze(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    snoozed_until = _NOW + timedelta(hours=2)

    first = history.record_snooze(db_session, space.id, space.user_id, exposure.id, snoozed_until, _NOW)
    updated_at_after_first = first.updated_at

    second = history.record_snooze(
        db_session, space.id, space.user_id, exposure.id, snoozed_until, _NOW + timedelta(minutes=1)
    )

    assert second.updated_at == updated_at_after_first


def test_re_snooze_to_different_later_instant_succeeds(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=2), _NOW)
    result = history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=5), _NOW)

    assert result.snoozed_until == _NOW + timedelta(hours=5)


def test_re_snooze_to_different_earlier_but_still_future_instant_succeeds(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=5), _NOW)
    result = history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=1), _NOW)

    assert result.snoozed_until == _NOW + timedelta(hours=1)


def test_snoozed_until_equal_to_now_rejected(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    with pytest.raises(InvalidSnoozeInstantError):
        history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW, _NOW)


def test_snoozed_until_before_now_rejected(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    with pytest.raises(InvalidSnoozeInstantError):
        history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW - timedelta(minutes=1), _NOW)


def test_invalid_snooze_produces_zero_mutation(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    with pytest.raises(InvalidSnoozeInstantError):
        history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW, _NOW)

    row = history.load_suppression_states(db_session, space.id, [("task", exposure.task_id)])
    assert row[("task", exposure.task_id)].snoozed_until is None


def test_snooze_cross_space_rejected(db_session: Session) -> None:
    space_a = _space(db_session)
    space_b = _space(db_session)
    _, _, exposure = _exposure(db_session, space_a)

    with pytest.raises(AttentionOwnershipError):
        history.record_snooze(db_session, space_b.id, space_b.user_id, exposure.id, _NOW + timedelta(hours=1), _NOW)


def test_snooze_cross_user_rejected(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    from app.modules.auth.models import User

    impostor = User(password_hash="not-a-real-hash")
    db_session.add(impostor)
    db_session.commit()
    db_session.refresh(impostor)

    with pytest.raises(AttentionOwnershipError):
        history.record_snooze(db_session, space.id, impostor.id, exposure.id, _NOW + timedelta(hours=1), _NOW)


def test_snooze_nonexistent_exposure_rejected(db_session: Session) -> None:
    space = _space(db_session)

    with pytest.raises(AttentionOwnershipError):
        history.record_snooze(db_session, space.id, space.user_id, 999999999, _NOW + timedelta(hours=1), _NOW)


# ==================================================
# COEXISTENCE
# ==================================================


def test_snooze_then_dismiss_preserves_both(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=2), _NOW)
    result = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))

    assert result.snoozed_until == _NOW + timedelta(hours=2)
    assert result.dismissed_at == _NOW + timedelta(minutes=5)


def test_dismiss_then_snooze_preserves_both(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))
    result = history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=2), _NOW)

    assert result.dismissed_at == _NOW + timedelta(minutes=5)
    assert result.snoozed_until == _NOW + timedelta(hours=2)


def test_active_snooze_wins_gate_precedence(db_session: Session) -> None:
    space = _space(db_session)
    task, signal, exposure = _exposure(db_session, space)

    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=2), _NOW)
    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    ranked = scoring.rank_for_surface([signal], _NOW + timedelta(hours=1), "daily_brief", states)
    assert ranked[0].suppression.suppressed is True
    assert ranked[0].suppression.reason_code == "SNOOZED"


def test_after_snooze_expiry_unchanged_snapshot_produces_dismissed_unchanged(db_session: Session) -> None:
    space = _space(db_session)
    task, signal, exposure = _exposure(db_session, space)

    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=2), _NOW)
    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    # Evaluate AFTER the snooze window has expired, with the SAME unchanged signal.
    ranked = scoring.rank_for_surface([signal], _NOW + timedelta(hours=3), "daily_brief", states)
    assert ranked[0].suppression.suppressed is True
    assert ranked[0].suppression.reason_code == "DISMISSED_UNCHANGED"


# ==================================================
# STALE HISTORY
# ==================================================


def test_feedback_on_older_exposure_persists_on_that_historical_row(db_session: Session) -> None:
    space = _space(db_session)
    task, signal, exposure_a = _exposure(db_session, space)
    candidate = scoring.score_signal(signal)
    exposure_b = history.record_exposure(
        db_session, space.id, space.user_id, candidate, "app_opened", _NOW + timedelta(hours=12), "Africa/Cairo")

    # Delayed feedback arrives for the OLDER exposure A.
    result = history.record_dismiss(db_session, space.id, space.user_id, exposure_a.id, _NOW + timedelta(hours=20))
    assert result.dismissed_at == _NOW + timedelta(hours=20)
    assert exposure_b.id != exposure_a.id


def test_newer_exposure_remains_current_suppression_authority(db_session: Session) -> None:
    space = _space(db_session)
    task, signal, exposure_a = _exposure(db_session, space)
    candidate = scoring.score_signal(signal)
    exposure_b = history.record_exposure(
        db_session, space.id, space.user_id, candidate, "app_opened", _NOW + timedelta(hours=12), "Africa/Cairo")

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    assert states[("task", task.id)].surfaced_at == exposure_b.surfaced_at


def test_old_feedback_cannot_suppress_through_load_suppression_states_when_newer_exposure_exists(
    db_session: Session,
) -> None:
    space = _space(db_session)
    task, signal, exposure_a = _exposure(db_session, space)
    candidate = scoring.score_signal(signal)
    history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", _NOW + timedelta(hours=12), "Africa/Cairo")

    # Dismiss the OLDER exposure A after B already exists.
    history.record_dismiss(db_session, space.id, space.user_id, exposure_a.id, _NOW + timedelta(hours=20))

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    # Must reflect exposure B (no dismissal), not A's dismissal.
    assert states[("task", task.id)].dismissed_at is None


# ==================================================
# IMMUTABILITY
# ==================================================


def test_dismiss_changes_none_of_the_immutable_exposure_core_fields(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    before = {
        "space_id": exposure.space_id,
        "user_id": exposure.user_id,
        "task_id": exposure.task_id,
        "event_id": exposure.event_id,
        "inbox_item_id": exposure.inbox_item_id,
        "signal_type": exposure.signal_type,
        "surface": exposure.surface,
        "policy_version": exposure.policy_version,
        "score": exposure.score,
        "reason_codes": exposure.reason_codes,
        "exposure_snapshot": exposure.exposure_snapshot,
        "surfaced_at": exposure.surfaced_at,
    }

    result = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))

    assert before == {
        "space_id": result.space_id,
        "user_id": result.user_id,
        "task_id": result.task_id,
        "event_id": result.event_id,
        "inbox_item_id": result.inbox_item_id,
        "signal_type": result.signal_type,
        "surface": result.surface,
        "policy_version": result.policy_version,
        "score": result.score,
        "reason_codes": result.reason_codes,
        "exposure_snapshot": result.exposure_snapshot,
        "surfaced_at": result.surfaced_at,
    }


def test_snooze_changes_none_of_the_immutable_exposure_core_fields(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)
    before = {
        "space_id": exposure.space_id,
        "user_id": exposure.user_id,
        "task_id": exposure.task_id,
        "signal_type": exposure.signal_type,
        "surface": exposure.surface,
        "policy_version": exposure.policy_version,
        "score": exposure.score,
        "reason_codes": exposure.reason_codes,
        "exposure_snapshot": exposure.exposure_snapshot,
        "surfaced_at": exposure.surfaced_at,
    }

    result = history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=1), _NOW)

    assert before == {
        "space_id": result.space_id,
        "user_id": result.user_id,
        "task_id": result.task_id,
        "signal_type": result.signal_type,
        "surface": result.surface,
        "policy_version": result.policy_version,
        "score": result.score,
        "reason_codes": result.reason_codes,
        "exposure_snapshot": result.exposure_snapshot,
        "surfaced_at": result.surfaced_at,
    }


def test_acted_on_at_remains_untouched_by_feedback(db_session: Session) -> None:
    space = _space(db_session)
    _, _, exposure = _exposure(db_session, space)

    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=1), _NOW)
    result = history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))

    assert result.acted_on_at is None


def test_updated_at_is_not_consumed_as_attention_evidence(db_session: Session) -> None:
    """updated_at legitimately bumps on a genuine feedback write — the
    locked principle is that it is never CONSULTED as evidence, not
    that it never changes. load_suppression_states (already proven in
    test_history.py) never reads it."""
    space = _space(db_session)
    task, _, exposure = _exposure(db_session, space)

    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(minutes=5))

    states = history.load_suppression_states(db_session, space.id, [("task", task.id)])
    assert states[("task", task.id)].dismissed_at == _NOW + timedelta(minutes=5)


# ==================================================
# REGRESSION (no migration / no provider imports)
# ==================================================


def test_no_provider_or_chat_imports_in_history_module() -> None:
    import inspect

    import_lines = [
        line.strip()
        for line in inspect.getsource(history).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("anthropic", "model_router", "orchestrator", "chat"):
        assert not any(forbidden in line for line in import_lines), (
            f"unexpected import referencing {forbidden!r}: {import_lines}"
        )
