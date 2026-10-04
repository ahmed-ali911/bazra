from evals.proactive_narration import ALL_CHECKS, PROACTIVE_NARRATION_SUITE
from evals.report import render_report
from evals.runner import evaluate_suite


def test_report_shows_suite_name_and_version() -> None:
    result = evaluate_suite(PROACTIVE_NARRATION_SUITE, ALL_CHECKS)
    report = render_report(result)
    assert "proactive_narration" in report
    assert "v1" in report


def test_report_shows_each_case_candidate_and_pass_fail() -> None:
    result = evaluate_suite(PROACTIVE_NARRATION_SUITE, ALL_CHECKS)
    report = render_report(result)
    assert "[PASS] clean_factual_narration" in report
    assert "[FAIL] mutation_claim_arabic" in report
    assert "candidate:" in report


def test_report_shows_failed_check_names_and_reasons() -> None:
    result = evaluate_suite(PROACTIVE_NARRATION_SUITE, ALL_CHECKS)
    report = render_report(result)
    assert "mutation_claim: FAILED" in report
    assert "matched completed-mutation phrasing" in report


def test_report_shows_summary_totals() -> None:
    result = evaluate_suite(PROACTIVE_NARRATION_SUITE, ALL_CHECKS)
    report = render_report(result)
    assert f"{result.passed_count}/{result.total} passed" in report
    assert "Matches frozen expectations: True" in report


def test_report_module_is_runnable_as_a_cli_entry_point(capsys) -> None:
    from evals.report import main

    main()
    captured = capsys.readouterr()
    assert "Suite: proactive_narration" in captured.out
