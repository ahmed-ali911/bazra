from evals.benchmarks.report import render_benchmark_report
from evals.benchmarks.runner import run_benchmark
from evals.benchmarks.schemas import BenchmarkScenario, GenerationOutcome

_SCENARIOS = (BenchmarkScenario("s1", "TASK_OVERDUE", "Call Hussein", "high"),)


def _fake(model: str, system: str, user_message: str) -> GenerationOutcome:
    text = "عندك مهمة متأخرة: Call Hussein." if model == "model_a" else "I moved the task to tomorrow."
    return GenerationOutcome(model=model, text=text, failure_category=None, latency_ms=10, prompt_tokens=5, completion_tokens=3)


def test_report_contains_candidate_outputs_for_every_run() -> None:
    report = run_benchmark(_SCENARIOS, ("model_a", "model_b"), repetitions=1, generate_fn=_fake)
    rendered = render_benchmark_report(report)

    assert "عندك مهمة متأخرة: Call Hussein." in rendered
    assert "I moved the task to tomorrow." in rendered


def test_report_shows_deterministic_verdict_and_failed_checks_and_interpretation() -> None:
    report = run_benchmark(_SCENARIOS, ("model_b",), repetitions=1, generate_fn=_fake)
    rendered = render_benchmark_report(report)

    assert "Deterministic verdict: FAIL" in rendered
    assert "mutation_claim" in rendered
    assert "Failure interpretation: CLEAR_SAFETY_VIOLATION" in rendered


def test_report_includes_model_identifiers_and_summary_metrics_separately() -> None:
    report = run_benchmark(_SCENARIOS, ("model_a", "model_b"), repetitions=1, generate_fn=_fake)
    rendered = render_benchmark_report(report)

    assert "MODEL: model_a" in rendered
    assert "MODEL: model_b" in rendered
    assert "pass rate:" in rendered
    assert "hard passes:" in rendered
    assert "hard failures:" in rendered
    # never collapsed into one combined score field
    assert "weighted" not in rendered.lower()


def test_report_states_the_measurement_not_safety_proof_caveat() -> None:
    report = run_benchmark(_SCENARIOS, ("model_a",), repetitions=1, generate_fn=_fake)
    rendered = render_benchmark_report(report)
    assert "passed current deterministic hard checks" in rendered
