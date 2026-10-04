"""Checkpoint 5.5, section 7 — the continuously-verified alignment test
promised by evals/benchmarks/prompts.py's own module docstring. If
production's narration prompt construction ever changes, this test
fails loudly rather than letting the benchmark silently drift onto a
stale, hand-duplicated copy.
"""

import pytest

from app.modules.model_router.schemas import ModelResponse
from app.modules.orchestrator import service as orchestrator_service
from evals.benchmarks.prompts import build_proactive_narration_system_prompt, build_proactive_narration_user_message


@pytest.mark.parametrize(
    "signal_type,title,priority",
    [
        ("TASK_OVERDUE", "Call Hussein", "high"),
        ("EVENT_UPCOMING", "Team sync", None),
        ("INBOX_NEEDS_ATTENTION", "Invoice from supplier", None),
    ],
)
def test_benchmark_user_message_matches_real_production_construction(
    monkeypatch: pytest.MonkeyPatch, signal_type: str, title: str, priority: str | None,
) -> None:
    captured = {}

    def _fake_complete(*, purpose, messages, system=None, tools=None):
        captured["messages"] = messages
        captured["system"] = system
        return ModelResponse(text="ok", model="x", prompt_tokens=1, completion_tokens=1, tool_uses=[], correlation_id="c")

    monkeypatch.setattr(orchestrator_service.model_router_service, "complete", _fake_complete)

    orchestrator_service.generate_app_opened_narration_text(signal_type=signal_type, title=title, priority=priority)

    real_user_message = captured["messages"][0]["content"]
    assert build_proactive_narration_user_message(signal_type, title, priority) == real_user_message


def test_benchmark_system_prompt_is_the_real_production_function_not_a_copy() -> None:
    """This one has zero drift risk by construction — it's a direct
    reference to the real function, not a duplicate — but the test
    still proves the import path resolves to the same object/behavior,
    not a stale alias."""
    assert build_proactive_narration_system_prompt is orchestrator_service._build_proactive_narration_system_prompt
    assert build_proactive_narration_system_prompt() == orchestrator_service._build_proactive_narration_system_prompt()
