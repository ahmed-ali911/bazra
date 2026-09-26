import pytest

from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import HistoryTurn, OrchestratorResult

_ANCHOR = "2030-06-15T14:30:00 (Africa/Cairo)"


class _FakeToolUse:
    def __init__(self, name, input, id="toolu_test"):
        self.id = id
        self.name = name
        self.input = input


class _FakeModelResponse:
    def __init__(self, text=None, tool_uses=None, correlation_id="corr_test"):
        self.text = text
        self.tool_uses = tool_uses or []
        self.correlation_id = correlation_id


def test_generate_reply_builds_correct_message_list_and_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _fake_complete(*, purpose, messages, system=None, tools=None):
        captured["purpose"] = purpose
        captured["messages"] = messages
        captured["system"] = system
        captured["tools"] = tools
        return _FakeModelResponse(text="canned reply")

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _fake_complete)

    history = [
        HistoryTurn(role="user", content="earlier question"),
        HistoryTurn(role="assistant", content="earlier answer"),
    ]
    result = orchestrator_service.generate_reply(
        history=history,
        context="Focus Today: nothing.",
        user_message="what's due today?",
        current_datetime_local=_ANCHOR,
    )

    assert isinstance(result, OrchestratorResult)
    assert result.text == "canned reply"
    assert result.tool_call is None
    assert captured["purpose"] == "chat_completion"
    assert captured["messages"] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "what's due today?"},
    ]
    assert "Focus Today: nothing." in captured["system"]
    assert _ANCHOR in captured["system"]
    assert "NO ability to edit, delete, mark complete" in captured["system"]


def test_generate_reply_with_tools_offered_returns_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _fake_complete(*, purpose, messages, system=None, tools=None):
        captured["tools"] = tools
        return _FakeModelResponse(
            text="I'll add that task.",
            tool_uses=[_FakeToolUse("propose_create_task", {"title": "Call Hussein"})],
        )

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _fake_complete)

    tools = [{"name": "propose_create_task"}]
    result = orchestrator_service.generate_reply(
        history=[], context="", user_message="add a task to call Hussein",
        current_datetime_local=_ANCHOR, tools=tools,
    )

    assert captured["tools"] == tools
    assert result.text == "I'll add that task."
    assert result.tool_call is not None
    assert result.tool_call.tool_name == "propose_create_task"
    assert result.tool_call.arguments == {"title": "Call Hussein"}


def test_generate_reply_tool_only_response_has_none_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        orchestrator_service.model_router_service,
        "complete",
        lambda **kwargs: _FakeModelResponse(text=None, tool_uses=[_FakeToolUse("propose_create_task", {"title": "X"})]),
    )

    result = orchestrator_service.generate_reply(
        history=[], context="", user_message="add a task", current_datetime_local=_ANCHOR
    )
    assert result.text is None
    assert result.tool_call.tool_name == "propose_create_task"


def test_generate_reply_wraps_model_router_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*, purpose, messages, system=None, tools=None):
        raise orchestrator_service.model_router_service.ModelRouterError("provider_error")

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _raise)

    with pytest.raises(orchestrator_service.OrchestratorError):
        orchestrator_service.generate_reply(
            history=[], context="", user_message="hi", current_datetime_local=_ANCHOR
        )


def test_system_prompt_forbids_write_claims_and_data_beyond_context() -> None:
    """A sanity check that the INTENDED instruction is really being
    sent — not a behavioral guarantee that the model will follow it.
    """
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "say plainly that this isn't available yet" in prompt
    assert "do not claim to have done it" in prompt
    assert "say you don't have that information rather than guessing" in prompt


def test_system_prompt_describes_propose_create_task_as_a_proposal_not_an_execution() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "propose_create_task" in prompt
    assert "does NOT create the task" in prompt
    assert "must explicitly confirm before anything is created" in prompt


def test_system_prompt_describes_propose_update_task_referencing_task_id() -> None:
    """Checkpoint 3.10: the model must be told to use the real task_id
    shown in Current Data, never guess one, and that this tool is a
    proposal (does NOT change anything) just like propose_create_task."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "propose_update_task" in prompt
    assert "task_id" in prompt


def test_system_prompt_requires_the_tool_call_for_supported_task_writes() -> None:
    """Checkpoint 3.12b: the model must be told plainly that it MUST call
    the tool for a supported create/update request — a reliability fix
    for the observed failure mode where the model sometimes replies with
    proposal-like text without ever calling propose_create_task/
    propose_update_task."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "you MUST call the propose_create_task tool" in prompt
    assert "you MUST call the propose_update_task tool" in prompt


def test_system_prompt_states_plain_text_is_not_a_substitute_for_the_tool_call() -> None:
    """Checkpoint 3.12b: plain-text promises/offers/descriptions must be
    explicitly ruled out as satisfying the requirement above."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "is NOT a substitute for actually calling it" in prompt
    assert "is NOT a substitute for calling it" in prompt


def test_system_prompt_excludes_read_only_and_hypothetical_requests_from_the_tool_requirement() -> None:
    """Checkpoint 3.12b: the MUST-call requirement only applies when the
    user is actually asking for that creation/change right now — not for
    a read-only question, hypothetical discussion, or explanation."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "actually asking for that specific creation or change right now" in prompt
    assert "never" in prompt
    assert "read-only question, hypothetical discussion, an explanation" in prompt


def test_system_prompt_still_requires_explicit_confirmation_before_execution() -> None:
    """Checkpoint 3.12b changes only how strongly the tool call itself is
    required — it must not weaken the separate, pre-existing guarantee
    that calling propose_create_task/propose_update_task never executes
    anything by itself."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "This does NOT create the task — it only proposes it" in prompt
    assert "This does NOT change it — it only proposes it" in prompt
    assert "must explicitly confirm before anything is created" in prompt


def test_system_prompt_no_longer_claims_tasks_cannot_be_edited_or_marked_complete() -> None:
    """Checkpoint 3.10 narrows the old blanket 'no ability to edit,
    delete, mark complete... in this conversation' claim — that remains
    true for Calendar/Inbox/Life Areas, but is no longer true for an
    existing Task's own fields."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "you still cannot delete a task" in prompt
    assert "propose_update_task" in prompt


def test_system_prompt_includes_current_datetime_anchor() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert _ANCHOR in prompt
    assert "Current date/time" in prompt


def test_system_prompt_describes_memory_proposal_tools_as_proposals_not_executions() -> None:
    """Checkpoint 3.4: propose_save_memory/propose_forget_memory must be
    described with the same "does NOT ... only proposes" language as
    propose_create_task already gets, and confirmation must be explicit."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "propose_save_memory" in prompt
    assert "propose_forget_memory" in prompt
    assert "does NOT save it" in prompt
    assert "does NOT forget it" in prompt


def test_system_prompt_instructs_never_restating_inference_as_settled_fact() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "INFERENCE" in prompt
    assert "never restate an" in prompt.lower() or "never silently upgrade" in prompt.lower()


def test_system_prompt_instructs_surfacing_contradictory_memories_rather_than_silently_choosing() -> None:
    """Point 1 of the 3.4 revision round: the model must be explicitly
    told to surface a contradiction between two retrieved memories to
    the user, not silently pick one as authoritative. Proves the
    instruction reaches the actual prompt sent to the model, not just
    that it's described somewhere in a docstring."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "contradict" in prompt.lower()
    assert "which is current" in prompt.lower()


def test_system_prompt_identifies_as_bazra_not_an_assistant_for_bazra() -> None:
    """Checkpoint 3.5: the model must be told it IS BAZRA, not merely
    "BAZRA's assistant" — the prior framing invited exactly the
    "so who are you really" follow-through this checkpoint closes.
    """
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "You are BAZRA" in prompt
    assert "BAZRA's assistant" not in prompt


def test_system_prompt_instructs_never_naming_the_underlying_provider() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert (
        "You never identify yourself as Claude, ChatGPT, Gemini, Anthropic, "
        "OpenAI, or any other underlying provider or model, by name" in prompt
    )


def test_system_prompt_instructs_no_false_consciousness_or_feelings_claims() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "Never claim consciousness or subjective feelings" in prompt


def test_system_prompt_instructs_grounding_memory_claims_in_actual_retrieved_data() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "Never claim to remember something unless it is actually present" in prompt


def test_system_prompt_instructs_egyptian_arabic_and_english_technical_terms_baseline() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "contemporary, natural Egyptian Arabic" in prompt
    assert "Technical terms may stay in English" in prompt


def test_system_prompt_instructs_humor_is_optional_never_forced() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "never force a joke" in prompt


def test_system_prompt_instructs_personality_never_overrides_factual_accuracy() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert (
        "Personality, tone, brevity, and humor must never come at the cost of "
        "factual accuracy" in prompt
    )


def test_system_prompt_states_truthfulness_and_identity_rules_take_precedence_over_stored_preferences() -> None:
    """A stored Memory PREFERENCE must never be able to subvert identity,
    truthfulness, factual accuracy, or existing safety/permission rules
    (e.g. skipping confirmation-before-write) — asserted against the
    exact authored precedence sentence (identity.py's own text), not a
    loose co-occurrence of independent words, and confirming this
    content actually reaches the composed prompt _build_system_prompt
    returns for a real turn.
    """
    from app.modules.orchestrator.identity import BAZRA_IDENTITY_INSTRUCTIONS

    precedence_sentence = (
        "User preferences may refine BAZRA's communication style and behavior, "
        "but they never override BAZRA's identity, truthfulness, factual "
        "accuracy, safety/permission rules, or grounded application state."
    )
    assert precedence_sentence in BAZRA_IDENTITY_INSTRUCTIONS

    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert precedence_sentence in prompt


def test_system_prompt_describes_get_weather_as_a_read_not_a_proposal() -> None:
    """Checkpoint 3.7: unlike propose_create_task/propose_save_memory/
    propose_forget_memory, get_weather must be described as executing
    immediately — no confirmation step — so the model doesn't treat it
    like the write tools it sits alongside in the same offered list."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "get_weather" in prompt
    assert "executes immediately" in prompt
    assert "no confirmation needed" in prompt


def test_system_prompt_instructs_never_inferring_a_weather_location() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "Never guess, default, or infer a location" in prompt
    assert "ask the user which place they mean" in prompt


def test_system_prompt_lists_exactly_the_supported_weather_horizons() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "'now', 'today', 'tonight', and 'tomorrow' are supported" in prompt


def test_system_prompt_instructs_response_mode_factual_vs_reason() -> None:
    """Checkpoint 3.8: the model expresses factual-vs-interpretive intent
    as a response_mode argument on the SAME get_weather call, not via a
    second tool or a separate classifier call."""
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert "Set response_mode to 'factual'" in prompt
    assert "Set it to 'reason' when the user is asking for judgment or a recommendation" in prompt


# ---- Checkpoint 3.8: terminal tool-result continuation ----------------------


def test_tool_result_system_prompt_excludes_current_date_time() -> None:
    """Correction 2: the continuation's system prompt must NOT include
    current date/time unless a concrete consumer proves it necessary —
    WeatherResult's own period_start/period_end/timezone are sufficient
    temporal grounding for every currently-approved example."""
    prompt = orchestrator_service._build_tool_result_system_prompt()
    assert "Current date/time" not in prompt
    assert _ANCHOR not in prompt


def test_tool_result_system_prompt_includes_identity_and_grounding_rules() -> None:
    prompt = orchestrator_service._build_tool_result_system_prompt()
    assert "You are BAZRA" in prompt
    assert "the ONLY authoritative source of live information" in prompt
    assert "never invent a missing field" in prompt


def test_generate_tool_result_reply_sends_no_tools_and_reuses_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def _fake_complete(*, purpose, messages, system=None, tools=None, correlation_id=None):
        captured["purpose"] = purpose
        captured["messages"] = messages
        captured["tools"] = tools
        captured["correlation_id"] = correlation_id
        return _FakeModelResponse(text="It's warm tonight, no jacket needed.", correlation_id=correlation_id)

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _fake_complete)

    result = orchestrator_service.generate_tool_result_reply(
        user_message="do I need a jacket tonight in Cairo?",
        tool_use_id="toolu_123",
        tool_name="get_weather",
        tool_arguments={"location": "Cairo", "horizon": "tonight", "response_mode": "reason"},
        assistant_text="Let me check the weather.",
        tool_result_content='{"condition": "clear", "temperature_low_c": 22}',
        correlation_id="corr_shared_value",
    )

    assert result == "It's warm tonight, no jacket needed."
    assert captured["purpose"] == "tool_result_reasoning"
    assert captured["tools"] is None  # terminal — structurally forecloses another tool call
    assert captured["correlation_id"] == "corr_shared_value"

    # Exact 3-message shape, tool_result immediately following tool_use,
    # nothing in between — the provider's own required ordering.
    assert captured["messages"][0] == {"role": "user", "content": "do I need a jacket tonight in Cairo?"}
    assistant_content = captured["messages"][1]["content"]
    assert assistant_content[-1].id == "toolu_123"
    assert assistant_content[-1].name == "get_weather"
    tool_result_content = captured["messages"][2]["content"]
    assert tool_result_content[0].tool_use_id == "toolu_123"
    assert tool_result_content[0].content == '{"condition": "clear", "temperature_low_c": 22}'


def test_generate_tool_result_reply_wraps_model_router_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**kwargs):
        raise orchestrator_service.model_router_service.ModelRouterError("provider_error")

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _raise)

    with pytest.raises(orchestrator_service.OrchestratorError):
        orchestrator_service.generate_tool_result_reply(
            user_message="do I need a jacket?",
            tool_use_id="toolu_1",
            tool_name="get_weather",
            tool_arguments={"location": "Cairo", "horizon": "now", "response_mode": "reason"},
            assistant_text=None,
            tool_result_content="{}",
            correlation_id="corr_x",
        )


def test_orchestrator_never_imports_a_write_capable_service_function() -> None:
    """A direct check on this module's own namespace: no create_*/
    update_*/delete_* function from any other module is bound here at
    all — the code-level guarantee that no DOMAIN write can happen
    through this module, independent of anything the model itself might
    say or which tool it calls. Re-run unchanged from Checkpoint 3.2 —
    must still pass, proving this checkpoint didn't quietly weaken it.
    """
    import app.modules.orchestrator.service as module

    forbidden_prefixes = ("create_", "update_", "delete_")
    for name in dir(module):
        assert not name.startswith(forbidden_prefixes), f"Unexpected write-capable name bound in orchestrator: {name}"
