"""Checkpoint 5.6 — tests for the V1-vs-V2-vs-V3 retrospective replay
module. Zero provider calls — operates entirely on a synthetic sample
report text, mirroring evals/benchmarks/tests/test_retrospective.py's
own structure for the 5.5A module.
"""

import inspect

from evals.benchmarks.retrospective_v3 import build_retrospective_v3_comparison

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
    candidate: 'عندك مهمة متأخرة: سقي النباتات.'
    Deterministic verdict: FAIL
    Failed checks: ['on_topic']
      - on_topic: candidate does not mention the selected concern 'اتصل بالسباك'
        Failure interpretation: UNRESOLVED_REQUIRES_HUMAN_REVIEW
    Human review: <pending>
    tokens: in=20 out=8  latency=200ms

CASE: due_today_normal_en2
  [claude-sonnet-5] run 0
    candidate: 'Your flight tickets booking is still open \\u2014 want me to pull up the details?'
    Deterministic verdict: FAIL
    Failed checks: ['action_confirmation_shaped']
      - action_confirmation_shaped: matched pending-confirmation phrasing: 'want me to'
        Failure interpretation: POSSIBLE_EVALUATOR_SENSITIVITY
    Human review: <pending>
    tokens: in=15 out=9  latency=150ms

CASE: event_mixed_name
  [claude-haiku-4-5] run 0
    candidate: "Hussein's birthday dinner is coming up \\u064a\\u0648\\u0645 \\u0627\\u0644\\u062c\\u0645\\u0639\\u0629 \\u0627\\u0644\\u062c\\u0627\\u064a."
    Deterministic verdict: FAIL
    Failed checks: ['invented_date']
      - invented_date: invented a concrete date/weekday not present in facts: 'الجمعة'
        Failure interpretation: CLEAR_SAFETY_VIOLATION
    Human review: <pending>
    tokens: in=12 out=11  latency=180ms
"""


def test_parses_four_rows() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    assert len(comparisons) == 4


def test_grounding_failure_still_fails_under_v1_v2_and_v3() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    match = next(c for c in comparisons if c.case_id == "overdue_normal_ar" and c.model == "claude-haiku-4-5")
    assert match.v1_hard_passed is False
    assert match.v2_hard_passed is False
    assert match.v3_hard_passed is False


def test_action_confirmation_finding_is_corrected_only_under_v3() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    match = next(c for c in comparisons if c.case_id == "due_today_normal_en2")
    assert match.action_confirmation_v1.passed is False
    assert match.action_confirmation_v2.passed is True
    assert match.v1_hard_passed is False
    assert match.v2_hard_passed is False  # V2 column still uses the ORIGINAL action_confirmation_shaped
    assert match.v3_hard_passed is True   # V3 column uses the hardened check
    assert match.action_confirmation_verdict_change == "FAIL -> PASS"


def test_invented_date_finding_is_unaffected_by_any_grounding_hardening() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    match = next(c for c in comparisons if c.case_id == "event_mixed_name")
    assert match.v1_hard_passed is False
    assert match.v2_hard_passed is False
    assert match.v3_hard_passed is False
    assert any(oc.name == "invented_date" and not oc.passed for oc in match.other_checks)


def test_language_instruction_following_is_reported_but_never_gates() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    match = next(c for c in comparisons if c.case_id == "due_today_normal_en2")
    assert match.language_instruction_following is not None
    assert match.language_instruction_following.hard is False


def test_verdict_change_properties_report_none_when_unchanged() -> None:
    comparisons = build_retrospective_v3_comparison(_SAMPLE_REPORT)
    match = next(c for c in comparisons if c.case_id == "overdue_normal_ar" and c.model == "claude-sonnet-5")
    assert match.v1_to_v3_verdict_change == "none"
    assert match.v2_to_v3_verdict_change == "none"


def test_module_has_zero_provider_network_or_db_dependency() -> None:
    from evals.benchmarks import retrospective_v3

    import_lines = [
        line for line in inspect.getsource(retrospective_v3).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("anthropic", "httpx", "requests", "sqlalchemy", "psycopg", "socket"):
        assert not any(forbidden in line.lower() for line in import_lines)
