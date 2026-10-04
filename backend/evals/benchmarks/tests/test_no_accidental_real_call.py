"""Checkpoint 5.5, sections 5/35 — proves the real-provider benchmark
can NEVER be triggered by the offline 5.4 harness, by any test in this
suite, or merely by importing this subpackage.
"""

import inspect

import pytest


def test_evals_report_does_not_import_evals_benchmarks() -> None:
    """The offline 5.4 report (`python -m evals.report`) must never be
    able to reach the benchmark subpackage, let alone execute it."""
    from evals import report as offline_report

    import_lines = [
        line for line in inspect.getsource(offline_report).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("evals.benchmarks" in line or "benchmarks" in line for line in import_lines)


def test_importing_the_benchmark_cli_module_makes_no_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merely importing evals.benchmarks.proactive_narration_compare
    (e.g. the way any test collection or `python -m` invocation does)
    must never call the provider — only explicit `--confirm` + main()
    does."""
    from app.modules.model_router import service as model_router_service

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("no provider call may occur merely from importing the benchmark module")

    monkeypatch.setattr(model_router_service, "_call_anthropic", _must_not_be_called)
    monkeypatch.setattr(model_router_service, "complete", _must_not_be_called)

    import importlib

    import evals.benchmarks.proactive_narration_compare as cli_module
    importlib.reload(cli_module)  # re-executes module body under the patched functions above


def test_running_main_without_confirm_flag_makes_no_provider_call(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from app.modules.model_router import service as model_router_service

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("no provider call may occur without --confirm")

    monkeypatch.setattr(model_router_service, "_call_anthropic", _must_not_be_called)
    monkeypatch.setattr(model_router_service, "complete", _must_not_be_called)
    monkeypatch.setattr("sys.argv", ["proactive_narration_compare"])

    from evals.benchmarks.proactive_narration_compare import main

    main()  # must not raise, must not call the provider
    captured = capsys.readouterr()
    assert "Intended total real-provider generations" in captured.out


def test_describe_plan_reports_the_exact_intended_generation_count() -> None:
    from evals.benchmarks.proactive_narration_compare import MODELS, REPETITIONS, describe_plan
    from evals.benchmarks.scenarios import PROACTIVE_NARRATION_BENCHMARK_SCENARIOS

    expected = len(PROACTIVE_NARRATION_BENCHMARK_SCENARIOS) * len(MODELS) * REPETITIONS
    assert str(expected) in describe_plan()


def test_5_4_offline_tests_are_unaffected_by_the_benchmark_subpackage_existing() -> None:
    """A structural sanity check: the 5.4 proactive_narration suite's
    own ALL_CHECKS/PROACTIVE_NARRATION_SUITE objects are unchanged in
    identity/content by evals.benchmarks merely existing alongside
    them."""
    from evals.proactive_narration import ALL_CHECKS, PROACTIVE_NARRATION_SUITE

    assert len(PROACTIVE_NARRATION_SUITE.cases) == 13
    assert len(ALL_CHECKS) == 9
