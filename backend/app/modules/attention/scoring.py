"""Checkpoint 4.3 — the deterministic Attention Scoring, Dedup &
Suppression engine.

Transforms the raw Signals produced by service.py's generate_signals
(Checkpoint 4.2) into scored, deduped, suppression-evaluated,
threshold-filtered, deterministically-ranked AttentionCandidates.

This module never queries the database, never calls the Model
Router/Anthropic, never creates a ProposedAction, and never persists
anything — it is a pure function of its own inputs. Security/space
scoping is entirely the responsibility of the 4.2 signal-generation
boundary (§25): every Signal handed to score_signal is already scoped.

attention_feedback persistence and any mutation API (snooze/dismiss/
mark-acted-on) are NOT implemented here — that is Checkpoint 4.4.
SuppressionState (schemas.py) is the narrow, deliberately storage-
agnostic input this module needs to evaluate the three feedback-based
gates; 4.4 constructs it from its own table, never the reverse.
"""

import math
from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction

from app.modules.attention.schemas import (
    AttentionCandidate,
    AttentionSurface,
    GateResult,
    ScoreComponent,
    ScoringError,
    Signal,
    SignalType,
    SuppressionState,
)

# ---- Locked v1 policy constants (Architecture Contract 4.0b) ----

#: Checkpoint 4.4a — the smallest explicit provenance mechanism the
#: architecture review asked for: a bare, manually-bumped string, never
#: a registry/dynamic policy engine. Persisted verbatim on every
#: AttentionExposure row (history.py) so a historical row's score stays
#: attributable to the policy that actually produced it. Bump this
#: string by hand whenever ANY locked constant in this module changes
#: (base scores, priority deltas, proximity formulas, thresholds,
#: cooldown, dedup/tie-break rules) — never silently recompute a past
#: exposure's score under a newer policy and present it as original.
POLICY_VERSION = "4.3"

_BASE_SCORES: dict[SignalType, int] = {
    "TASK_OVERDUE": 50,
    "EVENT_UPCOMING": 45,
    "TASK_DUE_TODAY": 40,
    "TASK_DUE_SOON": 25,
    "INBOX_NEEDS_ATTENTION": 15,
}

_PRIORITY_CONTRIBUTION: dict[str, int] = {"high": 20, "normal": 0, "low": -10}

# The SAME fixed category order used both to break an exact-score
# same-source dedup tie (§11) and as the second key in the global
# tie-break (§13) — one ordering, two consumers, never two separate
# orderings that could silently drift apart.
_SIGNAL_CATEGORY_ORDER: tuple[SignalType, ...] = (
    "TASK_OVERDUE",
    "EVENT_UPCOMING",
    "TASK_DUE_TODAY",
    "TASK_DUE_SOON",
    "INBOX_NEEDS_ATTENTION",
)
_CATEGORY_RANK: dict[SignalType, int] = {name: i for i, name in enumerate(_SIGNAL_CATEGORY_ORDER)}

_COOLDOWN = timedelta(hours=12)

_THRESHOLDS: dict[AttentionSurface, int] = {"app_opened": 45, "daily_brief": 20}

_TASK_SOURCE_TYPES: dict[SignalType, str] = {
    "TASK_OVERDUE": "task",
    "TASK_DUE_TODAY": "task",
    "TASK_DUE_SOON": "task",
}
_SOURCE_TYPE_BY_SIGNAL_TYPE: dict[SignalType, str] = {
    "TASK_OVERDUE": "task",
    "TASK_DUE_TODAY": "task",
    "TASK_DUE_SOON": "task",
    "EVENT_UPCOMING": "calendar_event",
    "INBOX_NEEDS_ATTENTION": "inbox_item",
}


def _round_half_up(value: Fraction) -> int:
    """The ONE explicit tie-break rule shared by every proximity formula
    (TASK_DUE_SOON/TASK_DUE_TODAY/EVENT_UPCOMING) — an exact .5 always
    rounds up (away from zero; every value reaching this function is
    already >= 0 via each formula's own max(0, ...) clamp or guaranteed-
    non-negative window). Deliberately NOT Python's built-in round(),
    which uses banker's-rounding-to-even: that would silently round
    12.5 down to 12 but 2.5 down to 2, an inconsistency the Checkpoint
    4.3 architecture review surfaced (its own worked scenario table
    required round(12.5)=12 alongside round(2.5)=3, which no single
    rule satisfies — round-half-up was the explicitly chosen resolution,
    with the scenario table's "72" corrected to 73 accordingly — see
    README.md's Checkpoint 4.3 section).

    Takes an exact Fraction, never a float — a float intermediate
    (e.g. 15 * (1 - 10/12) computed in binary floating point) can land
    a hair on either side of a true mathematical .5 tie purely from
    IEEE-754 representation error, which would make the tie-break rule
    depend on accidental float noise instead of the documented policy.
    """
    floor_value = math.floor(value)
    remainder = value - floor_value
    return floor_value + 1 if remainder >= Fraction(1, 2) else floor_value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScoringError(message)


def _validate_common(signal: Signal) -> None:
    expected_source_type = _SOURCE_TYPE_BY_SIGNAL_TYPE.get(signal.signal_type)
    _require(expected_source_type is not None, f"Unsupported signal_type: {signal.signal_type!r}")
    _require(
        signal.source_type == expected_source_type,
        f"Malformed signal: signal_type={signal.signal_type!r} paired with "
        f"source_type={signal.source_type!r} (expected {expected_source_type!r})",
    )
    if signal.signal_type in _TASK_SOURCE_TYPES:
        _require(
            signal.priority in _PRIORITY_CONTRIBUTION,
            f"Task-sourced signal (source_id={signal.source_id}, signal_type={signal.signal_type!r}) "
            f"has missing/invalid priority: {signal.priority!r}",
        )
    else:
        _require(
            signal.priority is None,
            f"Non-task signal (source_id={signal.source_id}, signal_type={signal.signal_type!r}) "
            f"unexpectedly carries a priority: {signal.priority!r}",
        )
    _require(
        signal.measurement_seconds >= 0,
        f"Signal (source_id={signal.source_id}, signal_type={signal.signal_type!r}) has negative "
        f"measurement_seconds: {signal.measurement_seconds!r}",
    )


def _score_task_overdue(signal: Signal) -> tuple[int, tuple[ScoreComponent, ...]]:
    base = _BASE_SCORES["TASK_OVERDUE"]
    priority_delta = _PRIORITY_CONTRIBUTION[signal.priority]  # type: ignore[index]
    overdue_hours = Fraction(signal.measurement_seconds) / 3600
    overdue_days = math.floor(overdue_hours / 24)
    overdue_delta = min(30, overdue_days * 5)
    components = (
        ScoreComponent("TASK_OVERDUE_BASE", None, base),
        ScoreComponent(f"PRIORITY_{signal.priority.upper()}", signal.priority, priority_delta),  # type: ignore[union-attr]
        ScoreComponent("OVERDUE_DAYS", overdue_days, overdue_delta),
    )
    return base + priority_delta + overdue_delta, components


def _score_task_due_today(signal: Signal) -> tuple[int, tuple[ScoreComponent, ...]]:
    base = _BASE_SCORES["TASK_DUE_TODAY"]
    priority_delta = _PRIORITY_CONTRIBUTION[signal.priority]  # type: ignore[index]
    hours_until_due = Fraction(signal.measurement_seconds) / 3600
    raw = 15 * max(Fraction(0), 1 - hours_until_due / 12)
    proximity_delta = _round_half_up(raw)
    components = (
        ScoreComponent("TASK_DUE_TODAY_BASE", None, base),
        ScoreComponent(f"PRIORITY_{signal.priority.upper()}", signal.priority, priority_delta),  # type: ignore[union-attr]
        ScoreComponent("DUE_TODAY_PROXIMITY", float(hours_until_due), proximity_delta),
    )
    return base + priority_delta + proximity_delta, components


def _score_task_due_soon(signal: Signal) -> tuple[int, tuple[ScoreComponent, ...]]:
    base = _BASE_SCORES["TASK_DUE_SOON"]
    priority_delta = _PRIORITY_CONTRIBUTION[signal.priority]  # type: ignore[index]
    hours_until_due = Fraction(signal.measurement_seconds) / 3600
    _require(
        0 <= hours_until_due < 4,
        f"TASK_DUE_SOON signal (source_id={signal.source_id}) has hours_until_due="
        f"{float(hours_until_due)!r} outside the guaranteed [0, 4) window",
    )
    raw = 20 * (1 - hours_until_due / 4)
    proximity_delta = _round_half_up(raw)
    components = (
        ScoreComponent("TASK_DUE_SOON_BASE", None, base),
        ScoreComponent(f"PRIORITY_{signal.priority.upper()}", signal.priority, priority_delta),  # type: ignore[union-attr]
        ScoreComponent("DUE_SOON_PROXIMITY", float(hours_until_due), proximity_delta),
    )
    return base + priority_delta + proximity_delta, components


def _score_event_upcoming(signal: Signal) -> tuple[int, tuple[ScoreComponent, ...]]:
    base = _BASE_SCORES["EVENT_UPCOMING"]
    minutes_until_start = Fraction(signal.measurement_seconds) / 60
    _require(
        0 <= minutes_until_start < 120,
        f"EVENT_UPCOMING signal (source_id={signal.source_id}) has minutes_until_start="
        f"{float(minutes_until_start)!r} outside the guaranteed [0, 120) window",
    )
    raw = 25 * (1 - minutes_until_start / 120)
    proximity_delta = _round_half_up(raw)
    components = (
        ScoreComponent("EVENT_UPCOMING_BASE", None, base),
        ScoreComponent("EVENT_PROXIMITY", float(minutes_until_start), proximity_delta),
    )
    return base + proximity_delta, components


def _score_inbox_needs_attention(signal: Signal) -> tuple[int, tuple[ScoreComponent, ...]]:
    base = _BASE_SCORES["INBOX_NEEDS_ATTENTION"]
    age_hours = Fraction(signal.measurement_seconds) / 3600
    age_days = math.floor(age_hours / 24)
    age_delta = min(10, age_days)
    components = (
        ScoreComponent("INBOX_NEEDS_ATTENTION_BASE", None, base),
        ScoreComponent("INBOX_AGE_DAYS", age_days, age_delta),
    )
    return base + age_delta, components


_SCORERS = {
    "TASK_OVERDUE": _score_task_overdue,
    "TASK_DUE_TODAY": _score_task_due_today,
    "TASK_DUE_SOON": _score_task_due_soon,
    "EVENT_UPCOMING": _score_event_upcoming,
    "INBOX_NEEDS_ATTENTION": _score_inbox_needs_attention,
}


def score_signal(signal: Signal) -> AttentionCandidate:
    """Scores exactly one Signal in isolation — no dedup, no
    suppression, no `now` needed (every input this depends on is
    already carried on the Signal itself). Raises ScoringError on
    structurally impossible Signal data (§10) rather than silently
    producing a nonsense score.
    """
    _validate_common(signal)
    scorer = _SCORERS[signal.signal_type]
    score, components = scorer(signal)
    return AttentionCandidate(signal=signal, score=score, reason_codes=components, suppression=None)


def _dedup(candidates: list[AttentionCandidate]) -> list[AttentionCandidate]:
    """Same-source dedup (§11): identity is (source_type, source_id).
    The single highest-scoring candidate for each identity survives;
    scores are never summed and signals are never merged. An exact-score
    tie is broken by the locked signal-category order (§13's SAME
    order), never by input/list position.
    """
    best: dict[tuple[str, int], AttentionCandidate] = {}
    for candidate in candidates:
        key = (candidate.signal.source_type, candidate.signal.source_id)
        current = best.get(key)
        if current is None or candidate.score > current.score:
            best[key] = candidate
        elif candidate.score == current.score:
            if _CATEGORY_RANK[candidate.signal.signal_type] < _CATEGORY_RANK[current.signal.signal_type]:
                best[key] = candidate
    return list(best.values())


def evaluate_gates(feedback: SuppressionState | None, now: datetime, current_snapshot: dict) -> GateResult:
    """The three feedback-based suppression gates (snooze, dismiss,
    cooldown), evaluated in the LOCKED §20 precedence order — the
    FIRST matching gate's reason_code is returned; later gates are
    never even checked once one matches, so a source that is both
    snoozed and in cooldown always reports SNOOZED (§23 test 12/13).

    Deliberately does NOT apply a surface threshold — that is
    surface-specific and applied separately by rank_for_surface, never
    inside this feedback-only evaluator. Deliberately does NOT consult
    acted_on_at (§19: a historical acted-on exposure never itself
    suppresses a new, current Signal).

    now is the caller's own already-resolved evaluation instant —
    never computed here (§16: "do not call datetime.now() inside
    suppression helpers").
    """
    if feedback is None:
        return GateResult(suppressed=False, reason_code=None)

    if feedback.snoozed_until is not None and now < feedback.snoozed_until:
        return GateResult(suppressed=True, reason_code="SNOOZED")

    if (
        feedback.dismissed_at is not None
        and feedback.dismissed_snapshot is not None
        and feedback.dismissed_snapshot == current_snapshot
    ):
        return GateResult(suppressed=True, reason_code="DISMISSED_UNCHANGED")

    if feedback.surfaced_at is not None and now < feedback.surfaced_at + _COOLDOWN:
        return GateResult(suppressed=True, reason_code="COOLDOWN")

    return GateResult(suppressed=False, reason_code=None)


def _tie_break_key(candidate: AttentionCandidate) -> tuple:
    """The locked §13 global tie-break, applied only among candidates
    that already passed suppression/threshold: score descending,
    signal-category order, earliest relevant_timestamp, lowest
    source_id. A missing relevant_timestamp sorts AFTER every candidate
    that has one (within the same score+category group) — encoded as a
    leading 0/1 flag so a real timestamp is never compared directly
    against None.
    """
    signal = candidate.signal
    timestamp_key = (0, signal.relevant_timestamp) if signal.relevant_timestamp is not None else (1, None)
    return (-candidate.score, _CATEGORY_RANK[signal.signal_type], timestamp_key, signal.source_id)


def rank_for_surface(
    signals: list[Signal],
    now: datetime,
    surface: AttentionSurface,
    feedback_by_source: dict[tuple[str, int], SuppressionState] | None = None,
) -> list[AttentionCandidate]:
    """The full Checkpoint 4.3 pipeline: score every Signal, dedup by
    source identity, evaluate suppression (gates, then this surface's
    own locked threshold) for each deduped candidate, and return ALL of
    them — eligible candidates FIRST, deterministically sorted by the
    global tie-break, followed by every suppressed candidate (each
    still carrying its own populated `suppression` for explainability/
    audit, in no particular further order). Callers that want only what
    should actually be shown filter for `c.suppression.suppressed is
    False`; callers auditing why something was excluded can inspect
    every candidate's `suppression.reason_code` directly.

    feedback_by_source is the only place attention_feedback state
    enters this pure engine — keyed by (source_type, source_id), never
    persisted or queried here (Checkpoint 4.4 owns that).
    """
    feedback_by_source = feedback_by_source or {}
    threshold = _THRESHOLDS[surface]

    scored = [score_signal(signal) for signal in signals]
    deduped = _dedup(scored)

    evaluated: list[AttentionCandidate] = []
    for candidate in deduped:
        key = (candidate.signal.source_type, candidate.signal.source_id)
        gate = evaluate_gates(feedback_by_source.get(key), now, candidate.signal.snapshot)
        if gate.suppressed:
            evaluated.append(replace(candidate, suppression=gate))
            continue
        if candidate.score < threshold:
            evaluated.append(replace(candidate, suppression=GateResult(suppressed=True, reason_code="BELOW_THRESHOLD")))
            continue
        evaluated.append(replace(candidate, suppression=GateResult(suppressed=False, reason_code=None)))

    eligible = sorted((c for c in evaluated if not c.suppression.suppressed), key=_tie_break_key)  # type: ignore[union-attr]
    suppressed = [c for c in evaluated if c.suppression.suppressed]  # type: ignore[union-attr]
    return eligible + suppressed
