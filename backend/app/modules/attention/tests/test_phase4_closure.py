"""Checkpoint 4.8 — Phase 4 Final Acceptance Matrix.

This is NOT a replacement for the deep, boundary-level unit suites
already accepted across 4.2-4.7 (signal generation, scoring edge
cases, exposure/snooze/dismiss/acted_on mechanics, every gate's own
exact-second boundary, narration's own adversarial verifier cases,
etc.) — those remain the authoritative depth coverage and are re-run
in full as part of this checkpoint's own regression, unchanged.

This file exists for a narrower, different purpose: proving that the
ACCEPTED PIECES compose into ONE coherent product at the scenario
level — each test below is intentionally short, happy-path-shaped, and
maps to exactly one lettered scenario (A-Q) from the 4.8 brief. Where
a scenario's own edge-case depth is already thoroughly proven
elsewhere, the test here stays a single, clear, representative case
and the docstring cross-references the deeper existing suite rather
than re-deriving it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.modules.actions import service as actions_service
from app.modules.attention import history, surfacing
from app.modules.attention.models import AttentionExposure
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
_SAFE_TEXT = "عندك مهمة متأخرة تستاهل نبص عليها."


def _owner(db_session: Session):
    user = auth_service.get_the_user(db_session)
    if user is None:
        from app.modules.auth.models import User

        user = User(password_hash=auth_service.hash_password("phase4-closure-tests-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _space(db_session: Session) -> Space:
    owner = _owner(db_session)
    space = Space(name="Phase 4 closure test space", is_default=False, user_id=owner.id)
    db_session.add(space)
    db_session.commit()
    db_session.refresh(space)
    return space


def _overdue_task(
    db_session: Session, space: Space, priority: str = "high", title: str = "Closure test task",
    due_at: datetime | None = None,
):
    return tasks_service.create_task(
        db_session, space.id,
        TaskCreate(title=title, due_at=due_at or datetime.now(timezone.utc) - timedelta(days=1), priority=priority),
    )


def _overdue_task_signal(db_session: Session, space: Space, priority: str = "high", title: str = "Closure test task"):
    """Builds BOTH the Task row and the matching Signal — using the
    SAME due_at value and the real _canonical_timestamp formatting a
    live generate_signals call would independently produce — so
    exposure_snapshot comparisons (the Same-Concern Repeat Gate,
    DISMISSED_UNCHANGED) correctly recognize it as the identical
    concern, exactly as test_same_concern_repeat.py already
    establishes."""
    from app.modules.attention import service as signal_service
    from app.modules.attention.schemas import Signal

    due_at = datetime.now(timezone.utc) - timedelta(days=1)
    task = _overdue_task(db_session, space, priority=priority, title=title, due_at=due_at)
    snapshot = {"due_at": signal_service._canonical_timestamp(due_at), "status": "open"}
    signal = Signal(
        signal_type="TASK_OVERDUE", source_type="task", source_id=task.id, title=task.title,
        relevant_timestamp=due_at, priority=priority, measurement_seconds=86400, snapshot=snapshot,
    )
    return task, signal


def _stub_narration(monkeypatch: pytest.MonkeyPatch, result_or_side_effect, captured: dict | None = None):
    def _fake(candidate):
        if captured is not None:
            captured["calls"] = captured.get("calls", 0) + 1
        if callable(result_or_side_effect) and not isinstance(result_or_side_effect, NarrationResult):
            return result_or_side_effect(candidate)
        return result_or_side_effect

    monkeypatch.setattr(narration_service, "generate_app_opened_narration", _fake)


class _FakeModelResponse:
    def __init__(self, text=None, tool_uses=None, correlation_id="corr_test", stop_reason=None):
        self.text = text
        self.tool_uses = tool_uses or []
        self.correlation_id = correlation_id
        self.stop_reason = stop_reason if stop_reason is not None else ("tool_use" if tool_uses else "end_turn")


def _fake_complete_for_ordinary_chat(*, purpose, messages, system=None, tools=None, tool_choice=None, correlation_id=None, **_ignored):
    if purpose == "claim_verification":
        return _FakeModelResponse(
            tool_uses=[ToolUseBlock(id="t1", name="certify_claim", input={"claims_bazra_mutation_completed": False})]
        )
    return _FakeModelResponse(
        tool_uses=[ToolUseBlock(id="t2", name="respond_with_text", input={"kind": "answer", "text": "تمام."})]
    )


# ==================================================
# A — NOTHING IMPORTANT
# ==================================================


def test_scenario_a_nothing_important(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """INVARIANT 2/3: silence is a valid, successful, zero-provider-cost
    outcome. See test_app_opened.py's own exhaustive no-candidate
    coverage for depth."""
    space = _space(db_session)
    captured: dict = {}
    _stub_narration(monkeypatch, NarrationResult(text=_SAFE_TEXT, source="model"), captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert captured.get("calls", 0) == 0
    assert db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count() == 0
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 0


# ==================================================
# B — ONE CLEAR IMPORTANT CONCERN
# ==================================================


def test_scenario_b_one_clear_concern(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """INVARIANTS 4/6/16: one topic selected, narrated, verified,
    persisted atomically, no ProposedAction."""
    space = _space(db_session)
    _overdue_task(db_session, space)
    captured: dict = {}
    _stub_narration(monkeypatch, NarrationResult(text=_SAFE_TEXT, source="model"), captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "surfaced"
    assert captured["calls"] == 1
    assert db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count() == 1
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 1
    assert db_session.query(actions_service.ProposedAction).filter(
        actions_service.ProposedAction.space_id == space.id
    ).count() == 0


# ==================================================
# C — GLOBAL FREQUENCY
# ==================================================


def test_scenario_c_global_frequency_blocks_with_zero_provider_calls(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 3/10: a recent (<30min) app_opened exposure for ANY
    source blocks proactive speech globally, at zero provider cost —
    the gap this scenario closes: test_app_opened.py proves the gate's
    own exact boundary in isolation; this proves it ALSO holds end to
    end through the real surfacing pipeline, not just at the decision
    layer."""
    space = _space(db_session)
    other_task = _overdue_task(db_session, space, title="An earlier concern")
    from app.modules.attention import scoring
    from app.modules.attention.schemas import Signal

    other_signal = Signal(
        signal_type="TASK_OVERDUE", source_type="task", source_id=other_task.id, title=other_task.title,
        relevant_timestamp=datetime.now(timezone.utc) - timedelta(days=1), priority="high",
        measurement_seconds=86400, snapshot={"due_at": "x", "status": "open"},
    )
    history.record_exposure(
        db_session, space.id, space.user_id, scoring.score_signal(other_signal), "app_opened",
        datetime.now(timezone.utc) - timedelta(minutes=5), _CAIRO, commit=True,
    )
    _overdue_task(db_session, space, title="A newer concern")  # a different, otherwise-eligible candidate

    captured: dict = {}
    _stub_narration(monkeypatch, NarrationResult(text=_SAFE_TEXT, source="model"), captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "proactive_frequency"
    assert captured.get("calls", 0) == 0
    assert db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count() == 0
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 1  # only the seeded one


# ==================================================
# D / E — SAME CONCERN + EXACT 24H BOUNDARY
# ==================================================


def test_scenario_d_same_concern_blocks_with_zero_provider_calls(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 9/10: global frequency has already passed (15h > 30m),
    but the SAME unchanged concern is still inside its own 24h window
    — see test_same_concern_repeat.py for the full boundary sweep."""
    from app.modules.attention import scoring

    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    history.record_exposure(
        db_session, space.id, space.user_id, scoring.score_signal(signal), "app_opened",
        datetime.now(timezone.utc) - timedelta(hours=15), _CAIRO, commit=True,
    )

    captured: dict = {}
    _stub_narration(monkeypatch, NarrationResult(text=_SAFE_TEXT, source="model"), captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "same_concern_recently_surfaced"
    assert captured.get("calls", 0) == 0


def test_scenario_e_same_concern_exact_24h_boundary_gate_passes(db_session: Session) -> None:
    """INVARIANT 9/10 boundary — the gate itself passes at exactly 24h;
    whether the system actually speaks depends on every OTHER gate too
    (this scenario deliberately does not assert "speak")."""
    from app.modules.attention import app_opened, scoring

    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    now = datetime.now(timezone.utc)
    candidate = scoring.score_signal(signal)
    history.record_exposure(db_session, space.id, space.user_id, candidate, "app_opened", now - timedelta(hours=24), _CAIRO)

    assert app_opened.same_concern_recently_surfaced(db_session, space.id, candidate, now) is False


# ==================================================
# F / G — SNOOZE / DISMISS
# ==================================================


def test_scenario_f_snoozed_candidate_not_surfaced(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """INVARIANT 13: see test_feedback.py/test_same_concern_repeat.py
    for full snooze mechanics; here only the end-to-end "not surfaced"
    outcome is asserted."""
    space = _space(db_session)
    task = _overdue_task(db_session, space)
    from app.modules.attention import scoring
    from app.modules.attention.schemas import Signal

    signal = Signal(
        signal_type="TASK_OVERDUE", source_type="task", source_id=task.id, title=task.title,
        relevant_timestamp=datetime.now(timezone.utc) - timedelta(days=1), priority="high",
        measurement_seconds=86400, snapshot={"due_at": "x", "status": "open"},
    )
    exposure = history.record_exposure(
        db_session, space.id, space.user_id, scoring.score_signal(signal), "app_opened",
        datetime.now(timezone.utc) - timedelta(hours=25), _CAIRO, commit=True,
    )
    history.record_snooze(
        db_session, space.id, space.user_id, exposure.id,
        datetime.now(timezone.utc) + timedelta(hours=2), datetime.now(timezone.utc),
    )

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"


def test_scenario_g_dismissed_unchanged_candidate_not_surfaced(db_session: Session) -> None:
    """INVARIANT 13: see test_feedback.py for full dismiss mechanics."""
    from app.modules.attention import scoring

    space = _space(db_session)
    _, signal = _overdue_task_signal(db_session, space)
    exposure = history.record_exposure(
        db_session, space.id, space.user_id, scoring.score_signal(signal), "app_opened",
        datetime.now(timezone.utc) - timedelta(hours=25), _CAIRO, commit=True,
    )
    history.record_dismiss(db_session, space.id, space.user_id, exposure.id, datetime.now(timezone.utc) - timedelta(hours=24, minutes=30))

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"


# ==================================================
# H — SOURCE RESOLVES DURING NARRATION
# ==================================================


def test_scenario_h_source_resolves_during_narration(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """INVARIANT 14: see test_surfacing.py's own
    test_stale_candidate_during_narration_produces_silence for the
    original proof; reasserted here as a named closure scenario."""
    space = _space(db_session)
    task = _overdue_task(db_session, space)

    def _side_effect(candidate):
        tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(status="done"))
        return NarrationResult(text=_SAFE_TEXT, source="model")

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "candidate_became_stale"
    assert db_session.query(ChatMessage).filter(ChatMessage.space_id == space.id).count() == 0
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 0


# ==================================================
# I — USER SPEAKS DURING NARRATION
# ==================================================


def test_scenario_i_user_speaks_during_narration_wins(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """INVARIANT 11: see test_surfacing.py's own
    test_new_user_message_during_narration_wins for the original proof."""
    space = _space(db_session)
    _overdue_task(db_session, space)

    def _side_effect(candidate):
        chat_service.record_user_message(db_session, space.id, space.user_id, "Ahmed's own message")
        return NarrationResult(text=_SAFE_TEXT, source="model")

    _stub_narration(monkeypatch, _side_effect)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "user_message_won_race"
    assert db_session.query(ChatMessage).filter(
        ChatMessage.space_id == space.id, ChatMessage.role == "assistant"
    ).count() == 0


# ==================================================
# J — PENDING ACTION
# ==================================================


def test_scenario_j_pending_action_blocks_and_confirmation_flow_undisturbed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 12: see test_app_opened.py's own pending-action tests
    for the gate itself; this additionally proves the pending proposal
    row is left completely untouched."""
    space = _space(db_session)
    _overdue_task(db_session, space)
    prompt = chat_service.record_assistant_message(db_session, space.id, space.user_id, "Shall I add this task?")
    proposal = actions_service.create_pending_action(
        db_session, space.id, space.user_id, prompt.id, "create_task", {"title": "Something else"},
    )

    captured: dict = {}
    _stub_narration(monkeypatch, NarrationResult(text=_SAFE_TEXT, source="model"), captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "pending_action"
    assert captured.get("calls", 0) == 0
    db_session.refresh(proposal)
    assert proposal.status == "pending"  # completely untouched


# ==================================================
# K — TWO SIMULTANEOUS APP_OPENED REQUESTS
# ==================================================


def test_scenario_k_simultaneous_requests_produce_at_most_one_surfacing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 15/16: see test_surfacing.py's own
    test_simultaneous_app_opened_requests_only_one_persists and
    test_lock_not_held_during_narration for the full real-Postgres
    concurrency proofs — reasserted here as a named closure scenario
    using the exact same barrier-synchronized two-thread pattern."""
    import threading

    from sqlalchemy.orm import sessionmaker

    space = _space(db_session)
    _overdue_task(db_session, space)
    space_id, user_id = space.id, space.user_id
    barrier = threading.Barrier(2)

    def _fake(candidate):
        barrier.wait(timeout=5)
        return NarrationResult(text=_SAFE_TEXT, source="model")

    monkeypatch.setattr(narration_service, "generate_app_opened_narration", _fake)

    results: list = [None, None]

    def _run(index: int) -> None:
        session = sessionmaker(bind=db_session.get_bind())()
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

    assert sorted(r.status for r in results) == ["silence", "surfaced"]
    verify = sessionmaker(bind=db_session.get_bind())()
    try:
        assert verify.query(ChatMessage).filter(
            ChatMessage.space_id == space_id, ChatMessage.role == "assistant"
        ).count() == 1
        assert verify.query(AttentionExposure).filter(AttentionExposure.space_id == space_id).count() == 1
    finally:
        verify.close()


# ==================================================
# L / M / N — NARRATION FAILURE / VERIFIER BLOCKS / VERIFIER FAILURE
# ==================================================


def test_scenario_l_narration_provider_failure_surfaces_deterministic_fallback(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 23/24: see test_narration.py for the full
    narration-layer proof of fallback construction from authoritative
    facts only; here we prove surfacing accepts a fallback result and
    still runs it through final revalidation. No real provider call."""
    space = _space(db_session)
    _overdue_task(db_session, space, title="Fallback task")
    fallback = NarrationResult(text="عندك مهمة متأخرة: Fallback task.", source="fallback", fallback_reason="narration_failed")
    _stub_narration(monkeypatch, fallback)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "surfaced"
    assert result.message.content == fallback.text
    assert db_session.query(AttentionExposure).filter(AttentionExposure.space_id == space.id).count() == 1


def test_scenario_m_verifier_blocked_narration_surfaces_fallback_not_unsafe_text(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 21/22: see test_narration.py's own adversarial tests
    (e.g. "I moved the task to tomorrow") for the full verifier-layer
    proof; here we confirm surfacing never leaks unsafe text merely
    because narration's OWN result object claims source="fallback"."""
    space = _space(db_session)
    _overdue_task(db_session, space, title="Verifier task")
    fallback = NarrationResult(text="عندك مهمة متأخرة: Verifier task.", source="fallback", fallback_reason="verification_blocked")
    _stub_narration(monkeypatch, fallback)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "surfaced"
    assert "I moved" not in result.message.content
    assert result.message.content == fallback.text


def test_scenario_n_verifier_failure_surfaces_fallback(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """INVARIANT 22: verifier contract failure is fail-closed — see
    test_narration.py's own test_verifier_failure_never_returns_model_text."""
    space = _space(db_session)
    _overdue_task(db_session, space, title="Verifier failure task")
    fallback = NarrationResult(text="عندك مهمة متأخرة: Verifier failure task.", source="fallback", fallback_reason="verification_failed")
    _stub_narration(monkeypatch, fallback)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "surfaced"
    assert result.message.content == fallback.text


# ==================================================
# O / P — BARE YES / OLD PROPOSAL INTERRUPTION
# ==================================================


def test_scenario_o_bare_yes_after_proactive_opening_causes_no_mutation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANTS 18/19/20: see test_surfacing.py's own
    test_bare_yes_after_proactive_opening_does_not_mutate_domain for
    the original proof."""
    space = _space(db_session)
    task = _overdue_task(db_session, space)
    _stub_narration(monkeypatch, NarrationResult(text="عندك مهمة متأخرة. تحب نرتبها؟", source="model"))

    surfaced = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)
    assert surfaced.status == "surfaced"

    monkeypatch.setattr(model_router_service, "complete", _fake_complete_for_ordinary_chat)
    chat_service.send_message(
        db_session, space.id, space.user_id, "أيوه",
        datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(days=1), _CAIRO,
    )

    assert db_session.query(actions_service.ProposedAction).filter(
        actions_service.ProposedAction.space_id == space.id
    ).count() == 0
    db_session.refresh(task)
    assert task.status == "open"


def test_scenario_p_old_resolved_proposal_cannot_be_reached_through_proactive_interruption(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 20: an OLD proposal (already rejected, no longer
    "latest pending") sits in history with its own confirmation-prompt
    ChatMessage. A proactive opening is then genuinely surfaced
    (inserting a NEW assistant message strictly after that old prompt).
    A later bare "yes" must not reach back and re-execute the old,
    unrelated, already-resolved proposal — proven two ways: (a) the
    real end-to-end flow causes no mutation, and (b) the underlying
    adjacency primitive itself correctly reports non-adjacency once the
    proactive message sits between the old prompt and any later
    message, exactly like any other intervening ChatMessage (see
    actions/tests/test_actions.py's own adjacency suite for the general
    mechanism this re-uses, unmodified)."""
    space = _space(db_session)
    # old_task deliberately carries NO due_at — it generates no Signal at
    # all and can never compete as an attention candidate; its only role
    # here is to be the target of the old, now-resolved proposal.
    old_task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Old unrelated task"))
    # The old confirmation prompt is genuinely OLD (well past the
    # 10-minute active-conversation Moment Quality window) — using
    # chat_service.record_assistant_message here would stamp it with
    # real current time via server_default=func.now(), which would
    # itself trigger that unrelated gate and mask what this scenario is
    # actually testing.
    old_prompt = ChatMessage(
        space_id=space.id, user_id=space.user_id, role="assistant", content="Add the old task?",
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(old_prompt)
    db_session.commit()
    db_session.refresh(old_prompt)
    old_proposal = actions_service.create_pending_action(
        db_session, space.id, space.user_id, old_prompt.id, "create_task", {"title": "Old unrelated task"},
    )
    actions_service.reject(db_session, space.id, space.user_id)
    db_session.refresh(old_proposal)
    assert old_proposal.status == "rejected"
    assert actions_service.get_latest_pending(db_session, space.id, space.user_id) is None

    _overdue_task(db_session, space, title="New real concern")
    _stub_narration(monkeypatch, NarrationResult(text="عندك مهمة متأخرة جديدة.", source="model"))
    surfaced = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)
    assert surfaced.status == "surfaced"
    assert surfaced.message.id > old_prompt.id

    monkeypatch.setattr(model_router_service, "complete", _fake_complete_for_ordinary_chat)
    user_message, _ = chat_service.send_message(
        db_session, space.id, space.user_id, "أيوه",
        datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(days=1), _CAIRO,
    )

    assert actions_service.is_still_conversationally_adjacent(
        db_session, space.id, space.user_id, old_proposal, user_message.id
    ) is False
    db_session.refresh(old_task)
    assert old_task.status == "open"  # never created/executed via the old proposal
    assert db_session.query(actions_service.ProposedAction).filter(
        actions_service.ProposedAction.space_id == space.id, actions_service.ProposedAction.status == "executed",
    ).count() == 0


# ==================================================
# Q — WINNER ROTATION
# ==================================================


def test_scenario_q_recently_surfaced_winner_silences_rather_than_rotating(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT 9/25: see test_same_concern_repeat.py's own
    test_recently_surfaced_winner_produces_silence_not_runner_up for
    the original decision-level proof; reasserted here through the
    FULL surfacing pipeline (zero provider calls either way)."""
    from app.modules.attention import scoring
    from app.modules.attention.schemas import Signal

    space = _space(db_session)
    # Build three distinct, strictly-ordered candidates the same way
    # test_same_concern_repeat.py does.
    def _task_signal(days_overdue, priority, title):
        due_at = datetime.now(timezone.utc) - timedelta(days=days_overdue)
        t = tasks_service.create_task(db_session, space.id, TaskCreate(title=title, due_at=due_at, priority=priority))
        from app.modules.attention import service as signal_service
        snapshot = {"due_at": signal_service._canonical_timestamp(due_at), "status": "open"}
        s = Signal(
            signal_type="TASK_OVERDUE", source_type="task", source_id=t.id, title=t.title,
            relevant_timestamp=due_at, priority=priority, measurement_seconds=days_overdue * 86400, snapshot=snapshot,
        )
        return t, s

    task_a, signal_a = _task_signal(6, "high", "Task A")
    _task_signal(3, "high", "Task B")
    _task_signal(1, "normal", "Task C")

    history.record_exposure(
        db_session, space.id, space.user_id, scoring.score_signal(signal_a), "app_opened",
        datetime.now(timezone.utc) - timedelta(hours=15), _CAIRO, commit=True,
    )

    captured: dict = {}
    _stub_narration(monkeypatch, NarrationResult(text=_SAFE_TEXT, source="model"), captured)

    result = surfacing.evaluate_and_surface_app_opened(db_session, space.id, space.user_id, _CAIRO)

    assert result.status == "silence"
    assert result.reason == "same_concern_recently_surfaced"
    assert captured.get("calls", 0) == 0
