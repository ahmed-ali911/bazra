"""Checkpoint 5.5, section 7 — production prompt fidelity.

The SYSTEM prompt is reused directly, unmodified, from production:
`orchestrator_service._build_proactive_narration_system_prompt()` is a
pure, side-effect-free function (no model call, no DB, no network) —
importing and calling it here is zero-risk and guarantees byte-for-byte
fidelity with zero duplication.

The USER message, by contrast, is a tiny (4-line) inline expression
inside `generate_app_opened_narration_text` itself, not a separately
importable function. Rather than refactor production code to extract
it (a real, if small, production change this checkpoint's own
discipline prefers to avoid unless genuinely necessary), this module
duplicates that exact logic and keeps it honest with a dedicated,
permanent alignment test
(`evals/benchmarks/tests/test_prompt_fidelity.py`) that captures what
the REAL production function actually sends to the provider (via a
monkeypatched `model_router_service.complete`) and asserts byte-for-
byte equality against this module's own builder. If production's
message-building ever changes, that test fails loudly — this is a
continuously-verified duplication, not a silent one.
"""

from app.modules.orchestrator.service import _build_proactive_narration_system_prompt

build_proactive_narration_system_prompt = _build_proactive_narration_system_prompt


def build_proactive_narration_user_message(signal_type: str, title: str, priority: str | None) -> str:
    """Mirrors orchestrator_service.generate_app_opened_narration_text's
    own exact facts_lines/user_message construction, verbatim. See this
    module's own docstring for how this is kept honest."""
    facts_lines = [f"signal_type: {signal_type}", f"title: {title}"]
    if priority is not None:
        facts_lines.append(f"priority: {priority}")
    return "Selected attention topic:\n" + "\n".join(facts_lines)
