"""Checkpoint 4.5c — the deterministic APP_OPENED decision/orchestration
layer.

This module answers exactly one question: "given the current,
authoritative persisted state, should BAZRA speak first right now, and
if so about what?" It is 100% read-only — no commit, no ChatMessage,
no AttentionExposure, no domain mutation, no ProposedAction mutation,
no provider call. Narration and surfacing/persistence both belong to a
later checkpoint; this module only ever returns a decision.

TERMINOLOGY — LOCKED (Checkpoint 4.5c correction to 4.5b):

This module does NOT implement, detect, or prove "Attention Resume" in
the sense of "Ahmed genuinely returned to BAZRA after a meaningful
absence." The repository has no authoritative presence/session/
last-seen state (confirmed in the 4.5a inspection), and V1 deliberately
does not add one — no session table, no presence table, no heartbeat,
no last_seen_at, no visibility/focus/reconnect listener, no cross-tab
synchronization.

What IS implemented is the narrower PROACTIVE FREQUENCY GATE: "has
BAZRA already proactively surfaced something on the app_opened surface
recently enough that it should stay quiet for now?" — answered purely
from existing AttentionExposure history (MAX(surfaced_at) WHERE
surface='app_opened'), never from anything the frontend claims. A
future frontend "app opened" trigger is only ever a trigger to
RE-EVALUATE this policy — it is never itself evidence that a genuine
human return occurred.

Conceptual pipeline (each stage short-circuits the next on failure —
cheap deterministic gates run before the more expensive signal
generation, even though nothing in this checkpoint ever reaches a
provider call regardless):

    evaluate_app_opened()
        -> proactive frequency gate
        -> Moment Quality: pending ProposedAction
        -> Moment Quality: active conversation
        -> generate_signals / load_suppression_states / rank_for_surface
        -> first unsuppressed winner, or SILENCE

Responsibility separation (kept distinct even though one function
coordinates all four):
    A. Proactive Frequency Gate — "did BAZRA already use its one
       app_opened turn recently?" (this module, from AttentionExposure
       history only).
    B. Moment Quality — "is this otherwise-fine moment suitable for an
       UNRELATED new proactive topic?" (this module: pending-action and
       active-conversation hard gates only).
    C. Attention Selection — "what currently deserves attention?" (NOT
       owned by this module — delegates entirely to the existing,
       unmodified service.generate_signals / history.load_suppression_states
       / scoring.rank_for_surface pipeline).
    D. Narration — "how should BAZRA say it?" — NOT implemented here.

"No eligible candidate" is an Attention Selection outcome, never
classified as a Moment Quality failure — it has its own reason code.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.actions import service as actions_service
from app.modules.attention import history, scoring
from app.modules.attention import service as signal_service
from app.modules.attention.models import AttentionExposure
from app.modules.attention.schemas import AttentionCandidate
from app.modules.chat.models import ChatMessage

#: V1 policy constants — reasoned defaults, not deeply validated
#: product truths (same status as the Architecture Contract's own
#: locked 12h cooldown / 4h / 2h signal windows). Tunable later.
#: Deliberately NOT added to scoring.POLICY_VERSION — that constant is
#: specifically about the scoring/suppression formula; nothing in this
#: module persists a per-decision row that could need attributing back
#: to "which policy produced it," so no version string is introduced
#: here (no current consumer would ever read it).
PROACTIVE_FREQUENCY_WINDOW = timedelta(minutes=30)
ACTIVE_CONVERSATION_WINDOW = timedelta(minutes=10)

#: Checkpoint 4.7 — a V1 PRODUCT POLICY CONSTANT, not a learned value and
#: not an architectural truth. Deliberately SEPARATE from, and longer
#: than, scoring.py's own general-purpose 12h per-source COOLDOWN (which
#: stays unchanged and still applies across every surface, including
#: app_opened, for every candidate regardless of signal_type/snapshot).
#: This window answers a narrower question specific to THIS surface:
#: "did app_opened proactively raise this EXACT same unresolved concern
#: recently enough that repeating it would feel like nagging?" — see
#: same_concern_recently_surfaced's own docstring.
SAME_CONCERN_REPEAT_WINDOW = timedelta(hours=24)

SilenceReason = Literal[
    "proactive_frequency", "pending_action", "active_conversation", "no_eligible_candidate",
    "same_concern_recently_surfaced",
]


@dataclass(frozen=True)
class AppOpenedDecision:
    """The smallest explicit result distinguishing SPEAK from SILENCE.
    Deliberately carries only what this checkpoint's own pipeline
    actually produces — reasons that belong to a LATER
    revalidation/surfacing checkpoint (e.g. a candidate going stale
    during narration, or a user message winning a race against
    in-flight narration) are NOT represented here; this is a decision
    about current state, not a record of what happened during a later
    narration attempt.
    """

    outcome: Literal["speak", "silence"]
    candidate: AttentionCandidate | None = None
    reason: SilenceReason | None = None


def _latest_app_opened_surfaced_at(db: Session, space_id: int) -> datetime | None:
    """The sole evidence behind the Proactive Frequency Gate — the most
    recent instant BAZRA actually delivered something on the
    app_opened surface, for this space, across ALL sources (compare
    with per-source Attention cooldown, which is scoped to one
    (source_type, source_id) at a time and is NOT what this function
    answers). Returns None if app_opened has never surfaced anything
    for this space.
    """
    return db.execute(
        select(AttentionExposure.surfaced_at)
        .where(AttentionExposure.space_id == space_id, AttentionExposure.surface == "app_opened")
        .order_by(AttentionExposure.surfaced_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _proactive_frequency_gate_passes(db: Session, space_id: int, now: datetime) -> bool:
    """True = pass (BAZRA may re-evaluate); False = too soon, stay
    silent. The boundary is exact and tested: an exposure exactly
    PROACTIVE_FREQUENCY_WINDOW old no longer blocks."""
    latest = _latest_app_opened_surfaced_at(db, space_id)
    if latest is None:
        return True
    return now - latest >= PROACTIVE_FREQUENCY_WINDOW


def _has_recent_chat_activity(db: Session, space_id: int, user_id: int, now: datetime) -> bool:
    """True = a ChatMessage (any role) exists strictly newer than
    ACTIVE_CONVERSATION_WINDOW ago, for THIS space/user — authoritative
    persisted data only, never frontend-reported activity state. The
    boundary is exact and tested: a message exactly
    ACTIVE_CONVERSATION_WINDOW old no longer counts as "active."
    """
    cutoff = now - ACTIVE_CONVERSATION_WINDOW
    exists = db.execute(
        select(ChatMessage.id)
        .where(
            ChatMessage.space_id == space_id,
            ChatMessage.user_id == user_id,
            ChatMessage.created_at > cutoff,
        )
        .limit(1)
    ).scalar_one_or_none()
    return exists is not None


def _same_concern_recently_surfaced(db: Session, space_id: int, candidate: AttentionCandidate, now: datetime) -> bool:
    """Checkpoint 4.7 — the SAME-CONCERN REPEAT GATE: distinct from (and
    checked IN ADDITION to) the global Proactive Frequency Gate above.
    That gate answers "how recently did BAZRA proactively speak AT ALL on
    this surface"; this one answers "how recently did BAZRA proactively
    raise THIS SAME concern" — the two are independent, and both must
    pass for a candidate to be spoken.

    "Same concern" = the latest app_opened exposure for this EXACT
    (source_type, source_id, signal_type) identity was surfaced within
    SAME_CONCERN_REPEAT_WINDOW AND its own exposure_snapshot equals the
    candidate's CURRENT Signal.snapshot — the identical snapshot-equality
    convention scoring.evaluate_gates' own DISMISSED_UNCHANGED gate
    already established (never a bespoke "which fields matter" heuristic).
    Deliberately NEVER compares narration text, embeddings, or asks a
    model — fully deterministic, zero provider cost either way.

    Snapshot never includes `title` (see service.py's own signal
    construction) — a title-only change can never make this method treat
    an otherwise-unchanged concern as new, matching the conservative "be
    careful about treating a title change as new" product policy. A
    genuine material change (e.g. due_at pushed out) changes the
    snapshot, correctly classifying it as a different concern; a
    fully-resolved concern (done/archived) never reaches this function at
    all, since fresh signal generation stops producing it upstream —
    this gate is never the thing deciding "is the concern still real",
    only "did we just mention this exact same thing too recently."

    True = SAME concern, recently surfaced — caller must suppress
    (SILENCE), never fall back to a runner-up candidate (locked V1
    product decision — see module docstring / README).
    """
    signal = candidate.signal
    exposure = history.get_latest_exposure_for_concern(
        db, space_id, signal.source_type, signal.source_id, signal.signal_type, "app_opened"
    )
    if exposure is None:
        return False
    if now - exposure.surfaced_at >= SAME_CONCERN_REPEAT_WINDOW:
        return False
    return exposure.exposure_snapshot == signal.snapshot


def same_concern_recently_surfaced(db: Session, space_id: int, candidate: AttentionCandidate, now: datetime) -> bool:
    """Checkpoint 4.7 — a thin public re-export of
    _same_concern_recently_surfaced, reused (never duplicated) by
    attention/surfacing.py's own finalization-time recheck — the same
    "two tabs can both pass the pre-narration check" race
    frequency_gate_passes already protects against, applied here to the
    narrower same-concern identity instead of the global surface-wide
    one."""
    return _same_concern_recently_surfaced(db, space_id, candidate, now)


def frequency_gate_passes(db: Session, space_id: int, now: datetime) -> bool:
    """Checkpoint 4.5e — a thin public re-export of the exact same
    Proactive Frequency Gate `evaluate_app_opened` already applies
    below, reused (never duplicated or reimplemented) by
    attention/surfacing.py's own finalization-time recheck — see that
    module's own docstring for why a fresh recheck under the advisory
    lock is required even though `evaluate_app_opened` already checked
    this once, earlier, before narration."""
    return _proactive_frequency_gate_passes(db, space_id, now)


def evaluate_app_opened(
    db: Session, space_id: int, user_id: int, now: datetime, timezone_name: str
) -> AppOpenedDecision:
    """The single entry point for this checkpoint. Pure read-only
    decision: no commit, no write of any kind, no provider call. `now`
    and `timezone_name` are always explicit, caller-supplied values —
    this function never calls datetime.now() itself and never infers a
    timezone from anything (IP, server clock, a stale exposure, a
    browser guess) — an invalid `timezone_name` propagates exactly as
    generate_signals' own InvalidTimezoneError already does, with no
    silent fallback.

    Gate order (cheap, deterministic checks first — see module
    docstring): Proactive Frequency -> pending ProposedAction -> active
    conversation -> Attention Selection -> Same-Concern Repeat Gate
    (Checkpoint 4.7, checked only against the already-selected winner).
    No local-time/quiet-hours gate exists anywhere in this pipeline,
    deliberately: Ahmed actively opening BAZRA is not equivalent to an
    unsolicited push notification, so an otherwise-eligible winner at 2
    AM still produces outcome="speak".
    """
    if not _proactive_frequency_gate_passes(db, space_id, now):
        return AppOpenedDecision(outcome="silence", reason="proactive_frequency")

    if actions_service.get_latest_pending(db, space_id, user_id) is not None:
        return AppOpenedDecision(outcome="silence", reason="pending_action")

    if _has_recent_chat_activity(db, space_id, user_id, now):
        return AppOpenedDecision(outcome="silence", reason="active_conversation")

    signals = signal_service.generate_signals(db, space_id, now, timezone_name)
    identities = [(signal.source_type, signal.source_id) for signal in signals]
    feedback_by_source = history.load_suppression_states(db, space_id, identities)
    ranked = scoring.rank_for_surface(signals, now, "app_opened", feedback_by_source)

    # Winner Invariant (locked, critical): rank_for_surface returns
    # eligible candidates first, suppressed ones afterwards — a
    # non-empty list does NOT mean a winner exists. Never blindly use
    # ranked[0]; always find the first candidate whose own
    # suppression.suppressed is explicitly False.
    winner = next((candidate for candidate in ranked if candidate.suppression.suppressed is False), None)
    if winner is None:
        return AppOpenedDecision(outcome="silence", reason="no_eligible_candidate")

    # Same-Concern Repeat Gate (Checkpoint 4.7) — checked ONLY against the
    # already-selected winner, never against the ranked list generally:
    # if the strongest candidate was just raised too recently, the
    # correct V1 behavior is SILENCE, never a cascade to the runner-up
    # (rotating through B, then C, then D on successive app-opens would
    # recreate exactly the nagging feeling this gate exists to prevent).
    if _same_concern_recently_surfaced(db, space_id, winner, now):
        return AppOpenedDecision(outcome="silence", reason="same_concern_recently_surfaced")

    return AppOpenedDecision(outcome="speak", candidate=winner)
