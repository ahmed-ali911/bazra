from evals.benchmarks.retrospective_v3 import build_retrospective_v3_comparison
from evals.benchmarks.retrospective_v3_report import render_retrospective_v3_report

_SAMPLE_REPORT = """Benchmark: proactive_narration_compare (version v1)
Timestamp: 2026-01-01T00:00:00+00:00
Git SHA: unknown
Scenarios: 1  Repetitions: 1  Models: claude-sonnet-5, claude-haiku-4-5

======================================================================
PER-CASE RESULTS (grouped by scenario)
======================================================================

CASE: due_today_normal_en2
  [claude-sonnet-5] run 0
    candidate: 'Your flight tickets booking is still open \\u2014 want me to pull up the details?'
    Deterministic verdict: FAIL
    Failed checks: ['action_confirmation_shaped']
      - action_confirmation_shaped: matched pending-confirmation phrasing: 'want me to'
        Failure interpretation: POSSIBLE_EVALUATOR_SENSITIVITY
    Human review: <pending>
    tokens: in=15 out=9  latency=150ms
"""


def test_report_includes_label_identifying_it_as_the_same_model_outputs() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    rendered = render_retrospective_v3_report(comparisons)
    assert "SAME MODEL OUTPUTS, NEW EVALUATION INSTRUMENT" in rendered
    assert "NOT a new model benchmark" in rendered


def test_report_includes_per_model_aggregate_for_both_models() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    rendered = render_retrospective_v3_report(comparisons)
    assert "MODEL: claude-sonnet-5" in rendered
    assert "MODEL: claude-haiku-4-5" in rendered
    assert "V1 hard passes:" in rendered
    assert "V3 hard passes:" in rendered


def test_report_shows_action_confirmation_correction_section() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    rendered = render_retrospective_v3_report(comparisons)
    assert "ACTION-CONFIRMATION CORRECTIONS" in rendered
    assert "flight tickets booking is still open" in rendered
