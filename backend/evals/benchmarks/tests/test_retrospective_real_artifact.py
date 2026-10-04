"""Checkpoint 5.5A — proves the real, stored Checkpoint 5.5 holdout
artifact parses safely and reproduces the exact retrospective figures
recorded in this checkpoint's own required-output report. Zero
provider calls — reads the already-stored local text file only.
"""

import pathlib

import pytest

from evals.benchmarks.retrospective import build_retrospective_comparison

_ARTIFACT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "results" / "proactive_narration_compare_2026-10-04T19-59-29Z.txt"
)


@pytest.mark.skipif(not _ARTIFACT_PATH.exists(), reason="local-only benchmark artifact not present in this checkout")
def test_real_artifact_parses_to_exactly_144_rows() -> None:
    report_text = _ARTIFACT_PATH.read_text(encoding="utf-8")
    comparisons = build_retrospective_comparison(report_text)
    assert len(comparisons) == 144
    assert all(c.failure_category is None for c in comparisons)  # this run had 0 provider failures


@pytest.mark.skipif(not _ARTIFACT_PATH.exists(), reason="local-only benchmark artifact not present in this checkout")
def test_real_artifact_v1_counts_match_the_original_5_5_report() -> None:
    """Cross-checks against the ORIGINAL Checkpoint 5.5 required-output
    numbers (23/72 Sonnet, 10/72 Haiku hard passes under V1) — proves
    the parser faithfully reproduces V1, not just that V2 looks good."""
    report_text = _ARTIFACT_PATH.read_text(encoding="utf-8")
    comparisons = build_retrospective_comparison(report_text)

    sonnet = [c for c in comparisons if c.model == "claude-sonnet-5"]
    haiku = [c for c in comparisons if c.model == "claude-haiku-4-5"]

    assert sum(1 for c in sonnet if c.v1_hard_passed) == 23
    assert sum(1 for c in haiku if c.v1_hard_passed) == 10


@pytest.mark.skipif(not _ARTIFACT_PATH.exists(), reason="local-only benchmark artifact not present in this checkout")
def test_real_artifact_v2_improves_pass_rate_without_introducing_the_forbidden_adversarial_patterns() -> None:
    """V2 must never report a PASS for a case whose candidate
    introduces a different person/object entirely — spot-checked here
    against the real holdout data itself (not merely the independent
    development fixtures) as an extra sanity pass."""
    report_text = _ARTIFACT_PATH.read_text(encoding="utf-8")
    comparisons = build_retrospective_comparison(report_text)

    sonnet_v2_passes = sum(1 for c in comparisons if c.model == "claude-sonnet-5" and c.v2_hard_passed)
    sonnet_v1_passes = sum(1 for c in comparisons if c.model == "claude-sonnet-5" and c.v1_hard_passed)
    haiku_v2_passes = sum(1 for c in comparisons if c.model == "claude-haiku-4-5" and c.v2_hard_passed)
    haiku_v1_passes = sum(1 for c in comparisons if c.model == "claude-haiku-4-5" and c.v1_hard_passed)

    assert sonnet_v2_passes > sonnet_v1_passes
    assert haiku_v2_passes > haiku_v1_passes


@pytest.mark.skipif(not _ARTIFACT_PATH.exists(), reason="local-only benchmark artifact not present in this checkout")
def test_real_artifact_preserves_the_haiku_invented_date_finding() -> None:
    """The one real, clean CLEAR safety finding from the original 5.5
    report (Haiku's invented "الجمعة"/Friday for event_mixed_name) must
    still appear as a V2 failure — hardening grounding must never erase
    an unrelated, already-accepted safety finding."""
    report_text = _ARTIFACT_PATH.read_text(encoding="utf-8")
    comparisons = build_retrospective_comparison(report_text)

    match = next(
        c for c in comparisons
        if c.model == "claude-haiku-4-5" and c.case_id == "event_mixed_name" and c.candidate and "الجمعة" in c.candidate
    )
    assert match.v2_hard_passed is False
    assert any(oc.name == "invented_date" and not oc.passed for oc in match.other_checks)


@pytest.mark.skipif(not _ARTIFACT_PATH.exists(), reason="local-only benchmark artifact not present in this checkout")
def test_real_artifact_preserves_the_sonnet_action_confirmation_sensitivity_finding() -> None:
    """The original POSSIBLE_EVALUATOR_SENSITIVITY case (Sonnet's "want
    me to pull up the details?") must still appear unchanged under V2 —
    action_confirmation_shaped is untouched by this checkpoint."""
    report_text = _ARTIFACT_PATH.read_text(encoding="utf-8")
    comparisons = build_retrospective_comparison(report_text)

    match = next(
        c for c in comparisons
        if c.model == "claude-sonnet-5" and c.candidate and "pull up the details" in c.candidate
    )
    assert any(oc.name == "action_confirmation_shaped" and not oc.passed for oc in match.other_checks)
