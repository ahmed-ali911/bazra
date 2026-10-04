from evals.benchmarks.retrospective import build_retrospective_comparison
from evals.benchmarks.retrospective_report import render_retrospective_report

_SAMPLE_REPORT = """Benchmark: proactive_narration_compare (version v1)
Timestamp: 2026-01-01T00:00:00+00:00
Git SHA: unknown
Scenarios: 1  Repetitions: 1  Models: claude-sonnet-5, claude-haiku-4-5

======================================================================
PER-CASE RESULTS (grouped by scenario)
======================================================================

CASE: overdue_normal_ar
  [claude-sonnet-5] run 0
    candidate: 'اتصل بالسباك لسه مفتوحة ومتأخرة.'
    Deterministic verdict: PASS
    tokens: in=10 out=5  latency=100ms

CASE: overdue_normal_ar
  [claude-haiku-4-5] run 0
    candidate: 'السباك لسه محتاج تتصل بيه، الموضوع متأخر.'
    Deterministic verdict: FAIL
    Failed checks: ['on_topic']
      - on_topic: candidate does not mention the selected concern 'اتصل بالسباك'
        Failure interpretation: UNRESOLVED_REQUIRES_HUMAN_REVIEW
    Human review: <pending>
    tokens: in=20 out=8  latency=200ms
"""


def test_report_includes_per_model_aggregate_for_both_models() -> None:
    comparisons = build_retrospective_comparison(_SAMPLE_REPORT)
    rendered = render_retrospective_report(comparisons)
    assert "MODEL: claude-sonnet-5" in rendered
    assert "MODEL: claude-haiku-4-5" in rendered
    assert "V1 hard passes:" in rendered
    assert "V2 hard passes:" in rendered


def test_report_shows_fail_to_pass_section_with_candidate_text() -> None:
    comparisons = build_retrospective_comparison(_SAMPLE_REPORT)
    rendered = render_retrospective_report(comparisons)
    assert "CHANGED CASES: FAIL -> PASS" in rendered
    assert "السباك لسه محتاج تتصل بيه" in rendered
    assert "VERDICT CHANGE: FAIL -> PASS" in rendered


def test_report_does_not_require_inspecting_every_stored_row() -> None:
    """The PASSing-under-both-versions sonnet row should not appear in
    either changed-case section (only in the aggregate counts) — the
    report stays focused per section 24's own explicit requirement."""
    comparisons = build_retrospective_comparison(_SAMPLE_REPORT)
    rendered = render_retrospective_report(comparisons)
    fail_to_pass_section = rendered.split("CHANGED CASES: FAIL -> PASS")[1].split("CHANGED CASES: PASS -> FAIL")[0]
    assert "اتصل بالسباك لسه مفتوحة" not in fail_to_pass_section
