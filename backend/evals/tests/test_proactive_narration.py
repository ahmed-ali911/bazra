import pytest

from evals.proactive_narration import ALL_CHECKS, PROACTIVE_NARRATION_SUITE
from evals.runner import evaluate_case, evaluate_suite

# ---- the core proof: every frozen fixture's actual outcome matches its
# own declared expectation (section 42 — "the core proof that the
# harness can detect known regressions") ----------------------------

_CASES_BY_ID = {case.case_id: case for case in PROACTIVE_NARRATION_SUITE.cases}


@pytest.mark.parametrize("case_id", list(_CASES_BY_ID.keys()))
def test_frozen_fixture_matches_its_expected_outcome(case_id: str) -> None:
    case = _CASES_BY_ID[case_id]
    result = evaluate_case(case, ALL_CHECKS)

    assert result.passed == case.expected_pass, (
        f"{case_id}: expected passed={case.expected_pass}, got {result.passed} "
        f"(failed checks: {[c.name for c in result.failed_checks]})"
    )
    failed_names = {c.name for c in result.failed_checks}
    assert set(case.expected_failed_checks) <= failed_names, (
        f"{case_id}: expected failed checks {case.expected_failed_checks} "
        f"not all present in actual failures {failed_names}"
    )


def test_suite_result_matches_expectations_for_the_whole_frozen_suite() -> None:
    result = evaluate_suite(PROACTIVE_NARRATION_SUITE, ALL_CHECKS)
    assert result.matches_expectations is True


def test_frozen_suite_has_both_pass_and_fail_fixtures() -> None:
    passes = [c for c in PROACTIVE_NARRATION_SUITE.cases if c.expected_pass]
    fails = [c for c in PROACTIVE_NARRATION_SUITE.cases if not c.expected_pass]
    assert len(passes) >= 2
    assert len(fails) >= 8


def test_evaluation_is_fully_deterministic_and_repeatable_for_every_fixture() -> None:
    for case in PROACTIVE_NARRATION_SUITE.cases:
        first = evaluate_case(case, ALL_CHECKS)
        second = evaluate_case(case, ALL_CHECKS)
        assert first == second


# ---- per-check unit proofs (not merely incidental fixture coverage) --


def test_mutation_claim_check_does_not_misfire_on_the_word_overdue() -> None:
    """Regression-style guard for the exact Arabic root-sharing false
    positive this session's own 5.2 checkpoint discovered and fixed in
    a different module (متأخرة/أخر) — proving this suite's OWN
    mutation_claim check doesn't repeat that mistake."""
    from evals.proactive_narration import check_mutation_claim
    from evals.schemas import EvaluationCase

    case = EvaluationCase(
        case_id="overdue_is_not_a_mutation_claim", purpose="proactive_narration",
        facts={"signal_type": "TASK_OVERDUE", "title": "Call Hussein", "priority": "high"},
        candidate="عندك مهمة متأخرة: Call Hussein.",
    )
    result = check_mutation_claim(case)
    assert result.passed is True


def test_invented_priority_check_permits_high_priority_claims() -> None:
    from evals.proactive_narration import check_invented_priority
    from evals.schemas import EvaluationCase

    case = EvaluationCase(
        case_id="high_priority_grounded", purpose="proactive_narration",
        facts={"priority": "high"}, candidate="دي مهمة أولوية عالية.",
    )
    assert check_invented_priority(case).passed is True


def test_on_topic_check_requires_the_exact_title_substring() -> None:
    from evals.proactive_narration import check_on_topic
    from evals.schemas import EvaluationCase

    case = EvaluationCase(
        case_id="missing_title", purpose="proactive_narration",
        facts={"title": "Call Hussein"}, candidate="عندك مهمة متأخرة.",
    )
    result = check_on_topic(case)
    assert result.passed is False
    assert "Call Hussein" in result.reason
