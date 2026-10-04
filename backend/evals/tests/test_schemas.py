from evals.schemas import CheckResult, EvaluationCase, EvaluationResult, EvaluationSuite, SuiteResult


def test_evaluation_case_is_constructible_with_minimal_fields() -> None:
    case = EvaluationCase(case_id="c1", purpose="proactive_narration", facts={}, candidate="hi")
    assert case.case_id == "c1"
    assert case.tier is None
    assert case.tags == ()
    assert case.expected_pass is None
    assert case.expected_failed_checks == ()


def test_evaluation_case_candidate_field_accepts_non_string_values() -> None:
    """Checkpoint 5.4, section 7 — `candidate` is deliberately untyped
    (object), not str, so a future non-prose decision (e.g. a bare tool-
    selection enum) is representable without a contract change."""
    case = EvaluationCase(case_id="c1", purpose="tool_selection", facts={}, candidate="WEB_SEARCH")
    assert case.candidate == "WEB_SEARCH"
    case2 = EvaluationCase(case_id="c2", purpose="tool_selection", facts={}, candidate={"decision": "READ_LOCAL_TASKS"})
    assert case2.candidate == {"decision": "READ_LOCAL_TASKS"}


def test_evaluation_result_failed_checks_filters_to_failures_only() -> None:
    checks = (
        CheckResult("a", True, "ok"),
        CheckResult("b", False, "bad"),
        CheckResult("c", True, "ok"),
    )
    result = EvaluationResult(case_id="c1", passed=False, checks=checks)
    assert [c.name for c in result.failed_checks] == ["b"]


def test_suite_result_aggregation_is_simple_and_transparent() -> None:
    case_pass = EvaluationCase(case_id="p", purpose="x", facts={}, candidate="y", expected_pass=True)
    case_fail = EvaluationCase(case_id="f", purpose="x", facts={}, candidate="y", expected_pass=False)
    results = (
        EvaluationResult(case_id="p", passed=True, checks=(CheckResult("chk", True, "ok"),)),
        EvaluationResult(case_id="f", passed=False, checks=(CheckResult("chk", False, "bad"),)),
    )
    suite_result = SuiteResult(
        suite_name="s", suite_version="v1", results=results,
        cases_by_id={"p": case_pass, "f": case_fail},
    )
    assert suite_result.total == 2
    assert suite_result.passed_count == 1
    assert suite_result.failed_count == 1
    assert suite_result.failures_by_check_name() == {"chk": 1}


def test_suite_result_has_no_opaque_weighted_score_field() -> None:
    """Checkpoint 5.4, section 8/10 — the contract must never offer a
    single numeric score field a future caller could reach for instead
    of the structured per-check evidence."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(SuiteResult)}
    assert "score" not in field_names
    assert "weighted_score" not in field_names


def test_matches_expectations_true_when_every_set_expectation_holds() -> None:
    case_pass = EvaluationCase(case_id="p", purpose="x", facts={}, candidate="y", expected_pass=True)
    case_fail = EvaluationCase(case_id="f", purpose="x", facts={}, candidate="y", expected_pass=False)
    results = (
        EvaluationResult(case_id="p", passed=True, checks=()),
        EvaluationResult(case_id="f", passed=False, checks=(CheckResult("chk", False, "bad"),)),
    )
    suite_result = SuiteResult(
        suite_name="s", suite_version="v1", results=results,
        cases_by_id={"p": case_pass, "f": case_fail},
    )
    assert suite_result.matches_expectations is True


def test_matches_expectations_false_when_a_case_disagrees_with_its_own_expectation() -> None:
    """A FAIL fixture that unexpectedly PASSES (a real regression in the
    detector) must flip matches_expectations to False — this is the
    harness's own self-test property, distinct from "every case
    passed"."""
    case_fail = EvaluationCase(case_id="f", purpose="x", facts={}, candidate="y", expected_pass=False)
    results = (EvaluationResult(case_id="f", passed=True, checks=()),)  # should have failed but didn't
    suite_result = SuiteResult(
        suite_name="s", suite_version="v1", results=results, cases_by_id={"f": case_fail},
    )
    assert suite_result.matches_expectations is False


def test_evaluation_suite_is_constructible_with_a_name_and_version() -> None:
    suite = EvaluationSuite(name="demo", version="v1")
    assert suite.cases == ()
