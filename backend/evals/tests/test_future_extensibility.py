"""Checkpoint 5.4, section 45 — proves the generic EvaluationCase/
EvaluationResult contract is NOT hard-coded to proactive narration or
even to prose generation, WITHOUT implementing any real future
evaluator. Purely a type/unit-level proof using throwaway inline check
functions defined in this test file — no fake Search subsystem, no
fake provider router, no fake free-source adapter (explicitly
forbidden by this section)."""

from evals.runner import evaluate_case
from evals.schemas import CheckResult, EvaluationCase


def test_contract_can_represent_a_tool_selection_decision_case() -> None:
    """A future suite comparing "which tool should have been called" —
    candidate is a bare decision string, not prose; facts carries the
    expected decision. No tool-selection evaluator is implemented by
    this checkpoint; this is a throwaway inline check proving only that
    the CONTRACT doesn't block it."""

    def _expected_tool_was_selected(case: EvaluationCase) -> CheckResult:
        expected = case.facts["expected_decision"]
        if case.candidate != expected:
            return CheckResult("tool_selection", False, f"expected {expected!r}, got {case.candidate!r}")
        return CheckResult("tool_selection", True, "matched expected decision")

    correct_case = EvaluationCase(
        case_id="should_read_local_tasks", purpose="tool_selection",
        facts={"expected_decision": "READ_LOCAL_TASKS"}, candidate="READ_LOCAL_TASKS",
    )
    wrong_case = EvaluationCase(
        case_id="should_not_have_searched_the_web", purpose="tool_selection",
        facts={"expected_decision": "READ_LOCAL_TASKS"}, candidate="WEB_SEARCH",
    )

    assert evaluate_case(correct_case, (_expected_tool_was_selected,)).passed is True
    assert evaluate_case(wrong_case, (_expected_tool_was_selected,)).passed is False


def test_contract_can_represent_a_source_routing_comparison_case() -> None:
    """A future suite comparing local-deterministic vs. free/owned vs.
    LLM-assisted output for the same information need — candidate is a
    structured dict here, not a string, proving `candidate: object` is
    not secretly assumed to be text anywhere in the runner itself."""

    def _candidate_is_a_dict_with_a_source_field(case: EvaluationCase) -> CheckResult:
        if not isinstance(case.candidate, dict) or "source" not in case.candidate:
            return CheckResult("structured_candidate", False, "candidate is not a source-tagged dict")
        return CheckResult("structured_candidate", True, "candidate carries a source field")

    case = EvaluationCase(
        case_id="local_vs_free_vs_llm", purpose="source_routing",
        facts={"information_need": "weather today"},
        candidate={"source": "local_deterministic", "value": "sunny, 25C"},
    )
    assert evaluate_case(case, (_candidate_is_a_dict_with_a_source_field,)).passed is True


def test_contract_does_not_require_a_tier_or_a_prose_purpose() -> None:
    """A case representing a decision outside the model-call/tier
    vocabulary entirely — `purpose`/`tier` are plain strings for exactly
    this reason (see schemas.py's own module docstring)."""
    case = EvaluationCase(case_id="x", purpose="source_routing", tier=None, facts={}, candidate={"anything": True})
    assert case.tier is None
    assert case.purpose == "source_routing"
