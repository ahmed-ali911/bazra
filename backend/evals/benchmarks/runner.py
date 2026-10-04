"""Checkpoint 5.5 — orchestrates generation + deterministic evaluation
+ interpretation into a BenchmarkReport.

`run_benchmark`'s `generate_fn` parameter defaults to the real
`generate_with_explicit_model` (the only real-provider call site) but
is fully injectable — every test in `evals/benchmarks/tests/` supplies
a fake, so 0 real provider calls ever occur from a test or from
importing this module (see
evals/benchmarks/tests/test_no_accidental_real_call.py).
"""

import subprocess
from collections.abc import Callable
from datetime import datetime, timezone

from evals.benchmarks.generation import generate_with_explicit_model
from evals.benchmarks.interpretation import classify_failure_interpretation
from evals.benchmarks.prompts import build_proactive_narration_system_prompt, build_proactive_narration_user_message
from evals.benchmarks.schemas import (
    BenchmarkCaseResult,
    BenchmarkReport,
    BenchmarkScenario,
    CheckInterpretation,
    GenerationOutcome,
    ModelSummary,
)
from evals.proactive_narration import ALL_CHECKS
from evals.schemas import EvaluationCase

GenerateFunction = Callable[[str, str, str], GenerationOutcome]


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return "unknown"


def _evaluate_candidate(scenario: BenchmarkScenario, candidate_text: str) -> tuple[bool, tuple[CheckInterpretation, ...]]:
    facts = {"signal_type": scenario.signal_type, "title": scenario.title, "priority": scenario.priority}
    case = EvaluationCase(case_id=scenario.case_id, purpose="proactive_narration", facts=facts, candidate=candidate_text)
    checks = tuple(check(case) for check in ALL_CHECKS)
    hard_passed = all(c.passed for c in checks)
    interpreted = tuple(
        CheckInterpretation(
            check_name=c.name, passed=c.passed, reason=c.reason,
            interpretation=classify_failure_interpretation(c.name, c.passed),
        )
        for c in checks
    )
    return hard_passed, interpreted


def run_benchmark(
    scenarios: tuple[BenchmarkScenario, ...],
    models: tuple[str, ...],
    repetitions: int,
    generate_fn: GenerateFunction = generate_with_explicit_model,
    suite_name: str = "proactive_narration_compare",
    suite_version: str = "v1",
) -> BenchmarkReport:
    system = build_proactive_narration_system_prompt()
    results: list[BenchmarkCaseResult] = []

    for scenario in scenarios:
        user_message = build_proactive_narration_user_message(scenario.signal_type, scenario.title, scenario.priority)
        for model in models:
            for run_index in range(repetitions):
                generation = generate_fn(model, system, user_message)
                if generation.failure_category is not None:
                    # Checkpoint 5.5, section 24 — a provider failure is
                    # NOT a candidate safety failure: hard_passed stays
                    # None (there is no candidate text to judge at all),
                    # never False.
                    results.append(
                        BenchmarkCaseResult(
                            scenario_case_id=scenario.case_id, model=model, run_index=run_index,
                            generation=generation, hard_passed=None, checks=(),
                        )
                    )
                    continue
                hard_passed, checks = _evaluate_candidate(scenario, generation.text)
                results.append(
                    BenchmarkCaseResult(
                        scenario_case_id=scenario.case_id, model=model, run_index=run_index,
                        generation=generation, hard_passed=hard_passed, checks=checks,
                    )
                )

    summaries = {model: _summarize_model(model, results) for model in models}

    return BenchmarkReport(
        suite_name=suite_name,
        suite_version=suite_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=_git_sha(),
        scenario_count=len(scenarios),
        repetitions=repetitions,
        models=models,
        results=tuple(results),
        summaries=summaries,
    )


def _summarize_model(model: str, all_results: list[BenchmarkCaseResult]) -> ModelSummary:
    rows = [r for r in all_results if r.model == model]
    attempted = len(rows)

    provider_failures: dict[str, int] = {}
    for row in rows:
        if row.generation.failure_category is not None:
            provider_failures[row.generation.failure_category] = provider_failures.get(row.generation.failure_category, 0) + 1

    completed = [r for r in rows if r.hard_passed is not None]

    hard_passes = sum(1 for r in completed if r.hard_passed)
    hard_failures = sum(1 for r in completed if not r.hard_passed)
    pass_rate = (hard_passes / len(completed)) if completed else None

    failures_by_check: dict[str, int] = {}
    clear_count = 0
    possible_count = 0
    unresolved_count = 0
    for row in completed:
        for check in row.checks:
            if check.passed:
                continue
            failures_by_check[check.check_name] = failures_by_check.get(check.check_name, 0) + 1
            if check.interpretation == "CLEAR_SAFETY_VIOLATION":
                clear_count += 1
            elif check.interpretation == "POSSIBLE_EVALUATOR_SENSITIVITY":
                possible_count += 1
            elif check.interpretation == "UNRESOLVED_REQUIRES_HUMAN_REVIEW":
                unresolved_count += 1

    failures_by_scenario: dict[str, int] = {}
    outcomes_by_scenario: dict[str, set] = {}
    for row in completed:
        outcomes_by_scenario.setdefault(row.scenario_case_id, set()).add(row.hard_passed)
        if not row.hard_passed:
            failures_by_scenario[row.scenario_case_id] = failures_by_scenario.get(row.scenario_case_id, 0) + 1
    inconsistent_scenarios = tuple(
        case_id for case_id, outcomes in sorted(outcomes_by_scenario.items()) if len(outcomes) > 1
    )

    total_input_tokens = sum(r.generation.prompt_tokens or 0 for r in rows if r.generation.prompt_tokens is not None)
    total_output_tokens = sum(r.generation.completion_tokens or 0 for r in rows if r.generation.completion_tokens is not None)
    latencies = [r.generation.latency_ms for r in rows]
    average_latency_ms = (sum(latencies) / len(latencies)) if latencies else None

    return ModelSummary(
        model=model,
        generations_attempted=attempted,
        generations_completed=len(completed),
        provider_failures_by_category=provider_failures,
        hard_passes=hard_passes,
        hard_failures=hard_failures,
        pass_rate=pass_rate,
        failures_by_check=failures_by_check,
        failures_by_scenario=failures_by_scenario,
        inconsistent_scenarios=inconsistent_scenarios,
        clear_safety_violations=clear_count,
        possible_evaluator_sensitivity=possible_count,
        unresolved_requires_human_review=unresolved_count,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        average_latency_ms=average_latency_ms,
    )
