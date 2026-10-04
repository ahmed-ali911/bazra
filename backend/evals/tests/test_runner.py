from evals.runner import evaluate_case, evaluate_suite
from evals.schemas import CheckResult, EvaluationCase, EvaluationSuite


def _always_pass(case: EvaluationCase) -> CheckResult:
    return CheckResult("always_pass", True, "stub")


def _always_fail(case: EvaluationCase) -> CheckResult:
    return CheckResult("always_fail", False, "stub failure")


def _soft_fail(case: EvaluationCase) -> CheckResult:
    return CheckResult("soft_informational", False, "non-gating", hard=False)


def test_evaluate_case_passes_when_every_hard_check_passes() -> None:
    case = EvaluationCase(case_id="c1", purpose="x", facts={}, candidate="y")
    result = evaluate_case(case, (_always_pass, _always_pass))
    assert result.passed is True
    assert result.case_id == "c1"


def test_evaluate_case_fails_when_any_hard_check_fails() -> None:
    case = EvaluationCase(case_id="c1", purpose="x", facts={}, candidate="y")
    result = evaluate_case(case, (_always_pass, _always_fail))
    assert result.passed is False
    assert [c.name for c in result.failed_checks] == ["always_fail"]


def test_a_failing_soft_check_never_fails_the_case() -> None:
    """Checkpoint 5.4, section 9 — the `hard` distinction exists so a
    future, explicitly non-gating informational check doesn't fail the
    whole case the way every V1 check (all hard=True) does."""
    case = EvaluationCase(case_id="c1", purpose="x", facts={}, candidate="y")
    result = evaluate_case(case, (_always_pass, _soft_fail))
    assert result.passed is True
    assert len(result.failed_checks) == 1  # still recorded, just non-gating


def test_evaluate_case_is_deterministically_repeatable() -> None:
    """Checkpoint 5.4, section 29 — same case + same checks must
    produce byte-identical results every time; no time/randomness/
    network/DB dependency anywhere in the runner."""
    case = EvaluationCase(case_id="c1", purpose="x", facts={"a": 1}, candidate="hello")
    first = evaluate_case(case, (_always_pass, _always_fail))
    second = evaluate_case(case, (_always_pass, _always_fail))
    assert first == second


def test_evaluate_suite_aggregates_every_case_in_order() -> None:
    cases = (
        EvaluationCase(case_id="a", purpose="x", facts={}, candidate="y"),
        EvaluationCase(case_id="b", purpose="x", facts={}, candidate="y"),
    )
    suite = EvaluationSuite(name="demo", version="v1", cases=cases)
    result = evaluate_suite(suite, (_always_pass,))
    assert [r.case_id for r in result.results] == ["a", "b"]
    assert result.suite_name == "demo"
    assert result.suite_version == "v1"
    assert result.passed_count == 2


def test_runner_module_has_no_network_db_or_provider_dependency() -> None:
    import inspect

    from evals import runner

    source = inspect.getsource(runner)
    for forbidden in ("requests", "httpx", "socket", "psycopg", "sqlalchemy", "anthropic"):
        assert forbidden not in source.lower()
