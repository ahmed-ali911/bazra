"""Checkpoint 4.5c — Deterministic APP_OPENED Orchestration.

Covers: Proactive Frequency Gate boundary exactness, Moment Quality
(pending action / active conversation) boundary exactness and
isolation, the Winner Invariant, and the zero-side-effect/zero-
migration/zero-provider-call guarantees locked by this checkpoint's own
brief. See app_opened.py's own module docstring for the locked
terminology distinction this suite also indirectly protects (no test
here ever asserts or relies on genuine human-presence detection).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.actions import service as actions_service
from app.modules.attention import app_opened, history, scoring
from app.modules.attention.models import AttentionExposure
from app.modules.attention.schemas import Signal
from app.modules.auth import service as auth_service
from app.modules.chat.models import ChatMessage
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_CAIRO = "Africa/Cairo"


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("app-opened-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    owner = _owner(db_session)
    space = Space(name="App Opened test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _overdue_task_signal(db_session: Session, space: Space, days_overdue: int = 1, priority: str = "high") -> tuple:
    due_at = _NOW - timedelta(days=days_overdue)
    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title=f"Overdue task {days_overdue}d", due_at=due_at, priority=priority)
    )
    signal = Signal(
        signal_type="TASK_OVERDUE",
        source_type="task",
        source_id=task.id,
        title=task.title,
        relevant_timestamp=due_at,
        priority=priority,
        measurement_seconds=days_overdue * 24 * 3600,
        snapshot={"due_at": due_at.isoformat(), "status": "open"},
    )
    return task, signal


def _exposure_at(db_session: Session, space: Space, signal: Signal, surfaced_at: datetime, surface: str = "app_opened") -> AttentionExposure:
    candidate = scoring.score_signal(signal)
    return history.record_exposure(db_session, space.id, space.user_id, candidate, surface, surfaced_at, _CAIRO)


def _chat_message(db_session: Session, space: Space, created_at: datetime, role: str = "user") -> ChatMessage:
    message = ChatMessage(space_id=space.id, user_id=space.user_id, role=role, content="hi", created_at=created_at)
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def _pending_action(db_session: Session, space: Space, expires_at: datetime) -> None:
    """expires_at is compared by the (unmodified) get_latest_pending
    against the DATABASE's real current time (func.now()), never the
    fictional `_NOW` this suite otherwise anchors everything to — so
    callers must pass a value relative to real wall-clock time, not
    relative to `_NOW`."""
    chat_message = _chat_message(db_session, space, _NOW - timedelta(minutes=20))
    actions_service.create_pending_action(
        db_session,
        space.id,
        space.user_id,
        chat_message.id,
        "create_task",
        {"title": "A proposed task"},
    )
    db_session.execute(
        update(actions_service.ProposedAction)
        .where(actions_service.ProposedAction.space_id == space.id)
        .values(expires_at=expires_at)
    )
    db_session.commit()


# ==================================================
# PROACTIVE FREQUENCY GATE — boundary exactness
# ==================================================


def test_no_prior_app_opened_exposure_passes_frequency_gate(db_session: Session) -> None:
    space = _space(db_session)
    assert app_opened._proactive_frequency_gate_passes(db_session, space.id, _NOW) is True


def test_exposure_10_seconds_ago_blocks(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(seconds=10))

    assert app_opened._proactive_frequency_gate_passes(db_session, space.id, _NOW) is False


def test_exposure_29_minutes_59_seconds_ago_blocks(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(minutes=29, seconds=59))

    assert app_opened._proactive_frequency_gate_passes(db_session, space.id, _NOW) is False


def test_exposure_exactly_30_minutes_ago_passes(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(minutes=30))

    assert app_opened._proactive_frequency_gate_passes(db_session, space.id, _NOW) is True


def test_exposure_31_minutes_ago_passes(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(minutes=31))

    assert app_opened._proactive_frequency_gate_passes(db_session, space.id, _NOW) is True


def test_frequency_gate_ignores_daily_brief_surface(db_session: Session) -> None:
    """surface='daily_brief' is a DIFFERENT surface's own exposure history
    — must never count against app_opened's frequency gate."""
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(seconds=10), surface="daily_brief")

    assert app_opened._proactive_frequency_gate_passes(db_session, space.id, _NOW) is True


def test_frequency_gate_scoped_per_space(db_session: Session) -> None:
    space_a = _space(db_session)
    space_b = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space_a)
    _exposure_at(db_session, space_a, signal, _NOW - timedelta(seconds=10))

    assert app_opened._proactive_frequency_gate_passes(db_session, space_b.id, _NOW) is True


def test_frequency_gate_is_global_across_sources_within_surface(db_session: Session) -> None:
    """The frequency gate is deliberately NOT per-source — a recent
    app_opened exposure of task A must block a DIFFERENT source B too."""
    space = _space(db_session)
    _, signal_a = _overdue_task_signal(db_session, space, days_overdue=1)
    _exposure_at(db_session, space, signal_a, _NOW - timedelta(seconds=10))

    # A distinct source (task B) never itself exposed still gets blocked,
    # because the gate is keyed only on (space_id, surface).
    assert app_opened._proactive_frequency_gate_passes(db_session, space.id, _NOW) is False


# ==================================================
# MOMENT QUALITY — pending ProposedAction
# ==================================================


def test_pending_action_blocks(db_session: Session) -> None:
    space = _space(db_session)
    _pending_action(db_session, space, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.outcome == "silence"
    assert decision.reason == "pending_action"


def test_expired_pending_action_does_not_block(db_session: Session) -> None:
    space = _space(db_session)
    _pending_action(db_session, space, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _overdue_task_signal(db_session, space)

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.reason != "pending_action"


def test_pending_action_isolated_per_space(db_session: Session) -> None:
    space_a = _space(db_session)
    space_b = _space(db_session)
    _pending_action(db_session, space_a, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    _overdue_task_signal(db_session, space_b)

    decision = app_opened.evaluate_app_opened(db_session, space_b.id, space_b.user_id, _NOW, _CAIRO)
    assert decision.reason != "pending_action"


# ==================================================
# MOMENT QUALITY — active conversation
# ==================================================


def test_message_10_seconds_old_blocks(db_session: Session) -> None:
    space = _space(db_session)
    _chat_message(db_session, space, _NOW - timedelta(seconds=10))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.outcome == "silence"
    assert decision.reason == "active_conversation"


def test_message_9_minutes_59_seconds_old_blocks(db_session: Session) -> None:
    space = _space(db_session)
    _chat_message(db_session, space, _NOW - timedelta(minutes=9, seconds=59))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.reason == "active_conversation"


def test_message_exactly_10_minutes_old_does_not_block(db_session: Session) -> None:
    space = _space(db_session)
    _chat_message(db_session, space, _NOW - timedelta(minutes=10))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.reason != "active_conversation"


def test_message_11_minutes_old_does_not_block(db_session: Session) -> None:
    space = _space(db_session)
    _chat_message(db_session, space, _NOW - timedelta(minutes=11))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.reason != "active_conversation"


def test_assistant_role_message_also_counts_as_active(db_session: Session) -> None:
    space = _space(db_session)
    _chat_message(db_session, space, _NOW - timedelta(seconds=10), role="assistant")

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.reason == "active_conversation"


def test_active_conversation_isolated_per_space(db_session: Session) -> None:
    space_a = _space(db_session)
    space_b = _space(db_session)
    _chat_message(db_session, space_a, _NOW - timedelta(seconds=10))
    _overdue_task_signal(db_session, space_b)

    decision = app_opened.evaluate_app_opened(db_session, space_b.id, space_b.user_id, _NOW, _CAIRO)
    assert decision.reason != "active_conversation"


def test_active_conversation_isolated_per_user(db_session: Session) -> None:
    space = _space(db_session)
    from app.modules.auth.models import User

    other_user = User(password_hash="not-a-real-hash")
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    message = ChatMessage(
        space_id=space.id, user_id=other_user.id, role="user", content="hi", created_at=_NOW - timedelta(seconds=10)
    )
    db_session.add(message)
    db_session.commit()

    assert app_opened._has_recent_chat_activity(db_session, space.id, space.user_id, _NOW) is False


# ==================================================
# WINNER INVARIANT / ATTENTION SELECTION
# ==================================================


def test_no_signals_at_all_produces_no_eligible_candidate(db_session: Session) -> None:
    space = _space(db_session)
    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.outcome == "silence"
    assert decision.reason == "no_eligible_candidate"


def test_below_threshold_signal_produces_no_eligible_candidate(db_session: Session) -> None:
    space = _space(db_session)
    # low-priority, 0-days-overdue task: 50 + (-10) + 0 = 40 < 45 threshold.
    tasks_service.create_task(
        db_session, space.id, TaskCreate(title="Barely overdue", due_at=_NOW - timedelta(hours=1), priority="low")
    )

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.outcome == "silence"
    assert decision.reason == "no_eligible_candidate"


def test_single_eligible_candidate_is_selected(db_session: Session) -> None:
    space = _space(db_session)
    task, _ = _overdue_task_signal(db_session, space)

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    assert decision.outcome == "speak"
    assert decision.candidate.signal.source_id == task.id


def test_all_candidates_suppressed_produces_silence(db_session: Session) -> None:
    space = _space(db_session)
    task, signal = _overdue_task_signal(db_session, space)
    exposure = _exposure_at(db_session, space, signal, _NOW - timedelta(hours=1))
    # Still within the 12h cooldown relative to _NOW, so this single
    # candidate is suppressed. Advance now past the 30-min frequency
    # gate but keep it within the attention cooldown window.
    later = _NOW + timedelta(minutes=31)

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, later, _CAIRO)
    assert decision.outcome == "silence"
    assert decision.reason == "no_eligible_candidate"
    assert exposure.task_id == task.id


def test_first_suppressed_second_eligible_selects_second(db_session: Session) -> None:
    space = _space(db_session)
    suppressed_task, suppressed_signal = _overdue_task_signal(db_session, space, days_overdue=5, priority="high")
    _exposure_at(db_session, space, suppressed_signal, _NOW - timedelta(hours=1))
    eligible_task, _ = _overdue_task_signal(db_session, space, days_overdue=1, priority="high")

    later = _NOW + timedelta(minutes=31)
    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, later, _CAIRO)

    assert decision.outcome == "speak"
    assert decision.candidate.signal.source_id == eligible_task.id
    assert decision.candidate.signal.source_id != suppressed_task.id


# ==================================================
# NO LOCAL-TIME / QUIET-HOURS GATE
# ==================================================


def test_2am_with_eligible_candidate_still_speaks(db_session: Session) -> None:
    space = _space(db_session)
    two_am = datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc)  # 2 AM Africa/Cairo (UTC+2)
    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title="Overdue at 2am", due_at=two_am - timedelta(days=1), priority="high")
    )

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, two_am, _CAIRO)
    assert decision.outcome == "speak"
    assert decision.candidate.signal.source_id == task.id


# ==================================================
# ERROR PROPAGATION
# ==================================================


def test_invalid_timezone_propagates_uncaught(db_session: Session) -> None:
    from app.modules.attention.schemas import InvalidTimezoneError

    space = _space(db_session)
    _overdue_task_signal(db_session, space)

    with pytest.raises(InvalidTimezoneError):
        app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, "Not/ARealZone")


# ==================================================
# ZERO SIDE EFFECTS
# ==================================================


def test_evaluation_creates_no_attention_exposure_rows(db_session: Session) -> None:
    space = _space(db_session)
    _overdue_task_signal(db_session, space)

    app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    count = db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count()
    assert count == 0


def test_evaluation_creates_no_chat_messages(db_session: Session) -> None:
    space = _space(db_session)
    _overdue_task_signal(db_session, space)

    app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    count = db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count()
    assert count == 0


def test_evaluation_creates_no_proposed_actions(db_session: Session) -> None:
    space = _space(db_session)
    _overdue_task_signal(db_session, space)

    app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    count = db_session.query(actions_service.ProposedAction).filter(
        actions_service.ProposedAction.space_id == space.id
    ).count()
    assert count == 0


def test_evaluation_mutates_no_task_fields(db_session: Session) -> None:
    space = _space(db_session)
    task, _ = _overdue_task_signal(db_session, space)
    before = (task.status, task.updated_at, task.due_at)

    app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    db_session.refresh(task)
    assert (task.status, task.updated_at, task.due_at) == before


def test_repeated_evaluation_is_idempotent_and_creates_no_persistent_state(db_session: Session) -> None:
    space = _space(db_session)
    _overdue_task_signal(db_session, space)

    first = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)
    second = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    assert first.outcome == second.outcome == "speak"
    assert first.candidate.signal.source_id == second.candidate.signal.source_id
    exposure_count = db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count()
    assert exposure_count == 0


def test_no_commit_performed_during_evaluation(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(db_session)
    _overdue_task_signal(db_session, space)
    db_session.commit()  # flush fixture setup, then watch for any commit DURING evaluation

    commits = []
    original_commit = db_session.commit

    def _spy_commit():
        commits.append(True)
        return original_commit()

    monkeypatch.setattr(db_session, "commit", _spy_commit)
    app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    assert commits == []


# ==================================================
# REGRESSION — no provider/narration imports, no new migration
# ==================================================


def test_no_provider_or_model_router_imports_in_app_opened_module() -> None:
    import inspect

    import_lines = [
        line.strip()
        for line in inspect.getsource(app_opened).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("anthropic", "model_router", "orchestrator"):
        assert not any(forbidden in line for line in import_lines), (
            f"unexpected import referencing {forbidden!r}: {import_lines}"
        )


def test_app_opened_decision_is_frozen_dataclass() -> None:
    import dataclasses

    assert dataclasses.is_dataclass(app_opened.AppOpenedDecision)
    assert app_opened.AppOpenedDecision.__dataclass_params__.frozen is True
