import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.attention import scoring
from app.modules.attention.schemas import AttentionCandidate, ScoringError, Signal, SuppressionState

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _task(signal_type, priority, measurement_seconds, source_id=1, snapshot=None):
    return Signal(
        signal_type=signal_type,
        source_type="task",
        source_id=source_id,
        title=f"task-{source_id}",
        relevant_timestamp=_NOW,
        priority=priority,
        measurement_seconds=measurement_seconds,
        snapshot=snapshot if snapshot is not None else {"due_at": "x", "status": "open"},
    )


def _event(measurement_seconds, source_id=1):
    return Signal(
        signal_type="EVENT_UPCOMING",
        source_type="calendar_event",
        source_id=source_id,
        title=f"event-{source_id}",
        relevant_timestamp=_NOW,
        priority=None,
        measurement_seconds=measurement_seconds,
        snapshot={"starts_at": "x"},
    )


def _inbox(measurement_seconds, source_id=1):
    return Signal(
        signal_type="INBOX_NEEDS_ATTENTION",
        source_type="inbox_item",
        source_id=source_id,
        title=f"inbox-{source_id}",
        relevant_timestamp=_NOW,
        priority=None,
        measurement_seconds=measurement_seconds,
        snapshot={},
    )


# ==================================================
# §21 — Locked architecture scenario proof
#
# Every scenario below was independently hand-verified against the
# locked formulas using exact rational (fractions.Fraction) arithmetic
# before this test was written. Two scenarios (A/J and F) land exactly
# on a .5 proximity tie under true rational arithmetic and require
# OPPOSITE tie-break directions under any single rounding rule — this
# was flagged back to the architecture owner as a genuine contradiction
# in the checkpoint's own worked example, not silently resolved.
# Decision: round-half-up is the one shared tie-break rule; the
# checkpoint brief's own "A/J = 72" is corrected to 73 accordingly (see
# README.md's Checkpoint 4.3 section for the full writeup). F's "43"
# needed no correction under round-half-up.
# ==================================================


def test_scenario_a_high_task_due_in_2h_dedup_winner_is_due_today_73():
    due_today = _task("TASK_DUE_TODAY", "high", 2 * 3600)
    due_soon = _task("TASK_DUE_SOON", "high", 2 * 3600)
    ranked = scoring.rank_for_surface([due_today, due_soon], _NOW, "daily_brief")
    assert len(ranked) == 1  # same-source dedup: only one candidate for this task
    assert ranked[0].signal.signal_type == "TASK_DUE_TODAY"
    assert ranked[0].score == 73


def test_scenario_b_low_task_overdue_1_day_score_45():
    signal = _task("TASK_OVERDUE", "low", 24 * 3600)
    assert scoring.score_signal(signal).score == 45


def test_scenario_c_normal_task_overdue_5_days_score_75():
    signal = _task("TASK_OVERDUE", "normal", 5 * 24 * 3600)
    assert scoring.score_signal(signal).score == 75


def test_scenario_d_event_in_30_minutes_score_64():
    signal = _event(30 * 60)
    assert scoring.score_signal(signal).score == 64


def test_scenario_e_inbox_unread_2_days_score_17():
    signal = _inbox(2 * 24 * 3600)
    assert scoring.score_signal(signal).score == 17


def test_scenario_f_normal_task_due_in_10h_due_today_score_43():
    signal = _task("TASK_DUE_TODAY", "normal", 10 * 3600)
    assert scoring.score_signal(signal).score == 43


def test_scenario_g1_normal_task_due_in_11_5h_score_41_below_app_opened_threshold():
    signal = _task("TASK_DUE_TODAY", "normal", 11.5 * 3600)
    candidate = scoring.score_signal(signal)
    assert candidate.score == 41
    ranked = scoring.rank_for_surface([signal], _NOW, "app_opened")
    assert ranked[0].suppression.suppressed is True
    assert ranked[0].suppression.reason_code == "BELOW_THRESHOLD"


def test_scenario_g2_high_task_due_in_11_5h_score_61_included_in_app_opened():
    signal = _task("TASK_DUE_TODAY", "high", 11.5 * 3600)
    candidate = scoring.score_signal(signal)
    assert candidate.score == 61
    ranked = scoring.rank_for_surface([signal], _NOW, "app_opened")
    assert ranked[0].suppression.suppressed is False


def test_scenario_h_score_75_item_surfaced_3h_ago_suppressed_by_cooldown():
    signal = _task("TASK_OVERDUE", "normal", 5 * 24 * 3600)
    feedback = SuppressionState(surfaced_at=_NOW - timedelta(hours=3))
    ranked = scoring.rank_for_surface([signal], _NOW, "app_opened", {("task", 1): feedback})
    assert ranked[0].score == 75
    assert ranked[0].suppression.suppressed is True
    assert ranked[0].suppression.reason_code == "COOLDOWN"


def test_scenario_i_same_item_at_surfaced_at_plus_12h_exactly_is_eligible_again():
    signal = _task("TASK_OVERDUE", "normal", 5 * 24 * 3600)
    feedback = SuppressionState(surfaced_at=_NOW - timedelta(hours=12))
    ranked = scoring.rank_for_surface([signal], _NOW, "app_opened", {("task", 1): feedback})
    assert ranked[0].score == 75
    assert ranked[0].suppression.suppressed is False


def test_scenario_j_high_task_due_in_2h_dedup_retains_only_due_today():
    due_today = _task("TASK_DUE_TODAY", "high", 2 * 3600)
    due_soon = _task("TASK_DUE_SOON", "high", 2 * 3600)
    assert scoring.score_signal(due_today).score == 73
    assert scoring.score_signal(due_soon).score == 55
    ranked = scoring.rank_for_surface([due_today, due_soon], _NOW, "daily_brief")
    assert [c.signal.signal_type for c in ranked] == ["TASK_DUE_TODAY"]


# ==================================================
# §22 — Rounding boundary tests (explicit half-value proof)
# ==================================================


def test_round_half_up_on_exact_half_values():
    from fractions import Fraction

    assert scoring._round_half_up(Fraction(25, 2)) == 13  # 12.5 -> 13
    assert scoring._round_half_up(Fraction(5, 2)) == 3  # 2.5 -> 3
    assert scoring._round_half_up(Fraction(1, 2)) == 1  # 0.5 -> 1
    assert scoring._round_half_up(Fraction(3, 2)) == 2  # 1.5 -> 2
    assert scoring._round_half_up(Fraction(0)) == 0
    assert scoring._round_half_up(Fraction(3, 4)) == 1  # not a tie: rounds to nearest (1)
    assert scoring._round_half_up(Fraction(1, 4)) == 0  # not a tie: rounds to nearest (0)


def test_due_today_proximity_hits_exact_half_tie_and_rounds_up():
    # hours_until_due=2 -> 15*(1-2/12) = 12.5 exactly -> rounds to 13
    signal = _task("TASK_DUE_TODAY", "normal", 2 * 3600)
    candidate = scoring.score_signal(signal)
    proximity = next(c for c in candidate.reason_codes if c.code == "DUE_TODAY_PROXIMITY")
    assert proximity.score_delta == 13
    assert candidate.score == 40 + 0 + 13


def test_due_today_proximity_hits_a_second_exact_half_tie_and_rounds_up():
    # hours_until_due=10 -> 15*(1-10/12) = 2.5 exactly -> rounds to 3
    signal = _task("TASK_DUE_TODAY", "normal", 10 * 3600)
    candidate = scoring.score_signal(signal)
    proximity = next(c for c in candidate.reason_codes if c.code == "DUE_TODAY_PROXIMITY")
    assert proximity.score_delta == 3


def test_due_soon_proximity_hits_exact_half_tie_and_rounds_up():
    # hours_until_due=3 -> 20*(1-3/4) = 5 exactly, not a tie; use hours=3.5 for tie check instead:
    # 20*(1-3.5/4) = 20*0.125 = 2.5 exactly -> rounds to 3
    signal = _task("TASK_DUE_SOON", "normal", int(3.5 * 3600))
    candidate = scoring.score_signal(signal)
    proximity = next(c for c in candidate.reason_codes if c.code == "DUE_SOON_PROXIMITY")
    assert proximity.score_delta == 3


def test_event_proximity_hits_exact_half_tie_and_rounds_up():
    # minutes_until_start=108 -> 25*(1-108/120) = 25*0.1 = 2.5 exactly -> rounds to 3
    signal = _event(108 * 60)
    candidate = scoring.score_signal(signal)
    proximity = next(c for c in candidate.reason_codes if c.code == "EVENT_PROXIMITY")
    assert proximity.score_delta == 3


# ==================================================
# Locked base scores / priority / overdue / inbox-age formulas
# ==================================================


def test_all_five_locked_base_scores():
    assert scoring._BASE_SCORES["TASK_OVERDUE"] == 50
    assert scoring._BASE_SCORES["EVENT_UPCOMING"] == 45
    assert scoring._BASE_SCORES["TASK_DUE_TODAY"] == 40
    assert scoring._BASE_SCORES["TASK_DUE_SOON"] == 25
    assert scoring._BASE_SCORES["INBOX_NEEDS_ATTENTION"] == 15


def test_priority_contribution_high_normal_low():
    high = scoring.score_signal(_task("TASK_OVERDUE", "high", 0))
    normal = scoring.score_signal(_task("TASK_OVERDUE", "normal", 0))
    low = scoring.score_signal(_task("TASK_OVERDUE", "low", 0))
    assert high.score - normal.score == 20
    assert normal.score - low.score == 10


def test_overdue_contribution_caps_at_30_after_6_days():
    six_days = scoring.score_signal(_task("TASK_OVERDUE", "normal", 6 * 24 * 3600))
    ten_days = scoring.score_signal(_task("TASK_OVERDUE", "normal", 10 * 24 * 3600))
    assert six_days.score == 50 + 30
    assert ten_days.score == 50 + 30  # capped, not 50 + 50


def test_overdue_contribution_under_24h_is_zero():
    candidate = scoring.score_signal(_task("TASK_OVERDUE", "normal", 3600))
    assert candidate.score == 50


def test_inbox_age_caps_at_10_after_10_days():
    ten_days = scoring.score_signal(_inbox(10 * 24 * 3600))
    twenty_days = scoring.score_signal(_inbox(20 * 24 * 3600))
    assert ten_days.score == 15 + 10
    assert twenty_days.score == 15 + 10


def test_inbox_age_under_1_day_is_zero():
    candidate = scoring.score_signal(_inbox(3600))
    assert candidate.score == 15


# ==================================================
# §10 — Structural validation
# ==================================================


def test_task_signal_missing_priority_raises_scoring_error():
    bad = _task("TASK_OVERDUE", None, 3600)
    with pytest.raises(ScoringError):
        scoring.score_signal(bad)


def test_negative_measurement_raises_scoring_error():
    bad = _task("TASK_OVERDUE", "normal", -1)
    with pytest.raises(ScoringError):
        scoring.score_signal(bad)


def test_unsupported_signal_type_raises_scoring_error():
    bad = Signal(
        signal_type="SOMETHING_ELSE",  # type: ignore[arg-type]
        source_type="task",
        source_id=1,
        title="x",
        relevant_timestamp=_NOW,
        priority="normal",
        measurement_seconds=0,
        snapshot={},
    )
    with pytest.raises(ScoringError):
        scoring.score_signal(bad)


def test_mismatched_source_type_raises_scoring_error():
    bad = Signal(
        signal_type="TASK_OVERDUE",
        source_type="calendar_event",  # wrong: TASK_OVERDUE must be source_type="task"
        source_id=1,
        title="x",
        relevant_timestamp=_NOW,
        priority="normal",
        measurement_seconds=0,
        snapshot={},
    )
    with pytest.raises(ScoringError):
        scoring.score_signal(bad)


def test_non_task_signal_with_unexpected_priority_raises_scoring_error():
    bad = Signal(
        signal_type="EVENT_UPCOMING",
        source_type="calendar_event",
        source_id=1,
        title="x",
        relevant_timestamp=_NOW,
        priority="high",  # events must never carry a priority
        measurement_seconds=0,
        snapshot={},
    )
    with pytest.raises(ScoringError):
        scoring.score_signal(bad)


def test_due_soon_signal_outside_guaranteed_window_raises_scoring_error():
    bad = _task("TASK_DUE_SOON", "normal", 5 * 3600)  # 5h > the guaranteed <4h window
    with pytest.raises(ScoringError):
        scoring.score_signal(bad)


def test_event_signal_outside_guaranteed_window_raises_scoring_error():
    bad = _event(3 * 3600)  # 3h > the guaranteed <2h window
    with pytest.raises(ScoringError):
        scoring.score_signal(bad)


def test_locked_scoring_caps_are_not_treated_as_data_corruption():
    """The overdue/inbox-age caps are POLICY (§10), not corruption
    handling — a 30-day-overdue task must score normally, not raise."""
    candidate = scoring.score_signal(_task("TASK_OVERDUE", "normal", 30 * 24 * 3600))
    assert candidate.score == 50 + 30


# ==================================================
# §11/§12 — Dedup identity and cross-source behavior
# ==================================================


def test_same_source_dedup_keeps_only_highest_score():
    due_today = _task("TASK_DUE_TODAY", "low", 11 * 3600, source_id=7)  # low score
    due_soon = _task("TASK_DUE_SOON", "high", 1 * 3600, source_id=7)  # high score
    ranked = scoring.rank_for_surface([due_today, due_soon], _NOW, "daily_brief")
    assert len(ranked) == 1
    assert ranked[0].signal.signal_type == "TASK_DUE_SOON"


def test_dedup_tie_uses_locked_category_order_not_input_order():
    """Synthetic equal-score candidates for the same source identity —
    real formulas rarely tie exactly across two different categories,
    so this constructs the tie directly to test _dedup's own
    tie-break rule in isolation from scoring."""
    due_today = _task("TASK_DUE_TODAY", "normal", 0, source_id=9)
    overdue = _task("TASK_OVERDUE", "normal", 0, source_id=9)

    low_category = AttentionCandidate(signal=due_today, score=50, reason_codes=())
    high_category = AttentionCandidate(signal=overdue, score=50, reason_codes=())
    result_order_1 = scoring._dedup([low_category, high_category])
    result_order_2 = scoring._dedup([high_category, low_category])
    assert result_order_1[0].signal.signal_type == "TASK_OVERDUE"
    assert result_order_2[0].signal.signal_type == "TASK_OVERDUE"


def test_cross_source_signals_are_never_deduped():
    task_signal = _task("TASK_OVERDUE", "normal", 3600, source_id=42)
    inbox_signal = _inbox(3600, source_id=91)  # different source identity
    ranked = scoring.rank_for_surface([task_signal, inbox_signal], _NOW, "daily_brief")
    identities = {(c.signal.source_type, c.signal.source_id) for c in ranked}
    assert identities == {("task", 42), ("inbox_item", 91)}


# ==================================================
# §13 — Global tie-break
# ==================================================


def test_global_sort_is_score_descending():
    high = _task("TASK_OVERDUE", "normal", 5 * 24 * 3600, source_id=1)  # 75
    low = _inbox(3600, source_id=2)  # 15
    ranked = scoring.rank_for_surface([high, low], _NOW, "daily_brief")
    assert [c.score for c in ranked] == [75, 15]


def test_global_tie_break_falls_back_to_category_then_timestamp_then_source_id():
    # Equal scores, different categories -> category order decides.
    a = AttentionCandidate(
        signal=_task("TASK_DUE_TODAY", "normal", 0, source_id=1), score=50, reason_codes=(), suppression=None
    )
    b = AttentionCandidate(
        signal=_task("TASK_OVERDUE", "normal", 0, source_id=2), score=50, reason_codes=(), suppression=None
    )
    ordered = sorted([a, b], key=scoring._tie_break_key)
    assert [c.signal.signal_type for c in ordered] == ["TASK_OVERDUE", "TASK_DUE_TODAY"]

    # Equal score AND category -> earliest timestamp first.
    earlier_signal = replace(_task("TASK_OVERDUE", "normal", 0, source_id=3), relevant_timestamp=_NOW - timedelta(hours=1))
    earlier = AttentionCandidate(signal=earlier_signal, score=50, reason_codes=(), suppression=None)
    later = AttentionCandidate(
        signal=_task("TASK_OVERDUE", "normal", 0, source_id=4), score=50, reason_codes=(), suppression=None
    )
    ordered2 = sorted([later, earlier], key=scoring._tie_break_key)
    assert ordered2[0].signal.source_id == 3

    # Equal score, category, AND timestamp -> lowest source_id first.
    same_ts_high_id = _task("TASK_OVERDUE", "normal", 0, source_id=20)
    same_ts_low_id = _task("TASK_OVERDUE", "normal", 0, source_id=5)
    c1 = AttentionCandidate(signal=same_ts_high_id, score=50, reason_codes=(), suppression=None)
    c2 = AttentionCandidate(signal=same_ts_low_id, score=50, reason_codes=(), suppression=None)
    ordered3 = sorted([c1, c2], key=scoring._tie_break_key)
    assert ordered3[0].signal.source_id == 5


def test_missing_relevant_timestamp_sorts_after_present_ones():
    with_ts = _task("TASK_OVERDUE", "normal", 0, source_id=1)
    without_ts = replace(_task("TASK_OVERDUE", "normal", 0, source_id=2), relevant_timestamp=None)
    c_with = AttentionCandidate(signal=with_ts, score=50, reason_codes=(), suppression=None)
    c_without = AttentionCandidate(signal=without_ts, score=50, reason_codes=(), suppression=None)
    ordered = sorted([c_without, c_with], key=scoring._tie_break_key)
    assert ordered[0].signal.source_id == 1
    assert ordered[1].signal.source_id == 2


# ==================================================
# §14 — Threshold policies
# ==================================================


def test_app_opened_threshold_is_45():
    assert scoring._THRESHOLDS["app_opened"] == 45


def test_daily_brief_threshold_is_20():
    assert scoring._THRESHOLDS["daily_brief"] == 20


def test_score_between_thresholds_included_in_daily_brief_not_app_opened():
    signal = _inbox(30 * 24 * 3600)  # score 15 + 10 (capped) = 25
    app_opened = scoring.rank_for_surface([signal], _NOW, "app_opened")
    daily_brief = scoring.rank_for_surface([signal], _NOW, "daily_brief")
    assert app_opened[0].suppression.reason_code == "BELOW_THRESHOLD"
    assert daily_brief[0].suppression.suppressed is False


# ==================================================
# §23 — Suppression test matrix
# ==================================================


def test_suppression_no_feedback_is_eligible():
    result = scoring.evaluate_gates(None, _NOW, {"a": 1})
    assert result.suppressed is False
    assert result.reason_code is None


def test_suppression_cooldown_11h59m_ago_suppresses():
    feedback = SuppressionState(surfaced_at=_NOW - timedelta(hours=11, minutes=59))
    result = scoring.evaluate_gates(feedback, _NOW, {})
    assert result.suppressed is True
    assert result.reason_code == "COOLDOWN"


def test_suppression_cooldown_exactly_12h_ago_is_eligible():
    feedback = SuppressionState(surfaced_at=_NOW - timedelta(hours=12))
    result = scoring.evaluate_gates(feedback, _NOW, {})
    assert result.suppressed is False


def test_suppression_cooldown_more_than_12h_ago_is_eligible():
    feedback = SuppressionState(surfaced_at=_NOW - timedelta(hours=13))
    result = scoring.evaluate_gates(feedback, _NOW, {})
    assert result.suppressed is False


def test_suppression_snoozed_into_future_suppresses():
    feedback = SuppressionState(snoozed_until=_NOW + timedelta(hours=1))
    result = scoring.evaluate_gates(feedback, _NOW, {})
    assert result.suppressed is True
    assert result.reason_code == "SNOOZED"


def test_suppression_snoozed_exactly_until_now_is_eligible():
    feedback = SuppressionState(snoozed_until=_NOW)
    result = scoring.evaluate_gates(feedback, _NOW, {})
    assert result.suppressed is False


def test_suppression_snoozed_in_past_is_eligible():
    feedback = SuppressionState(snoozed_until=_NOW - timedelta(minutes=1))
    result = scoring.evaluate_gates(feedback, _NOW, {})
    assert result.suppressed is False


def test_suppression_dismissed_identical_snapshot_suppresses():
    snapshot = {"due_at": "2026-06-01T12:00:00+00:00", "status": "open"}
    feedback = SuppressionState(dismissed_at=_NOW - timedelta(hours=1), dismissed_snapshot=dict(snapshot))
    result = scoring.evaluate_gates(feedback, _NOW, snapshot)
    assert result.suppressed is True
    assert result.reason_code == "DISMISSED_UNCHANGED"


def test_suppression_dismissed_changed_snapshot_is_eligible():
    old_snapshot = {"due_at": "2026-06-01T12:00:00+00:00", "status": "open"}
    new_snapshot = {"due_at": "2026-06-02T12:00:00+00:00", "status": "open"}
    feedback = SuppressionState(dismissed_at=_NOW - timedelta(hours=1), dismissed_snapshot=old_snapshot)
    result = scoring.evaluate_gates(feedback, _NOW, new_snapshot)
    assert result.suppressed is False


def test_suppression_dismissed_unrelated_field_cannot_matter_because_absent_from_snapshot():
    # snapshot only ever contains due_at/status for Task signals — there
    # is no "unrelated field" slot for e.g. description to leak into, so
    # an edit to an unrelated field cannot change the snapshot at all.
    snapshot = {"due_at": "2026-06-01T12:00:00+00:00", "status": "open"}
    feedback = SuppressionState(dismissed_at=_NOW - timedelta(hours=1), dismissed_snapshot=dict(snapshot))
    result = scoring.evaluate_gates(feedback, _NOW, dict(snapshot))
    assert result.suppressed is True


def test_suppression_acted_on_alone_does_not_permanently_suppress():
    feedback = SuppressionState(acted_on_at=_NOW - timedelta(hours=1))
    result = scoring.evaluate_gates(feedback, _NOW, {})
    assert result.suppressed is False
    assert result.reason_code is None


def test_suppression_precedence_snooze_beats_dismiss_and_cooldown():
    snapshot = {"due_at": "x"}
    feedback = SuppressionState(
        snoozed_until=_NOW + timedelta(hours=1),
        dismissed_at=_NOW - timedelta(hours=1),
        dismissed_snapshot=dict(snapshot),
        surfaced_at=_NOW - timedelta(hours=1),
    )
    result = scoring.evaluate_gates(feedback, _NOW, snapshot)
    assert result.reason_code == "SNOOZED"


def test_suppression_precedence_dismiss_beats_cooldown():
    snapshot = {"due_at": "x"}
    feedback = SuppressionState(
        dismissed_at=_NOW - timedelta(hours=1),
        dismissed_snapshot=dict(snapshot),
        surfaced_at=_NOW - timedelta(hours=1),
    )
    result = scoring.evaluate_gates(feedback, _NOW, snapshot)
    assert result.reason_code == "DISMISSED_UNCHANGED"


def test_suppression_precedence_cooldown_beats_below_threshold():
    # A low-scoring signal that is ALSO in cooldown must report COOLDOWN,
    # not BELOW_THRESHOLD, per the locked precedence (gates evaluated
    # before threshold in rank_for_surface).
    signal = _inbox(3600, source_id=1)  # score 15, below both thresholds
    feedback = SuppressionState(surfaced_at=_NOW - timedelta(hours=1))
    ranked = scoring.rank_for_surface([signal], _NOW, "daily_brief", {("inbox_item", 1): feedback})
    assert ranked[0].suppression.reason_code == "COOLDOWN"


def test_suppression_below_threshold_only_when_no_gate_applies():
    signal = _inbox(3600, source_id=1)  # score 15, below daily_brief's 20
    ranked = scoring.rank_for_surface([signal], _NOW, "daily_brief")
    assert ranked[0].suppression.reason_code == "BELOW_THRESHOLD"


# ==================================================
# §24 — Determinism tests
# ==================================================


def test_same_inputs_produce_structurally_identical_output():
    signals = [
        _task("TASK_OVERDUE", "high", 2 * 24 * 3600, source_id=1),
        _event(45 * 60, source_id=2),
        _inbox(3 * 24 * 3600, source_id=3),
    ]
    run1 = scoring.rank_for_surface(signals, _NOW, "daily_brief")
    run2 = scoring.rank_for_surface(signals, _NOW, "daily_brief")
    assert [(c.signal.source_type, c.signal.source_id, c.score, c.suppression) for c in run1] == [
        (c.signal.source_type, c.signal.source_id, c.score, c.suppression) for c in run2
    ]


def test_shuffled_input_order_produces_identical_final_ordering():
    signals = [
        _task("TASK_OVERDUE", "high", 2 * 24 * 3600, source_id=1),
        _task("TASK_DUE_TODAY", "normal", 3 * 3600, source_id=2),
        _event(10 * 60, source_id=3),
        _inbox(5 * 24 * 3600, source_id=4),
        _task("TASK_OVERDUE", "low", 3600, source_id=5),
    ]
    baseline = [(c.signal.source_type, c.signal.source_id) for c in scoring.rank_for_surface(signals, _NOW, "daily_brief")]
    rng = random.Random(1234)
    for _ in range(5):
        shuffled = signals[:]
        rng.shuffle(shuffled)
        result = [(c.signal.source_type, c.signal.source_id) for c in scoring.rank_for_surface(shuffled, _NOW, "daily_brief")]
        assert result == baseline


def test_same_source_dedup_tie_does_not_depend_on_input_order():
    a = AttentionCandidate(signal=_task("TASK_DUE_TODAY", "normal", 0, source_id=1), score=50, reason_codes=())
    b = AttentionCandidate(signal=_task("TASK_OVERDUE", "normal", 0, source_id=1), score=50, reason_codes=())
    assert scoring._dedup([a, b])[0].signal.signal_type == "TASK_OVERDUE"
    assert scoring._dedup([b, a])[0].signal.signal_type == "TASK_OVERDUE"


def test_global_tie_does_not_depend_on_input_order():
    c1 = AttentionCandidate(signal=_task("TASK_OVERDUE", "normal", 0, source_id=10), score=50, reason_codes=())
    c2 = AttentionCandidate(signal=_task("TASK_OVERDUE", "normal", 0, source_id=3), score=50, reason_codes=())
    order1 = sorted([c1, c2], key=scoring._tie_break_key)
    order2 = sorted([c2, c1], key=scoring._tie_break_key)
    assert [c.signal.source_id for c in order1] == [3, 10]
    assert [c.signal.source_id for c in order2] == [3, 10]


# ==================================================
# §26 — Zero LLM / zero persistence (structural proof)
# ==================================================


def test_scoring_module_has_no_llm_or_persistence_imports():
    import inspect

    source = inspect.getsource(scoring)
    for forbidden in ("anthropic", "model_router", "orchestrator", "Session", "db.execute", "db.commit"):
        assert forbidden not in source, f"unexpected reference to {forbidden!r} in scoring.py"
