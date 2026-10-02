"""Checkpoint 4.7 — the Same-Concern Repeat Gate. Covers the exact 24h
boundary (`app_opened.same_concern_recently_surfaced` in isolation, so
these are never conflated with the unrelated 30-minute global Proactive
Frequency Gate), the independence of the two gates at the full
`evaluate_app_opened` level, concern identity (signal_type/snapshot/
source), snooze/dismiss/resolved precedence, and the locked Winner
Rotation Invariant (a recently-surfaced winner produces SILENCE, never
a cascade to the runner-up).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.modules.attention import app_opened, history, scoring
from app.modules.attention import service as signal_service
from app.modules.attention.schemas import Signal
from app.modules.auth import service as auth_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate, TaskUpdate

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_CAIRO = "Africa/Cairo"


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("same-concern-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    owner = _owner(db_session)
    space = Space(name="Same Concern test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _overdue_task_signal(
    db_session: Session, space: Space, days_overdue: int = 1, priority: str = "high", title: str = "Water the plants",
) -> tuple:
    due_at = _NOW - timedelta(days=days_overdue)
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title=title, due_at=due_at, priority=priority))
    snapshot = {"due_at": signal_service._canonical_timestamp(due_at), "status": "open"}
    signal = Signal(
        signal_type="TASK_OVERDUE", source_type="task", source_id=task.id, title=task.title,
        relevant_timestamp=due_at, priority=priority, measurement_seconds=days_overdue * 24 * 3600,
        snapshot=snapshot,
    )
    return task, signal


def _exposure_at(db_session: Session, space: Space, signal: Signal, surfaced_at: datetime, surface: str = "app_opened"):
    candidate = scoring.score_signal(signal)
    return history.record_exposure(db_session, space.id, space.user_id, candidate, surface, surfaced_at, _CAIRO)


# ==================================================
# ISOLATED GATE BOUNDARY TESTS (never conflated with the 30-min
# Proactive Frequency Gate — called directly, at any `now`)
# ==================================================


def test_no_previous_exposure_gate_passes(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    candidate = scoring.score_signal(signal)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, _NOW) is False


def test_same_unchanged_concern_5_minutes_ago_blocks(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(minutes=5))
    candidate = scoring.score_signal(signal)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, _NOW) is True


def test_same_unchanged_concern_23h59m_ago_blocks(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(hours=23, minutes=59))
    candidate = scoring.score_signal(signal)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, _NOW) is True


def test_same_unchanged_concern_exactly_24h_ago_passes(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(hours=24))
    candidate = scoring.score_signal(signal)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, _NOW) is False


def test_same_unchanged_concern_24h_and_1_second_ago_passes(db_session: Session) -> None:
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(hours=24, seconds=1))
    candidate = scoring.score_signal(signal)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, _NOW) is False


def test_different_signal_type_same_source_does_not_block(db_session: Session) -> None:
    """Section 20.F — a genuinely different concern (different
    signal_type) for the SAME source must not be treated as a repeat."""
    space = _space(db_session)
    task, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(minutes=5))

    due_soon_signal = Signal(
        signal_type="TASK_DUE_SOON", source_type="task", source_id=task.id, title=task.title,
        relevant_timestamp=_NOW + timedelta(hours=1), priority="high", measurement_seconds=3600,
        snapshot={"due_at": signal_service._canonical_timestamp(_NOW + timedelta(hours=1)), "status": "open"},
    )
    candidate = scoring.score_signal(due_soon_signal)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, _NOW) is False


def test_changed_snapshot_same_signal_type_does_not_block(db_session: Session) -> None:
    """Section 20.F / §8 — a material change (due_at pushed out, still
    overdue by a different margin) changes the snapshot, correctly
    classifying this as a different concern, not a repeat."""
    space = _space(db_session)
    task, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(minutes=5))

    new_due_at = _NOW - timedelta(hours=2)  # still overdue, but a materially different due_at
    changed_signal = Signal(
        signal_type="TASK_OVERDUE", source_type="task", source_id=task.id, title=task.title,
        relevant_timestamp=new_due_at, priority="high", measurement_seconds=7200,
        snapshot={"due_at": signal_service._canonical_timestamp(new_due_at), "status": "open"},
    )
    candidate = scoring.score_signal(changed_signal)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, _NOW) is False


def test_different_source_is_not_blocked_by_an_unrelated_sources_exposure(db_session: Session) -> None:
    space = _space(db_session)
    _, signal_a = _overdue_task_signal(db_session, space, title="Task A")
    _exposure_at(db_session, space, signal_a, _NOW - timedelta(minutes=5))

    _, signal_b = _overdue_task_signal(db_session, space, title="Task B")
    candidate_b = scoring.score_signal(signal_b)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate_b, _NOW) is False


# ==================================================
# FULL PIPELINE — GATE INDEPENDENCE (section 20.A / 20.C)
# ==================================================


def test_full_pipeline_no_exposure_candidate_may_speak(db_session: Session) -> None:
    space = _space(db_session)
    _overdue_task_signal(db_session, space)

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    assert decision.outcome == "speak"


def test_global_frequency_passes_but_same_concern_gate_still_blocks(db_session: Session) -> None:
    """Section 20.C — the defining proof that the two gates are
    independent. IMPORTANT real interaction discovered during
    implementation (see this checkpoint's own final report): the
    pre-existing, UNCHANGED per-source 12h COOLDOWN gate
    (scoring._COOLDOWN) already suppresses the exact same source for
    its own first 12 hours, regardless of signal_type/snapshot — so a
    31-minutes-old exposure is not yet a clean isolation of THIS new
    gate at all (the candidate is already excluded upstream, inside
    rank_for_surface, via COOLDOWN, before Winner Invariant even runs).
    The window that genuinely isolates the NEW 24h Same-Concern Repeat
    Gate's own additional effect is (12h, 24h) — here, 15 hours: past
    the pre-existing cooldown, but still within this gate's own window.
    """
    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    _exposure_at(db_session, space, signal, _NOW - timedelta(hours=15))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    assert decision.outcome == "silence"
    assert decision.reason == "same_concern_recently_surfaced"


# ==================================================
# SNOOZE / DISMISS / RESOLVED PRECEDENCE (section 20.H/I/J, section 9)
# ==================================================


def test_snoozed_candidate_remains_suppressed_regardless_of_repeat_window(db_session: Session) -> None:
    space = _space(db_session)
    task, signal = _overdue_task_signal(db_session, space)
    exposure = _exposure_at(db_session, space, signal, _NOW - timedelta(hours=25))  # repeat window alone would pass
    history.record_snooze(db_session, space.id, space.user_id, exposure.id, _NOW + timedelta(hours=2), _NOW)

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    assert decision.outcome == "silence"
    assert decision.reason == "no_eligible_candidate"


def test_dismissed_unchanged_snapshot_remains_suppressed_regardless_of_repeat_window(db_session: Session) -> None:
    space = _space(db_session)
    task, signal = _overdue_task_signal(db_session, space)
    exposure = _exposure_at(db_session, space, signal, _NOW - timedelta(hours=25))
    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, _NOW - timedelta(hours=24, minutes=30))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    assert decision.outcome == "silence"
    assert decision.reason == "no_eligible_candidate"


def test_resolved_concern_does_not_reappear_merely_because_24h_elapsed(db_session: Session) -> None:
    """Marking the task done triggers the existing, unrelated
    Task-completion -> InboxItem side effect, whose own created_at is
    REAL wall-clock time (server_default=func.now()) — so this one test
    must evaluate at real-time-relative `now`, not the fictional _NOW
    every other test in this suite anchors to (the same established
    "test timestamps must be real-time-relative when the code under
    test calls datetime.now() internally" convention from earlier
    checkpoints)."""
    space = _space(db_session)
    real_now = datetime.now(timezone.utc)
    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title="Water the plants", due_at=real_now - timedelta(days=1), priority="high")
    )
    snapshot = {"due_at": signal_service._canonical_timestamp(real_now - timedelta(days=1)), "status": "open"}
    signal = Signal(
        signal_type="TASK_OVERDUE", source_type="task", source_id=task.id, title=task.title,
        relevant_timestamp=real_now - timedelta(days=1), priority="high", measurement_seconds=86400,
        snapshot=snapshot,
    )
    _exposure_at(db_session, space, signal, real_now - timedelta(hours=30))
    tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(status="done"))

    # Captured AFTER the done-transition's own InboxItem side effect
    # (whose created_at is the DATABASE's own now(), not Python's) —
    # using the earlier `real_now` here risks a millisecond-level race
    # where the DB's row is a hair newer than Python's own captured
    # instant, producing a spurious negative measurement_seconds.
    evaluation_now = datetime.now(timezone.utc)
    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, evaluation_now, _CAIRO)

    assert decision.outcome == "silence"
    assert decision.reason == "no_eligible_candidate"


# ==================================================
# WINNER ROTATION INVARIANT (section 21 — mandatory)
# ==================================================


def test_recently_surfaced_winner_produces_silence_not_runner_up(db_session: Session) -> None:
    space = _space(db_session)
    # A: strongest (high priority, 6 days overdue -> 50+20+30=100).
    task_a, signal_a = _overdue_task_signal(db_session, space, days_overdue=6, priority="high", title="Task A")
    # B: second (high priority, 3 days overdue -> 50+20+15=85).
    _, signal_b = _overdue_task_signal(db_session, space, days_overdue=3, priority="high", title="Task B")
    # C: third (normal priority, 1 day overdue -> 50+0+5=55).
    _, signal_c = _overdue_task_signal(db_session, space, days_overdue=1, priority="normal", title="Task C")

    assert scoring.score_signal(signal_a).score > scoring.score_signal(signal_b).score > scoring.score_signal(signal_c).score

    # A was already proactively surfaced recently (same unchanged
    # concern) — 15h ago: past the pre-existing 12h per-source COOLDOWN
    # (so A is NOT excluded upstream by that unrelated, unchanged gate —
    # it genuinely reaches Winner Invariant and becomes the selected
    # winner), but still within THIS gate's own 24h window. See the
    # comment on test_global_frequency_passes_but_same_concern_gate_still_blocks
    # for why an earlier exposure would not actually isolate this gate.
    _exposure_at(db_session, space, signal_a, _NOW - timedelta(hours=15))

    decision = app_opened.evaluate_app_opened(db_session, space.id, space.user_id, _NOW, _CAIRO)

    assert decision.outcome == "silence"
    assert decision.reason == "same_concern_recently_surfaced"
    # Explicitly NOT candidate B or C — no cascade/rotation.
    assert decision.candidate is None
