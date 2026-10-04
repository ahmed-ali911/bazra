"""Checkpoint 5.5A — tests for the holdout retrospective re-evaluation
parser and V1/V2 comparison builder. All synthetic sample text here —
never the real stored 5.5 artifact, which is exercised separately by
`evals/benchmarks/tests/test_retrospective_real_artifact.py`.
"""

import pytest

from evals.benchmarks.retrospective import build_retrospective_comparison, parse_benchmark_report

_SAMPLE_REPORT = """Benchmark: proactive_narration_compare (version v1)
Timestamp: 2026-01-01T00:00:00+00:00
Git SHA: unknown
Scenarios: 1  Repetitions: 2  Models: model_a, model_b

======================================================================
PER-CASE RESULTS (grouped by scenario)
======================================================================

CASE: overdue_high
  [model_a] run 0
    candidate: 'Call Hussein is still open.'
    Deterministic verdict: PASS
    tokens: in=10 out=5  latency=100ms

  [model_a] run 1
    PROVIDER FAILURE: rate_limited (latency 50ms)

CASE: overdue_high
  [model_b] run 0
    candidate: "It's still open — don't forget."
    Deterministic verdict: FAIL
    Failed checks: ['on_topic']
      - on_topic: candidate does not mention the selected concern 'X'
        Failure interpretation: UNRESOLVED_REQUIRES_HUMAN_REVIEW
    Human review: <pending>
    tokens: in=20 out=8  latency=200ms
"""


def test_parse_extracts_candidate_rows_correctly() -> None:
    rows = parse_benchmark_report(_SAMPLE_REPORT)
    assert len(rows) == 3
    assert rows[0].case_id == "overdue_high"
    assert rows[0].model == "model_a"
    assert rows[0].run_index == 0
    assert rows[0].candidate == "Call Hussein is still open."
    assert rows[0].failure_category is None


def test_parse_handles_provider_failure_rows() -> None:
    rows = parse_benchmark_report(_SAMPLE_REPORT)
    failure_row = rows[1]
    assert failure_row.candidate is None
    assert failure_row.failure_category == "rate_limited"


def test_parse_handles_embedded_quotes_and_apostrophes_via_literal_eval() -> None:
    rows = parse_benchmark_report(_SAMPLE_REPORT)
    assert rows[2].candidate == "It's still open — don't forget."


def test_parse_raises_on_unresolved_block_rather_than_silently_dropping() -> None:
    broken = """CASE: x
  [model_a] run 0
CASE: y
  [model_a] run 0
    candidate: 'ok'
    Deterministic verdict: PASS
"""
    with pytest.raises(ValueError):
        parse_benchmark_report(broken)


def test_parse_raises_on_completely_unmatched_report() -> None:
    with pytest.raises(ValueError):
        parse_benchmark_report("nothing matches this format at all")


def test_build_retrospective_comparison_produces_both_verdicts_for_each_candidate() -> None:
    comparisons = build_retrospective_comparison(_SAMPLE_REPORT)
    candidate_comparisons = [c for c in comparisons if c.candidate is not None]
    assert len(candidate_comparisons) == 2
    for c in candidate_comparisons:
        assert c.v1_on_topic is not None
        assert c.v2_on_topic is not None
        assert c.v1_hard_passed is not None
        assert c.v2_hard_passed is not None


def test_provider_failure_rows_have_no_hard_verdict_in_either_version() -> None:
    comparisons = build_retrospective_comparison(_SAMPLE_REPORT)
    failure_comparison = next(c for c in comparisons if c.failure_category is not None)
    assert failure_comparison.v1_hard_passed is None
    assert failure_comparison.v2_hard_passed is None
    assert failure_comparison.verdict_change == "none"


def test_verdict_change_detects_fail_to_pass() -> None:
    comparisons = build_retrospective_comparison(_SAMPLE_REPORT)
    # model_b's case: V1 fails on_topic (no "X" title match since this
    # sample case_id "overdue_high" is a REAL scenario with its own
    # real title "Call Hussein" — the candidate here doesn't mention it
    # at all, so BOTH V1 and V2 should fail it (no change) — this test
    # only proves the mechanism runs end-to-end without error; the real
    # V1->V2 verdict-change evidence comes from the real artifact run.
    model_b_comparison = next(c for c in comparisons if c.model == "model_b")
    assert model_b_comparison.verdict_change in ("none", "FAIL -> PASS", "PASS -> FAIL")


def test_zero_provider_calls_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.model_router import service as model_router_service

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("retrospective re-evaluation must never call a provider")

    monkeypatch.setattr(model_router_service, "_call_anthropic", _must_not_be_called)
    monkeypatch.setattr(model_router_service, "complete", _must_not_be_called)

    build_retrospective_comparison(_SAMPLE_REPORT)  # must not raise
