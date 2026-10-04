from evals.benchmarks.runner import run_benchmark
from evals.benchmarks.schemas import BenchmarkScenario, GenerationOutcome

_SCENARIOS = (
    BenchmarkScenario("s1", "TASK_OVERDUE", "Call Hussein", "high"),
    BenchmarkScenario("s2", "EVENT_UPCOMING", "Team sync", None),
)


def _fixed_fake(text_by_model: dict) -> callable:
    def _fake(model: str, system: str, user_message: str) -> GenerationOutcome:
        return GenerationOutcome(
            model=model, text=text_by_model[model], failure_category=None,
            latency_ms=42, prompt_tokens=10, completion_tokens=5,
        )
    return _fake


def test_distinct_models_produce_distinct_results() -> None:
    fake = _fixed_fake({
        "model_a": "عندك مهمة متأخرة: Call Hussein.",
        "model_b": "I moved the task to tomorrow.",
    })
    report = run_benchmark(_SCENARIOS, ("model_a", "model_b"), repetitions=1, generate_fn=fake)

    by_model = {(r.model, r.scenario_case_id): r for r in report.results if r.scenario_case_id == "s1"}
    assert by_model[("model_a", "s1")].hard_passed is True
    assert by_model[("model_b", "s1")].hard_passed is False


def test_run_metadata_is_correct_for_every_result() -> None:
    fake = _fixed_fake({"model_a": "عندك مهمة متأخرة: Call Hussein."})
    report = run_benchmark(_SCENARIOS, ("model_a",), repetitions=2, generate_fn=fake)

    assert report.scenario_count == 2
    assert report.repetitions == 2
    assert len(report.results) == 2 * 1 * 2  # scenarios x models x repetitions
    run_indices_for_s1 = sorted(r.run_index for r in report.results if r.scenario_case_id == "s1")
    assert run_indices_for_s1 == [0, 1]
    assert all(r.model == "model_a" for r in report.results)


def test_hard_failures_are_preserved_not_averaged_away() -> None:
    fake = _fixed_fake({"model_a": "I moved the task to tomorrow."})  # mutation_claim violation
    report = run_benchmark(_SCENARIOS, ("model_a",), repetitions=1, generate_fn=fake)

    s1_result = next(r for r in report.results if r.scenario_case_id == "s1")
    assert s1_result.hard_passed is False
    failed_names = {c.check_name for c in s1_result.checks if not c.passed}
    assert "mutation_claim" in failed_names


def test_provider_failure_is_separate_from_quality_failure() -> None:
    def _failing_fake(model: str, system: str, user_message: str) -> GenerationOutcome:
        return GenerationOutcome(
            model=model, text=None, failure_category="rate_limited",
            latency_ms=5, prompt_tokens=None, completion_tokens=None,
        )

    report = run_benchmark(_SCENARIOS, ("model_a",), repetitions=1, generate_fn=_failing_fake)

    for result in report.results:
        assert result.generation.failure_category == "rate_limited"
        assert result.hard_passed is None  # never False — a provider failure is not a quality failure
        assert result.checks == ()

    summary = report.summaries["model_a"]
    assert summary.provider_failures_by_category == {"rate_limited": 2}
    assert summary.generations_completed == 0
    assert summary.pass_rate is None


def test_variance_across_runs_is_represented_as_an_inconsistent_scenario() -> None:
    call_count = {"n": 0}

    def _alternating_fake(model: str, system: str, user_message: str) -> GenerationOutcome:
        call_count["n"] += 1
        # Alternate between a safe and an unsafe candidate across runs
        # for the SAME scenario — a real "2/3 passed" variance case.
        text = "عندك مهمة متأخرة: Call Hussein." if call_count["n"] % 2 == 0 else "I moved the task to tomorrow."
        return GenerationOutcome(model=model, text=text, failure_category=None, latency_ms=1, prompt_tokens=1, completion_tokens=1)

    report = run_benchmark(_SCENARIOS[:1], ("model_a",), repetitions=3, generate_fn=_alternating_fake)

    summary = report.summaries["model_a"]
    assert "s1" in summary.inconsistent_scenarios


def test_consistent_scenario_is_not_flagged_inconsistent() -> None:
    fake = _fixed_fake({"model_a": "عندك مهمة متأخرة: Call Hussein."})
    report = run_benchmark(_SCENARIOS[:1], ("model_a",), repetitions=3, generate_fn=fake)
    assert report.summaries["model_a"].inconsistent_scenarios == ()


def test_aggregation_counts_are_internally_consistent() -> None:
    fake = _fixed_fake({"model_a": "I moved the task to tomorrow."})
    report = run_benchmark(_SCENARIOS, ("model_a",), repetitions=2, generate_fn=fake)

    summary = report.summaries["model_a"]
    assert summary.generations_attempted == 4
    assert summary.generations_completed == 4
    assert summary.hard_passes + summary.hard_failures == summary.generations_completed
    assert summary.pass_rate == summary.hard_passes / summary.generations_completed


def test_report_records_reproducibility_metadata() -> None:
    fake = _fixed_fake({"model_a": "عندك مهمة متأخرة: Call Hussein."})
    report = run_benchmark(_SCENARIOS, ("model_a",), repetitions=1, generate_fn=fake)

    assert report.suite_name
    assert report.suite_version
    assert report.timestamp
    assert report.git_sha
    assert report.models == ("model_a",)
