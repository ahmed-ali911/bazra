import inspect
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.attention import history, resolution, scoring
from app.modules.attention.models import AttentionExposure
from app.modules.attention.schemas import Signal, TaskMutationState
from app.modules.auth import service as auth_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_TZ = "Africa/Cairo"


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("acted-on-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    owner = _owner(db_session)
    space = Space(name="Acted-on test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _overdue_signal(task, measurement_seconds=2 * 24 * 3600) -> Signal:
    return Signal(
        signal_type="TASK_OVERDUE",
        source_type="task",
        source_id=task.id,
        title=task.title,
        relevant_timestamp=task.due_at,
        priority="normal",
        measurement_seconds=measurement_seconds,
        snapshot={"due_at": "2026-05-30T12:00:00+00:00", "status": "open"},
    )


def _record(db_session: Session, space: Space, task, surface="app_opened", surfaced_at=_NOW, tz=_TZ):
    candidate = scoring.score_signal(_overdue_signal(task))
    return history.record_exposure(db_session, space.id, space.user_id, candidate, surface, surfaced_at, tz)


# ==================================================
# Helper tests (1-12)
# ==================================================


def test_no_exposure_is_noop(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)

    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT count(*) FROM attention_exposures WHERE task_id = :id"), {"id": task.id}
    ).scalar_one()
    assert row == 0


def test_latest_exposure_selected_by_surfaced_at_then_id(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    _record(db_session, space, task, surfaced_at=_NOW - timedelta(hours=20))
    latest = _record(db_session, space, task, surfaced_at=_NOW - timedelta(hours=1))

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT id, acted_on_at FROM attention_exposures WHERE task_id = :id ORDER BY id"), {"id": task.id}
    ).mappings().all()
    acted_on = {r["id"]: r["acted_on_at"] for r in row}
    assert acted_on[latest.id] is not None
    older_id = min(acted_on.keys())
    assert acted_on[older_id] is None


def test_newest_exposure_used_across_different_surfaces(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    _record(db_session, space, task, surface="app_opened", surfaced_at=_NOW - timedelta(hours=20))
    latest = _record(db_session, space, task, surface="daily_brief", surfaced_at=_NOW - timedelta(hours=1))

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": latest.id}
    ).scalar_one()
    assert row is not None


def test_latest_already_acted_on_is_noop(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)
    db_session.execute(
        text("UPDATE attention_exposures SET acted_on_at = :ts WHERE id = :id"),
        {"ts": _NOW - timedelta(minutes=5), "id": exposure.id},
    )
    db_session.commit()

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()
    assert row == _NOW - timedelta(minutes=5)  # unchanged, not rewritten


def test_resolved_writes_acted_on_at(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()
    assert row == _NOW


def test_not_resolved_does_not_write(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW - timedelta(hours=1)))
    exposure = _record(db_session, space, task)

    # Still open, due_at still in the past -> NOT_RESOLVED
    state = TaskMutationState(status="open", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()
    assert row is None


def test_unknown_does_not_write(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW))
    # record_exposure requires a VALID timezone_name; simulate a historical
    # NULL-timezone row (pre-4.4c-1 shape) via raw SQL instead, exactly
    # like test_history.py's own pre-existing-row test.
    db_session.execute(
        text(
            "INSERT INTO attention_exposures "
            "(space_id, user_id, task_id, signal_type, surface, policy_version, score, "
            "reason_codes, exposure_snapshot, surfaced_at) "
            "VALUES (:space_id, :user_id, :task_id, 'TASK_DUE_TODAY', 'app_opened', 'test', 40, "
            "'[]'::jsonb, '{}'::jsonb, :surfaced_at)"
        ),
        {"space_id": space.id, "user_id": space.user_id, "task_id": task.id, "surfaced_at": _NOW},
    )
    db_session.commit()
    row_id = db_session.execute(
        text("SELECT id FROM attention_exposures WHERE task_id = :id"), {"id": task.id}
    ).scalar_one()

    state = TaskMutationState(status="open", due_at=task.due_at + timedelta(days=1), archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    acted_on = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": row_id}
    ).scalar_one()
    assert acted_on is None  # UNKNOWN (no timezone_name) -> no write


def test_conditional_write_preserves_first_acted_on_at(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()
    first_value = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()

    # A second attempt, later, must not rewrite it.
    history.attempt_acted_on_attribution(
        db_session, space.id, "task", task.id, state, _NOW + timedelta(hours=1)
    )
    db_session.commit()
    second_value = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()
    assert first_value == second_value == _NOW


def test_evaluator_exception_is_contained_logged_and_swallowed(db_session: Session, monkeypatch) -> None:
    """Verifies logging via a monkeypatched spy on history.logger itself,
    never via pytest's `caplog` plumbing — this test suite's own
    migrations/env.py calls alembic's fileConfig() (session-scoped,
    autouse, triggered by the very first test needing a DB), which uses
    logging.config.fileConfig's default disable_existing_loggers=True
    and disables every application logger not explicitly listed in
    alembic.ini — a genuine, pre-existing, repo-wide quirk unrelated to
    this checkpoint, confirmed directly (logging.getLogger(
    "app.modules.attention.history").disabled is True inside this test
    session). caplog-based assertions are therefore unreliable here;
    spying on the logger call itself sidesteps Python's logging
    plumbing entirely and is not affected by that quirk.
    """
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)

    def _boom(*args, **kwargs):
        raise RuntimeError("forced evaluator failure")

    monkeypatch.setattr(resolution, "evaluate_acted_on", _boom)

    logged_calls = []
    monkeypatch.setattr(history.logger, "exception", lambda msg, *a: logged_calls.append(msg % a if a else msg))

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()  # must still succeed

    assert len(logged_calls) == 1
    assert "acted_on attribution failed" in logged_calls[0]
    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()
    assert row is None


def test_attention_sql_failure_is_contained_logged_and_swallowed(db_session: Session, monkeypatch) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    _record(db_session, space, task)

    original_execute = db_session.execute
    call_count = {"n": 0}

    def _flaky_execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        # Let the SELECT (latest exposure lookup) through, fail only on
        # the later UPDATE attempt.
        if call_count["n"] > 2 and "UPDATE" in str(stmt).upper():
            raise RuntimeError("forced SQL failure inside savepoint")
        return original_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", _flaky_execute)

    logged_calls = []
    monkeypatch.setattr(history.logger, "exception", lambda msg, *a: logged_calls.append(msg % a if a else msg))

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)

    monkeypatch.undo()
    db_session.commit()  # must still succeed after the forced failure

    assert len(logged_calls) == 1
    assert "acted_on attribution failed" in logged_calls[0]


def test_outer_session_remains_usable_after_nested_rollback(db_session: Session, monkeypatch) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    _record(db_session, space, task)

    monkeypatch.setattr(
        resolution, "evaluate_acted_on", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    monkeypatch.undo()

    # Session must still be fully usable for ordinary queries/commits.
    still_usable = db_session.execute(text("SELECT 1")).scalar_one()
    assert still_usable == 1
    db_session.commit()


def test_domain_pending_outer_change_survives_nested_rollback(db_session: Session, monkeypatch) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="original", due_at=_NOW, status="done"))
    _record(db_session, space, task)

    # Make an outer, still-pending change (never flushed/committed yet).
    task.title = "changed-before-attention-failure"

    monkeypatch.setattr(
        resolution, "evaluate_acted_on", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    monkeypatch.undo()

    db_session.commit()
    db_session.refresh(task)
    assert task.title == "changed-before-attention-failure"


# ==================================================
# Concurrency / immutability (36-40)
# ==================================================


def test_concurrent_acted_on_attempts_exactly_one_first_write_wins(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)
    SessionLocal = sessionmaker(bind=db_session.get_bind())
    space_id, task_id, exposure_id = space.id, task.id, exposure.id

    results: list = [None, None]

    def _attempt(index: int, instant: datetime) -> None:
        session: Session = SessionLocal()
        try:
            state = TaskMutationState(status="done", due_at=_NOW, archived_at=None)
            history.attempt_acted_on_attribution(session, space_id, "task", task_id, state, instant)
            session.commit()
            row = session.execute(
                text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure_id}
            ).scalar_one()
            results[index] = row
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


def test_snoozed_until_untouched_by_attribution(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)
    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=2), _NOW)

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT snoozed_until FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()
    assert row == _NOW + timedelta(hours=2)


def test_dismissed_at_untouched_by_attribution(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)
    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW - timedelta(minutes=5))

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    row = db_session.execute(
        text("SELECT dismissed_at FROM attention_exposures WHERE id = :id"), {"id": exposure.id}
    ).scalar_one()
    assert row == _NOW - timedelta(minutes=5)


def test_exposure_core_provenance_untouched_by_attribution(db_session: Session) -> None:
    space = _space(db_session)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="t", due_at=_NOW, status="done"))
    exposure = _record(db_session, space, task)

    before = db_session.execute(
        text(
            "SELECT space_id, user_id, task_id, signal_type, surface, policy_version, score, "
            "reason_codes, exposure_snapshot, timezone_name, surfaced_at "
            "FROM attention_exposures WHERE id = :id"
        ),
        {"id": exposure.id},
    ).mappings().one()

    state = TaskMutationState(status="done", due_at=task.due_at, archived_at=None)
    history.attempt_acted_on_attribution(db_session, space.id, "task", task.id, state, _NOW)
    db_session.commit()

    after = db_session.execute(
        text(
            "SELECT space_id, user_id, task_id, signal_type, surface, policy_version, score, "
            "reason_codes, exposure_snapshot, timezone_name, surfaced_at "
            "FROM attention_exposures WHERE id = :id"
        ),
        {"id": exposure.id},
    ).mappings().one()

    assert dict(before) == dict(after)


def test_updated_at_is_never_used_as_semantic_evidence_by_attribution() -> None:
    """Structural proof: attempt_acted_on_attribution's own code never
    reads or writes updated_at — only acted_on_at is ever set."""
    source_lines = [
        line.strip() for line in inspect.getsource(history.attempt_acted_on_attribution).splitlines()
        if not line.strip().startswith("#") and '"""' not in line
    ]
    joined = "\n".join(source_lines)
    assert "updated_at" not in joined


# ==================================================
# Structural safety
# ==================================================


def test_history_module_has_no_llm_or_scheduler_imports() -> None:
    import_lines = [
        line.strip()
        for line in inspect.getsource(history).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("anthropic", "model_router", "orchestrator", "apscheduler", "celery"):
        assert not any(forbidden in line for line in import_lines), (
            f"unexpected import referencing {forbidden!r}: {import_lines}"
        )


def test_attempt_acted_on_attribution_signature_has_no_commit_parameter() -> None:
    """This function never exposes its own commit=False escape hatch —
    it must always run strictly before the caller's own single commit,
    never independently committable."""
    signature = inspect.signature(history.attempt_acted_on_attribution)
    assert "commit" not in signature.parameters
