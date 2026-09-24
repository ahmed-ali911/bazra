import pytest

from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import HistoryTurn


def test_generate_reply_builds_correct_message_list_and_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _fake_complete(*, purpose, messages, system=None):
        captured["purpose"] = purpose
        captured["messages"] = messages
        captured["system"] = system

        class _Response:
            text = "canned reply"

        return _Response()

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _fake_complete)

    history = [
        HistoryTurn(role="user", content="earlier question"),
        HistoryTurn(role="assistant", content="earlier answer"),
    ]
    reply = orchestrator_service.generate_reply(
        history=history, context="Focus Today: nothing.", user_message="what's due today?"
    )

    assert reply == "canned reply"
    assert captured["purpose"] == "chat_completion"
    assert captured["messages"] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "what's due today?"},
    ]
    assert "Focus Today: nothing." in captured["system"]
    assert "NO ability to create, edit, or delete" in captured["system"]


def test_generate_reply_wraps_model_router_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*, purpose, messages, system=None):
        raise orchestrator_service.model_router_service.ModelRouterError("provider_error")

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _raise)

    with pytest.raises(orchestrator_service.OrchestratorError):
        orchestrator_service.generate_reply(history=[], context="", user_message="hi")


def test_system_prompt_forbids_write_claims_and_data_beyond_context() -> None:
    """A sanity check that the INTENDED instruction is really being
    sent — not a behavioral guarantee that the model will follow it.
    """
    prompt = orchestrator_service._build_system_prompt("some context")
    assert "say plainly that this isn't available yet" in prompt
    assert "do not claim to have done it" in prompt
    assert "say you don't have that information rather than guessing" in prompt


def test_orchestrator_never_imports_a_write_capable_service_function() -> None:
    """A direct check on this module's own namespace: no create_*/
    update_*/delete_* function from any other module is bound here at
    all — the code-level guarantee that no write can happen through
    this module, independent of anything the model itself might say.
    """
    import app.modules.orchestrator.service as module

    forbidden_prefixes = ("create_", "update_", "delete_")
    for name in dir(module):
        assert not name.startswith(forbidden_prefixes), f"Unexpected write-capable name bound in orchestrator: {name}"
