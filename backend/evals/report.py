"""Checkpoint 5.4 — a minimal, human-readable offline report (section
22/46). A CLI-and-test-readable text block, deliberately not a web
dashboard or frontend: `python -m evals.report` runs the one real
suite (proactive narration) and prints it.
"""

from evals.proactive_narration import ALL_CHECKS, PROACTIVE_NARRATION_SUITE
from evals.runner import evaluate_suite
from evals.schemas import SuiteResult


def render_report(result: SuiteResult) -> str:
    lines = [f"Suite: {result.suite_name} (version {result.suite_version})", ""]
    for case_result in result.results:
        case = result.cases_by_id[case_result.case_id]
        status = "PASS" if case_result.passed else "FAIL"
        lines.append(f"[{status}] {case_result.case_id}")
        lines.append(f"  candidate: {case.candidate!r}")
        for check in case_result.checks:
            mark = "ok" if check.passed else "FAILED"
            lines.append(f"  - {check.name}: {mark} — {check.reason}")
        lines.append("")
    lines.append(
        f"Summary: {result.passed_count}/{result.total} passed, {result.failed_count} failed"
    )
    failures = result.failures_by_check_name()
    if failures:
        lines.append("Failures by check:")
        for name, count in sorted(failures.items()):
            lines.append(f"  - {name}: {count}")
    lines.append(f"Matches frozen expectations: {result.matches_expectations}")
    return "\n".join(lines)


def main() -> None:
    result = evaluate_suite(PROACTIVE_NARRATION_SUITE, ALL_CHECKS)
    print(render_report(result))


if __name__ == "__main__":
    main()
