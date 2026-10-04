"""Checkpoint 5.4 — the generic evaluator/runner API (section 31/32).

Deliberately reusable by ANY suite's own check functions — nothing
here is proactive-narration-specific (see evals/proactive_narration.py
for that suite's own checks). A "check function" is any callable
accepting an EvaluationCase and returning a CheckResult; evaluate_case
simply runs every check supplied and gates on the hard ones. No model
call, no network, no DB access anywhere in this module.
"""

from collections.abc import Callable, Sequence

from evals.schemas import EvaluationCase, EvaluationResult, EvaluationSuite, SuiteResult

CheckFunction = Callable[[EvaluationCase], "CheckResult"]  # noqa: F821 (forward ref, see schemas.CheckResult)


def evaluate_case(case: EvaluationCase, checks: Sequence[CheckFunction]) -> EvaluationResult:
    """Runs every supplied check against `case` and gates pass/fail on
    the hard ones only (section 9: a failing hard check always fails
    the case; nothing is averaged). A V1 suite's checks are all hard,
    so today `passed` is equivalent to "every check passed" — the
    `hard` distinction exists for a future, explicitly non-gating
    informational check, not exercised by this checkpoint's own suite.
    """
    results = tuple(check(case) for check in checks)
    passed = all(result.passed for result in results if result.hard)
    return EvaluationResult(case_id=case.case_id, passed=passed, checks=results)


def evaluate_suite(suite: EvaluationSuite, checks: Sequence[CheckFunction]) -> SuiteResult:
    results = tuple(evaluate_case(case, checks) for case in suite.cases)
    cases_by_id = {case.case_id: case for case in suite.cases}
    return SuiteResult(suite_name=suite.name, suite_version=suite.version, results=results, cases_by_id=cases_by_id)
