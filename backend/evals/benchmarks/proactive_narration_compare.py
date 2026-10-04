"""Checkpoint 5.5, section 34/35/39 — the explicit, separate real-
provider benchmark command. NEVER imported by `evals.report` or any
5.4/5.5 test; running `python -m evals.report` can never reach this
module, let alone execute it.

Running this module with NO arguments is intentionally SAFE: it only
prints the consent-gate plan (scenario count, models, repetitions,
intended total provider generations) and exits — it makes 0 provider
calls. The real benchmark only executes when invoked with the explicit
`--confirm` flag, which this checkpoint's own brief requires to follow
Ahmed's own explicit, out-of-band approval — this flag's presence is
necessary but not sufficient permission; it must only be passed after
that approval is actually given, by the human operator, not by this
code.

    python -m evals.benchmarks.proactive_narration_compare
        -> prints the plan only, 0 provider calls, always safe to run

    python -m evals.benchmarks.proactive_narration_compare --confirm
        -> executes the real benchmark (only after explicit approval)
"""

import argparse

from evals.benchmarks.report import render_benchmark_report
from evals.benchmarks.runner import run_benchmark
from evals.benchmarks.scenarios import PROACTIVE_NARRATION_BENCHMARK_SCENARIOS, SUITE_NAME, SUITE_VERSION

MODELS = ("claude-sonnet-5", "claude-haiku-4-5")
REPETITIONS = 3


def describe_plan() -> str:
    scenario_count = len(PROACTIVE_NARRATION_BENCHMARK_SCENARIOS)
    total_generations = scenario_count * len(MODELS) * REPETITIONS
    return "\n".join([
        f"Suite: {SUITE_NAME} (version {SUITE_VERSION})",
        f"Scenario count: {scenario_count}",
        f"Models: {', '.join(MODELS)}",
        f"Repetitions per model per scenario: {REPETITIONS}",
        f"Intended total real-provider generations: {total_generations}",
        "Token usage: recorded per generation when the provider response exposes it (input/output tokens).",
        "Cost: no monetary estimate computed here — see model_router's own documented, hand-maintained rate "
        "table if a cost estimate is wanted separately; this benchmark reports raw token counts only.",
        "",
        "This command makes 0 provider calls unless invoked with --confirm, "
        "which must only be passed after Ahmed's own explicit, out-of-band approval.",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm", action="store_true",
        help="Actually execute the real-provider benchmark. Only pass this after explicit approval.",
    )
    args = parser.parse_args()

    if not args.confirm:
        print(describe_plan())
        return

    report = run_benchmark(
        scenarios=PROACTIVE_NARRATION_BENCHMARK_SCENARIOS, models=MODELS, repetitions=REPETITIONS,
        suite_name=SUITE_NAME, suite_version=SUITE_VERSION,
    )
    print(render_benchmark_report(report))


if __name__ == "__main__":
    main()
