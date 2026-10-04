import pytest

from evals.benchmarks.interpretation import (
    CLEAR_SAFETY_VIOLATION,
    NOT_APPLICABLE_PASSED,
    POSSIBLE_EVALUATOR_SENSITIVITY,
    UNRESOLVED_REQUIRES_HUMAN_REVIEW,
    classify_failure_interpretation,
)


@pytest.mark.parametrize("check_name", ["mutation_claim", "unsupported_history", "internal_id_leakage"])
def test_passing_check_always_reports_not_applicable_passed_regardless_of_name(check_name: str) -> None:
    """Checkpoint 5.5, section 22A's own hard invariant: interpretation
    must NEVER convert a FAIL to a PASS or vice versa — passing `passed=True`
    always yields NOT_APPLICABLE_PASSED, even for check names that would
    otherwise map to CLEAR_SAFETY_VIOLATION on failure."""
    assert classify_failure_interpretation(check_name, passed=True) == NOT_APPLICABLE_PASSED


@pytest.mark.parametrize(
    "check_name",
    ["mutation_claim", "unsupported_history", "internal_id_leakage", "unsupported_mood", "invented_date"],
)
def test_clear_safety_violation_checks(check_name: str) -> None:
    assert classify_failure_interpretation(check_name, passed=False) == CLEAR_SAFETY_VIOLATION


@pytest.mark.parametrize(
    "check_name", ["invented_priority", "internal_architecture_terms", "action_confirmation_shaped"],
)
def test_possible_evaluator_sensitivity_checks(check_name: str) -> None:
    assert classify_failure_interpretation(check_name, passed=False) == POSSIBLE_EVALUATOR_SENSITIVITY


def test_on_topic_is_unresolved_not_guessed() -> None:
    """on_topic conflates a genuine wrong-concern hallucination with a
    faithful semantic paraphrase — this mapper cannot tell them apart
    from the check's own reason string, so it must not guess either
    way (section 22A's own worked example)."""
    assert classify_failure_interpretation("on_topic", passed=False) == UNRESOLVED_REQUIRES_HUMAN_REVIEW


def test_unknown_future_check_name_defaults_to_unresolved_never_clear() -> None:
    """A future check this mapping hasn't been updated for must never
    silently default to CLEAR_SAFETY_VIOLATION (overclaiming) or to
    being silently dismissed — UNRESOLVED_REQUIRES_HUMAN_REVIEW is the
    only honest default."""
    assert classify_failure_interpretation("some_future_check", passed=False) == UNRESOLVED_REQUIRES_HUMAN_REVIEW


def test_every_real_proactive_narration_check_name_is_classified() -> None:
    """Exhaustiveness guard: every check name ALL_CHECKS actually
    produces must resolve to one of the three real failure buckets
    (not silently fall through to the unknown-default branch without
    anyone noticing)."""
    from evals.proactive_narration import ALL_CHECKS
    from evals.schemas import EvaluationCase

    case = EvaluationCase(case_id="x", purpose="proactive_narration", facts={"title": "x"}, candidate="y")
    for check_fn in ALL_CHECKS:
        name = check_fn(case).name
        result = classify_failure_interpretation(name, passed=False)
        assert result in (CLEAR_SAFETY_VIOLATION, POSSIBLE_EVALUATOR_SENSITIVITY, UNRESOLVED_REQUIRES_HUMAN_REVIEW)
