"""Checkpoint 5.5, section 18/19 — the human-review comparison artifact.

Side-by-side per scenario across every model, every run, with the full
candidate text, every check's own pass/fail + reason + interpretation,
and separate (never collapsed into one score) per-model aggregate
metrics. Deliberately plain text (no dashboard, no frontend).
"""

from evals.benchmarks.schemas import BenchmarkReport


def render_benchmark_report(report: BenchmarkReport) -> str:
    lines = [
        f"Benchmark: {report.suite_name} (version {report.suite_version})",
        f"Timestamp: {report.timestamp}",
        f"Git SHA: {report.git_sha}",
        f"Scenarios: {report.scenario_count}  Repetitions: {report.repetitions}  Models: {', '.join(report.models)}",
        "",
        "=" * 70,
        "PER-CASE RESULTS (grouped by scenario)",
        "=" * 70,
        "",
    ]

    by_scenario: dict[str, list] = {}
    for result in report.results:
        by_scenario.setdefault(result.scenario_case_id, []).append(result)

    for case_id, rows in by_scenario.items():
        lines.append(f"CASE: {case_id}")
        for row in rows:
            lines.append(f"  [{row.model}] run {row.run_index}")
            if row.generation.failure_category is not None:
                lines.append(f"    PROVIDER FAILURE: {row.generation.failure_category} (latency {row.generation.latency_ms}ms)")
                lines.append("")
                continue
            status = "PASS" if row.hard_passed else "FAIL"
            lines.append(f"    candidate: {row.generation.text!r}")
            lines.append(f"    Deterministic verdict: {status}")
            if not row.hard_passed:
                failed = [c for c in row.checks if not c.passed]
                lines.append(f"    Failed checks: {[c.check_name for c in failed]}")
                for c in failed:
                    lines.append(f"      - {c.check_name}: {c.reason}")
                    lines.append(f"        Failure interpretation: {c.interpretation}")
                lines.append("    Human review: <pending — inspect candidate text above>")
            lines.append(
                f"    tokens: in={row.generation.prompt_tokens} out={row.generation.completion_tokens}  latency={row.generation.latency_ms}ms"
            )
            lines.append("")
        lines.append("")

    lines.append("=" * 70)
    lines.append("PER-MODEL SUMMARY (separate metrics — never one score)")
    lines.append("=" * 70)
    for model, summary in report.summaries.items():
        lines.append(f"\nMODEL: {model}")
        lines.append(f"  generations attempted: {summary.generations_attempted}")
        lines.append(f"  generations completed: {summary.generations_completed}")
        lines.append(f"  provider failures by category: {summary.provider_failures_by_category}")
        lines.append(f"  hard passes: {summary.hard_passes}")
        lines.append(f"  hard failures: {summary.hard_failures}")
        lines.append(f"  pass rate: {summary.pass_rate}")
        lines.append(f"  failures by check: {summary.failures_by_check}")
        lines.append(f"  failures by scenario: {summary.failures_by_scenario}")
        lines.append(f"  inconsistent scenarios (variance across runs): {summary.inconsistent_scenarios}")
        lines.append(f"  clear safety violations: {summary.clear_safety_violations}")
        lines.append(f"  possible evaluator-sensitivity failures: {summary.possible_evaluator_sensitivity}")
        lines.append(f"  unresolved — requires human review: {summary.unresolved_requires_human_review}")
        lines.append(f"  total input tokens: {summary.total_input_tokens}")
        lines.append(f"  total output tokens: {summary.total_output_tokens}")
        lines.append(f"  average latency (ms, observed benchmark latency, not guaranteed production latency): {summary.average_latency_ms}")

    lines.append("")
    lines.append(
        "NOTE: 'passed current deterministic hard checks' — not 'proved safe'. "
        "See evals/proactive_narration.py's own Blind spot docstrings and "
        "evals/benchmarks/interpretation.py for what each failure bucket does "
        "and does not establish."
    )
    return "\n".join(lines)
