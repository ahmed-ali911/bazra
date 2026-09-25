import pytest

from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import HistoryTurn, OrchestratorResult

_ANCHOR = "2030-06-15T14:30:00 (Africa/Cairo)"


class _FakeToolUse:
    def __init__(self, name, input):
        self.name = name
        self.input = input


class _FakeModelResponse:
    def __init__(self, text=None, tool_uses=None):
        self.text = text
        self.tool_uses = tool_uses or []


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


def test_system_prompt_includes_current_datetime_anchor() -> None:
    prompt = orchestrator_service._build_system_prompt("some context", _ANCHOR)
    assert _ANCHOR in prompt
    assert "Current date/time" in prompt


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
