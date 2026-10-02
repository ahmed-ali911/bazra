"""Checkpoint 4.5d — Proactive Narration + Verification + Deterministic
Fallback. Covers model-success, topic isolation, verifier fail-closed
semantics, deterministic fallback (content/budget/zero-model-content),
action safety, persistence (zero writes), provider-call budget, the
personality boundary, and adversarial model-output handling.
"""

from datetime import datetime, timezone

import pytest

from app.modules.attention.schemas import AttentionCandidate, GateResult, ScoreComponent, Signal
from app.modules.narration import service as narration_service
from app.modules.orchestrator import service as orchestrator_service

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _candidate(
    signal_type: str = "TASK_OVERDUE",
    source_type: str = "task",
    title: str = "Call Hussein",
    priority: str | None = "high",
    score: int = 75,
) -> AttentionCandidate:
    signal = Signal(
        signal_type=signal_type,
        source_type=source_type,
        source_id=1,
        title=title,
        relevant_timestamp=_NOW,
        priority=priority,
        measurement_seconds=86400,
        snapshot={"due_at": _NOW.isoformat(), "status": "open"},
    )
    return AttentionCandidate(
        signal=signal,
        score=score,
        reason_codes=(ScoreComponent("TASK_OVERDUE_BASE", None, 50),),
        suppression=GateResult(suppressed=False, reason_code=None),
    )


def _stub_narration(monkeypatch: pytest.MonkeyPatch, text, captured: dict | None = None):
    def _fake(*, signal_type, title, priority):
        if captured is not None:
            captured["narration_calls"] = captured.get("narration_calls", 0) + 1
            captured["narration_args"] = (signal_type, title, priority)
        if isinstance(text, Exception):
            raise text
        return text

    monkeypatch.setattr(orchestrator_service, "generate_app_opened_narration_text", _fake)


def _stub_verifier(monkeypatch: pytest.MonkeyPatch, result, captured: dict | None = None):
    def _fake(candidate_text):
        if captured is not None:
            captured["verifier_calls"] = captured.get("verifier_calls", 0) + 1
            captured["verifier_text"] = candidate_text
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(orchestrator_service, "verify_no_mutation_claim", _fake)


# ==================================================
# MODEL SUCCESS
# ==================================================


def test_safe_model_narration_is_returned_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, "عندك مهمة متأخرة تستاهل نبص عليها.", captured)
    _stub_verifier(monkeypatch, False, captured)  # claims_bazra_mutation_completed=False -> safe

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "model"
    assert result.text == "عندك مهمة متأخرة تستاهل نبص عليها."
    assert result.fallback_reason is None
    assert captured["narration_calls"] == 1
    assert captured["verifier_calls"] == 1
    assert captured["verifier_text"] == "عندك مهمة متأخرة تستاهل نبص عليها."


def test_successful_path_is_exactly_two_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, "safe narration", captured)
    _stub_verifier(monkeypatch, False, captured)

    narration_service.generate_app_opened_narration(_candidate())

    assert captured["narration_calls"] == 1
    assert captured["verifier_calls"] == 1


# ==================================================
# TOPIC ISOLATION / PRIVACY
# ==================================================


def test_narration_module_never_calls_signal_generation_or_ranking() -> None:
    """Checks for an actual CALL (name immediately followed by an open
    paren) — never a bare mention, since the module's own prose
    docstring legitimately discusses these excluded functions by name
    without parentheses."""
    import inspect

    source = inspect.getsource(narration_service)
    for forbidden in ("generate_signals(", "load_suppression_states(", "rank_for_surface(", "gather_context("):
        assert forbidden not in source


def test_narration_module_has_no_db_dependency() -> None:
    import inspect

    signature = inspect.signature(narration_service.generate_app_opened_narration)
    assert "db" not in signature.parameters
    assert list(signature.parameters) == ["candidate"]


def test_orchestrator_receives_only_the_three_scalar_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, "safe narration", captured)
    _stub_verifier(monkeypatch, False)

    narration_service.generate_app_opened_narration(_candidate(title="Call Hussein", priority="high"))

    assert captured["narration_args"] == ("TASK_OVERDUE", "Call Hussein", "high")


# ==================================================
# VERIFIER
# ==================================================


def test_unsafe_verifier_result_never_returns_model_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, "I moved the task to tomorrow.")
    _stub_verifier(monkeypatch, True)  # claims_bazra_mutation_completed=True -> unsafe

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "fallback"
    assert result.fallback_reason == "verification_blocked"
    assert "I moved the task to tomorrow." not in result.text


def test_verifier_failure_never_returns_model_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, "some candidate text")
    _stub_verifier(monkeypatch, orchestrator_service.ClaimVerificationFailed("provider_call_failed"))

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "fallback"
    assert result.fallback_reason == "verification_failed"
    assert "some candidate text" not in result.text


def test_verifier_receives_only_the_candidate_narration_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, "candidate narration text")
    _stub_verifier(monkeypatch, False, captured)

    narration_service.generate_app_opened_narration(_candidate())

    assert captured["verifier_text"] == "candidate narration text"


# ==================================================
# FALLBACK
# ==================================================


def test_narration_provider_failure_produces_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, orchestrator_service.OrchestratorError("provider_error"))

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "fallback"
    assert result.fallback_reason == "narration_failed"
    assert "Call Hussein" in result.text


def test_empty_narration_produces_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, "   ")

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "fallback"
    assert result.fallback_reason == "narration_empty"


def test_none_narration_produces_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, None)

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "fallback"
    assert result.fallback_reason == "narration_empty"


def test_narration_provider_failure_calls_no_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, orchestrator_service.OrchestratorError("provider_error"))
    _stub_verifier(monkeypatch, False, captured)

    narration_service.generate_app_opened_narration(_candidate())

    assert captured.get("verifier_calls", 0) == 0


def test_fallback_contains_zero_model_derived_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, "some hallucinated unsafe thing nobody authored")
    _stub_verifier(monkeypatch, True)

    result = narration_service.generate_app_opened_narration(_candidate())

    assert "hallucinated" not in result.text
    assert "unsafe" not in result.text


def test_fallback_path_calls_no_second_narration_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, "unsafe text", captured)
    _stub_verifier(monkeypatch, True)

    narration_service.generate_app_opened_narration(_candidate())

    assert captured["narration_calls"] == 1


@pytest.mark.parametrize(
    "signal_type,source_type",
    [
        ("TASK_OVERDUE", "task"),
        ("TASK_DUE_TODAY", "task"),
        ("TASK_DUE_SOON", "task"),
        ("EVENT_UPCOMING", "calendar_event"),
        ("INBOX_NEEDS_ATTENTION", "inbox_item"),
    ],
)
def test_every_supported_signal_type_has_a_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch, signal_type, source_type
) -> None:
    priority = "normal" if source_type == "task" else None
    _stub_narration(monkeypatch, orchestrator_service.OrchestratorError("provider_error"))

    result = narration_service.generate_app_opened_narration(
        _candidate(signal_type=signal_type, source_type=source_type, title="Something", priority=priority)
    )

    assert result.source == "fallback"
    assert "Something" in result.text


def test_fallback_contains_only_authoritative_candidate_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, orchestrator_service.OrchestratorError("provider_error"))

    result = narration_service.generate_app_opened_narration(_candidate(title="Exact Title", priority="high"))

    assert "Exact Title" in result.text
    # The high-priority suffix is authoritative (candidate.signal.priority
    # is literally "high") — never invented urgency.
    assert "أولوية عالية" in result.text


def test_fallback_omits_priority_suffix_for_normal_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, orchestrator_service.OrchestratorError("provider_error"))

    result = narration_service.generate_app_opened_narration(_candidate(priority="normal"))

    assert "أولوية عالية" not in result.text


# ==================================================
# ACTION SAFETY
# ==================================================


def test_narration_creates_zero_proposed_action_rows(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Counts a DELTA, not an absolute zero — the test database is
    shared across the whole pytest session (see conftest.py), so an
    earlier, unrelated test's own rows may already exist."""
    from app.modules.actions.service import ProposedAction

    before = db_session.query(ProposedAction).count()
    _stub_narration(monkeypatch, "safe narration")
    _stub_verifier(monkeypatch, False)

    narration_service.generate_app_opened_narration(_candidate())

    assert db_session.query(ProposedAction).count() == before


def test_bare_conversational_invitation_does_not_execute_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proactive opening's own invitation-style continuation
    ("تحب نرتبها؟") is just text — this module has no path to an
    action/execution pipeline at all (no actions_service import)."""
    import inspect

    assert "actions_service" not in inspect.getsource(narration_service)
    assert "confirm_and_execute" not in inspect.getsource(narration_service)


# ==================================================
# PERSISTENCE
# ==================================================


def test_zero_chat_message_writes(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Delta, not absolute zero — same shared-test-session-database
    reasoning as test_narration_creates_zero_proposed_action_rows."""
    from app.modules.chat.models import ChatMessage

    before = db_session.query(ChatMessage).count()
    _stub_narration(monkeypatch, "safe narration")
    _stub_verifier(monkeypatch, False)

    narration_service.generate_app_opened_narration(_candidate())

    assert db_session.query(ChatMessage).count() == before


def test_zero_attention_exposure_writes(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Delta, not absolute zero — same shared-test-session-database
    reasoning as test_narration_creates_zero_proposed_action_rows."""
    from app.modules.attention.models import AttentionExposure

    before = db_session.query(AttentionExposure).count()
    _stub_narration(monkeypatch, "safe narration")
    _stub_verifier(monkeypatch, False)

    narration_service.generate_app_opened_narration(_candidate())

    assert db_session.query(AttentionExposure).count() == before


def test_narration_service_has_no_commit_call() -> None:
    import inspect

    source = inspect.getsource(narration_service)
    assert ".commit()" not in source


# ==================================================
# CALL BUDGET (consolidated, see MODEL SUCCESS / FALLBACK sections above too)
# ==================================================


def test_verifier_block_triggers_no_repair_or_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, "unsafe candidate", captured)
    _stub_verifier(monkeypatch, True, captured)

    narration_service.generate_app_opened_narration(_candidate())

    assert captured["narration_calls"] == 1
    assert captured["verifier_calls"] == 1


def test_verifier_failure_triggers_no_repair_or_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _stub_narration(monkeypatch, "candidate text", captured)
    _stub_verifier(monkeypatch, orchestrator_service.ClaimVerificationFailed("x"), captured)

    narration_service.generate_app_opened_narration(_candidate())

    assert captured["narration_calls"] == 1
    assert captured["verifier_calls"] == 1


# ==================================================
# PERSONALITY BOUNDARY (prompt-level, see test_orchestrator.py for the
# full system-prompt assertions)
# ==================================================


def test_narration_service_requests_no_mood_or_emotion_inference() -> None:
    import inspect

    source = inspect.getsource(narration_service)
    for forbidden in ("mood", "emotion", "sentiment"):
        assert forbidden not in source.lower()


# ==================================================
# ADVERSARIAL MODEL OUTPUT
# ==================================================


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "I moved the task to tomorrow.",
        "I already completed that for you.",
        "I deleted the event.",
    ],
)
def test_adversarial_mutation_claims_are_blocked_and_replaced(monkeypatch: pytest.MonkeyPatch, unsafe_text) -> None:
    _stub_narration(monkeypatch, unsafe_text)
    _stub_verifier(monkeypatch, True)  # the (already-proven, Checkpoint 3.25) verifier certifies this as unsafe

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "fallback"
    assert unsafe_text not in result.text


def test_safe_natural_wording_passes_when_verifier_certifies_it(monkeypatch: pytest.MonkeyPatch) -> None:
    safe_text = "عندك مهمة متأخرة تستاهل نبص عليها."
    _stub_narration(monkeypatch, safe_text)
    _stub_verifier(monkeypatch, False)

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "model"
    assert result.text == safe_text


# ==================================================
# RESULT TYPE
# ==================================================


def test_narration_result_is_frozen_dataclass() -> None:
    import dataclasses

    from app.modules.narration.schemas import NarrationResult

    assert dataclasses.is_dataclass(NarrationResult)
    assert NarrationResult.__dataclass_params__.frozen is True


def test_model_result_never_carries_a_fallback_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, "safe narration")
    _stub_verifier(monkeypatch, False)

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "model"
    assert result.fallback_reason is None


def test_fallback_result_always_carries_a_fallback_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_narration(monkeypatch, orchestrator_service.OrchestratorError("x"))

    result = narration_service.generate_app_opened_narration(_candidate())

    assert result.source == "fallback"
    assert result.fallback_reason is not None
