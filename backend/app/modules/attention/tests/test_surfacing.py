"""Checkpoint 4.5e — APP_OPENED Production Wiring, Revalidation, and
Concurrency. Covers: deciding (zero-cost silence), narrating once,
atomic persistence, every finalization-time silence condition (stale
candidate, user-message-wins, pending-action-appeared,
frequency-became-fresh), a forced final-commit-failure proof, and real
Postgres concurrency proofs (lock not held during narration, two
simultaneous requests).
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.actions import service as actions_service
from app.modules.attention import history, surfacing
from app.modules.attention.models import AttentionExposure
from app.modules.attention.schemas import InvalidTimezoneError
from app.modules.auth import service as auth_service
from app.modules.chat import service as chat_service
from app.modules.chat.models import ChatMessage
from app.modules.model_router import service as model_router_service
from app.modules.model_router.schemas import ToolUseBlock
from app.modules.narration import service as narration_service
from app.modules.narration.schemas import NarrationResult
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate, TaskUpdate

_CAIRO = "Africa/Cairo"


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("surfacing-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    owner = _owner(db_session)
    space = Space(name="Surfacing test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _overdue_task(db_session: Session, space: Space, priority: str = "high"):
    return tasks_service.create_task(
        db_session, space.id,
        TaskCreate(title="Overdue surfacing task", due_at=datetime.now(timezone.utc) - timedelta(days=1), priority=priority),
    )


def _stub_narration(monkeypatch: pytest.MonkeyPatch, result_or_side_effect, captured: dict | None = None):
    def _fake(candidate):
        if captured is not None:
            captured["calls"] = captured.get("calls", 0) + 1
        if callable(result_or_side_effect) and not isinstance(result_or_side_effect, NarrationResult):
            return result_or_side_effect(candidate)
        return result_or_side_effect

    monkeypatch.setattr(narration_service, "generate_app_opened_narration", _fake)


def _fresh_session(db_session: Session) -> Session:
    return sessionmaker(bind=db_session.get_bind())()


_SAFE_RESULT = NarrationResult(text="عندك مهمة متأخرة تستاهل نبص عليها.", source="model")


# ==================================================
# 4.5c SILENCE -> ZERO PROVIDER CALLS, ZERO WRITES
# ==================================================


def test_initial_silence_makes_zero_narration_calls_and_zero_writes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = _space(db_session)  # no signals at all -> no_eligible_candidate
    captured: dict = {}
    _stub_narration(monkeypatch, _SAFE_RESULT, captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "no_eligible_candidate"
    assert captured.get("calls", 0) == 0
    assert db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count() == 0
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 0


# ==================================================
# SPEAK -> NARRATION CALLED ONCE -> SUCCESSFUL SURFACE
# ==================================================


def test_speak_calls_narration_exactly_once_and_surfaces(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)
    captured: dict = {}
    _stub_narration(monkeypatch, _SAFE_RESULT, captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert captured["calls"] == 1
    assert result.status == "surfaced"
    assert result.message is not None
    assert result.message.role == "assistant"
    assert result.message.content == _SAFE_RESULT.text


def test_successful_surface_persists_exactly_one_message_and_one_exposure(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)
    _stub_narration(monkeypatch, _SAFE_RESULT)

    surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count() == 1
    exposures = db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).all()
    assert len(exposures) == 1
    assert exposures[0].surface == "app_opened"


def test_fallback_narration_surfaces_normally(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)
    fallback = NarrationResult(text="عندك مهمة متأخرة: Overdue surfacing task.", source="fallback", fallback_reason="narration_failed")
    _stub_narration(monkeypatch, fallback)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "surfaced"
    assert result.message.content == fallback.text


# ==================================================
# STALE CANDIDATE DURING NARRATION
# ==================================================


def test_stale_candidate_during_narration_produces_silence(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(db_session)
    task = _overdue_task(db_session, space)

    def _side_effect(candidate):
        # Simulate: while narration was "in flight", the task got
        # completed through some other path (e.g. the user marked it
        # done from another device).
        tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(status="done"))
        return _SAFE_RESULT

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "candidate_became_stale"
    assert db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count() == 0
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 0


# ==================================================
# USER MESSAGE WINS
# ==================================================


def test_new_user_message_during_narration_wins(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)

    def _side_effect(candidate):
        chat_service.record_user_message(db_session, space.id, space.user_id, "Ahmed's own message mid-narration")
        return _SAFE_RESULT

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "user_message_won_race"
    # The user's own message remains — only the PROACTIVE opening was discarded.
    assert db_session.query(ChatMessage).filter(
        ChatMessage.space_id == space.id, ChatMessage.role == "user"
    ).count() == 1
    assert db_session.query(ChatMessage).filter(
        ChatMessage.space_id == space.id, ChatMessage.role == "assistant"
    ).count() == 0
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 0


def test_unrelated_assistant_row_during_narration_does_not_trigger_user_message_wins(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locked distinction: USER MESSAGE WINS means a NEW USER message —
    an unrelated assistant row appearing in between (e.g. a concurrent
    duplicate request's own proactive opening) must not, by itself,
    trigger this specific check."""
    space = _space(db_session)
    _overdue_task(db_session, space)

    def _side_effect(candidate):
        chat_service.record_assistant_message(db_session, space.id, space.user_id, "unrelated assistant row")
        return _SAFE_RESULT

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.reason != "user_message_won_race"


# ==================================================
# PENDING ACTION APPEARED DURING NARRATION
# ==================================================


def test_pending_action_appeared_during_narration_produces_silence(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)

    def _side_effect(candidate):
        prompt = chat_service.record_assistant_message(db_session, space.id, space.user_id, "proposal prompt")
        actions_service.create_pending_action(
            db_session, space.id, space.user_id, prompt.id, "create_task", {"title": "Something else"},
        )
        return _SAFE_RESULT

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "pending_action_appeared"
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 0


# ==================================================
# FREQUENCY BECAME FRESH DURING NARRATION
# ==================================================


def test_frequency_became_fresh_during_narration_produces_silence(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)

    def _side_effect(candidate):
        # Simulate another concurrent request already winning and
        # recording a fresh app_opened exposure first, for the SAME
        # selected candidate (which source won is irrelevant to the
        # surface-wide frequency gate — it is keyed on space+surface
        # only, never per-source).
        history.record_exposure(
            db_session, space.id, space.user_id, candidate, "app_opened",
            datetime.now(timezone.utc), _CAIRO, commit=True,
        )
        return _SAFE_RESULT

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "frequency_became_fresh"
    exposures = db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).all()
    assert len(exposures) == 1  # only the simulated "other tab" exposure


# ==================================================
# FINAL COMMIT FAILURE
# ==================================================


def test_final_commit_failure_leaves_nothing_durable(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)
    _stub_narration(monkeypatch, _SAFE_RESULT)

    monkeypatch.setattr(
        db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("forced final commit failure"))
    )

    with pytest.raises(RuntimeError):
        surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    fresh = _fresh_session(db_session)
    try:
        message_count = fresh.query(ChatMessage).filter(ChatMessage.space_id == space.id).count()
        exposure_count = fresh.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count()
    finally:
        fresh.rollback()
        fresh.close()

    assert message_count == 0
    assert exposure_count == 0


# ==================================================
# INVALID TIMEZONE PROPAGATES
# ==================================================


def test_invalid_timezone_propagates_uncaught(db_session: Session) -> None:
    space = _space(db_session)
    _overdue_task(db_session, space)
    with pytest.raises(InvalidTimezoneError):
        surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, "Not/ARealZone")


# ==================================================
# CONCURRENCY — REAL POSTGRES PROOFS
# ==================================================


def test_lock_not_held_during_narration(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves Phase B (narration) holds NO advisory lock: from INSIDE
    the (stubbed) narration call, a second, independent connection's
    pg_try_advisory_xact_lock for the SAME (space_id, user_id) key must
    succeed immediately."""
    space = _space(db_session)
    _overdue_task(db_session, space)
    lock_key = f"{space.id}:{space.user_id}"
    probe_results: dict = {}

    def _side_effect(candidate):
        probe = _fresh_session(db_session)
        try:
            probe_results["acquired_during_narration"] = probe.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": lock_key}
            ).scalar_one()
        finally:
            probe.rollback()
            probe.close()
        return _SAFE_RESULT

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "surfaced"
    assert probe_results["acquired_during_narration"] is True, (
        "a second connection could NOT acquire the advisory lock during narration -- "
        "the lock is being held across provider latency, which is forbidden"
    )


def test_simultaneous_app_opened_requests_only_one_persists(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real two-thread/two-session proof: both requests pass the
    initial 4.5c decision and both narrate, but only one may atomically
    persist — the loser's own finalization-time frequency recheck must
    see the winner's fresh exposure and return silence, with no
    duplicate rows. The fake narration function is patched ONCE, via
    the outer fixture, before either thread starts — never re-patched
    per-thread, since monkeypatch.setattr mutates shared module state
    that both threads' calls would otherwise race on."""
    space = _space(db_session)
    _overdue_task(db_session, space)
    space_id, user_id = space.id, space.user_id

    barrier = threading.Barrier(2)

    def _fake(candidate):
        barrier.wait(timeout=5)  # force both threads to reach finalization at roughly the same time
        return _SAFE_RESULT

    monkeypatch.setattr(narration_service, "generate_app_opened_narration", _fake)

    results: list = [None, None]

    def _run(index: int) -> None:
        session: Session = sessionmaker(bind=db_session.get_bind())()
        try:
            results[index] = surfacing.evaluate_and_surface_app_opened(session, space_id, user_id, _CAIRO)
        finally:
            session.close()

    t1 = threading.Thread(target=_run, args=(0,))
    t2 = threading.Thread(target=_run, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    statuses = sorted(r.status for r in results)
    assert statuses == ["silence", "surfaced"]

    verify = _fresh_session(db_session)
    try:
        assert verify.query(ChatMessage).filter(
            ChatMessage.space_id == space_id, ChatMessage.role == "assistant"
        ).count() == 1
        assert verify.query(AttentionExposure).filter(
            AttentionExposure.space_id == space_id, AttentionExposure.surface == "app_opened"
        ).count() == 1
    finally:
        verify.rollback()
        verify.close()


# ==================================================
# ACTION AUTHORITY
# ==================================================


class _FakeModelResponse:
    def __init__(self, text=None, tool_uses=None, correlation_id="corr_test", stop_reason=None):
        self.text = text
        self.tool_uses = tool_uses or []
        self.correlation_id = correlation_id
        self.stop_reason = stop_reason if stop_reason is not None else ("tool_use" if tool_uses else "end_turn")


def _fake_complete_for_bare_yes(*, purpose, messages, system=None, tools=None, tool_choice=None, correlation_id=None):
    if purpose == "claim_verification":
        return _FakeModelResponse(
            tool_uses=[ToolUseBlock(id="t1", name="certify_claim", input={"claims_bazra_mutation_completed": False})]
        )
    # chat_completion: the model has no actual pending proposal to act on
    # (there is none — the proactive opening created no ProposedAction),
    # so a safe respond_with_text is the realistic model behavior here.
    return _FakeModelResponse(
        tool_uses=[ToolUseBlock(id="t2", name="respond_with_text", input={"kind": "answer", "text": "تمام، قولّي لو حبيت تتكلم فيها."})]
    )


def test_bare_yes_after_proactive_opening_does_not_mutate_domain(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = _space(db_session)
    task = _overdue_task(db_session, space)
    _stub_narration(monkeypatch, NarrationResult(text="عندك مهمة متأخرة. تحب نرتبها؟", source="model"))

    surfaced = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)
    assert surfaced.status == "surfaced"
    assert db_session.query(actions_service.ProposedAction).filter(
        actions_service.ProposedAction.space_id == space.id
    ).count() == 0

    # Stub the provider for the ensuing ordinary chat turn too — no real
    # provider call anywhere in this test (Checkpoint 4.5e brief §30).
    monkeypatch.setattr(model_router_service, "complete", _fake_complete_for_bare_yes)

    user_message, assistant_message = chat_service.send_message(
        db_session, space.id, space.user_id, "أيوه",
        datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(days=1), _CAIRO,
    )

    assert db_session.query(actions_service.ProposedAction).filter(
        actions_service.ProposedAction.space_id == space.id
    ).count() == 0
    db_session.refresh(task)
    assert task.status == "open"
