"""Checkpoint 4.5e — APP_OPENED Production Wiring, Revalidation, and
Concurrency.

This is the FIRST checkpoint where BAZRA may actually persist and
surface an assistant-initiated conversational opening. It wires three
already-accepted, unmodified layers into one production-safe flow:

    1. WHAT/WHETHER  — attention/app_opened.py (4.5c), untouched
    2. HOW            — narration/service.py (4.5d), untouched
    3. durable, race-safe SURFACING — this module (new)

Locked pipeline (see each phase's own comment below):

    Phase A (no advisory lock): capture the evaluation anchor, then
      call the existing evaluate_app_opened. A SILENCE here returns
      immediately — zero narration calls, zero writes.
    Phase B (no advisory lock): for a SPEAK decision, call the
      existing generate_app_opened_narration. This is the only phase
      that may take provider-latency seconds; it runs with NO
      database lock held, by construction (the lock is acquired only
      in Phase C below).
    Phase C (advisory lock held for a SHORT, local-only critical
      section): re-acquire the exact same (space_id, user_id)
      advisory-lock key chat/service.py's own conversation turns
      already use, then re-check every condition that could have
      changed while Phase B was in flight — user-message-wins,
      pending-action-appeared, frequency-became-fresh,
      same-concern-became-recently-surfaced (Checkpoint 4.7), and
      selected-candidate-became-stale — and, only if ALL of them still
      pass, persist the assistant ChatMessage and the AttentionExposure
      together in ONE transaction.

Core invariant, stated the way the accepted brief states it:
DECIDE -> NARRATE -> VERIFY/FALLBACK -> REVALIDATE -> PERSIST
ATOMICALLY -> SURFACE. Never PERSIST -> VERIFY. Never NARRATE and then
assume the candidate is still true. Never hold the advisory lock
across provider latency.

STALE CANDIDATE POLICY — LOCKED: if the selected candidate becomes
stale (or any other finalization check fails) during narration, the
result is SILENCE. There is no cascade to a runner-up candidate, no
second narration call, and no rerank — the narration text in hand
describes exactly the ONE candidate that was selected, and showing it
about a DIFFERENT candidate (or re-ranking and picking a new one after
the fact) would create a truth mismatch. V1 intentionally chooses
silence over a cascade.

EXPOSURE SEMANTICS — LOCKED: a successful AttentionExposure write means
BAZRA's backend committed a proactive item intended for presentation.
It does NOT prove the frontend rendered it or that Ahmed actually saw
it — there is no display acknowledgment in V1, and none is added here
(no rendered_at/seen_at/delivery-receipt column or field).

ACTION AUTHORITY — ABSOLUTE: this module creates no ProposedAction,
ever. The persisted assistant ChatMessage grants no action authority
by itself — Phase 3's existing confirm/reject machinery is completely
unaffected; a bare "yes" after a proactive opening is ordinary chat
input, handled entirely by chat_service's own existing routing, same
as any other message.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.actions import service as actions_service
from app.modules.attention import app_opened as app_opened_service
from app.modules.attention import history, revalidation
from app.modules.chat import service as chat_service
from app.modules.chat.models import ChatMessage
from app.modules.narration import service as narration_service

#: The four 4.5c-own silence reasons (see app_opened.SilenceReason) are
#: a strict subset of this set — this module never redefines them,
#: only adds the four NEW finalization-time reasons this checkpoint
#: introduces. Internal/observability only — never exposed by the API
#: response schema (see attention/schemas.py's own AppOpenedResponse).
SurfaceSilenceReason = Literal[
    "proactive_frequency", "pending_action", "active_conversation", "no_eligible_candidate",
    "same_concern_recently_surfaced",
    "user_message_won_race", "pending_action_appeared", "frequency_became_fresh", "candidate_became_stale",
]


@dataclass(frozen=True)
class AppOpenedSurfaceResult:
    """`message` is populated only when status == "surfaced" — the
    real, persisted assistant ChatMessage ORM row (the router maps it
    to ChatMessageResponse). `reason` is internal/test observability
    only, exactly like AppOpenedDecision.reason in 4.5c; the public API
    response never exposes it."""

    status: Literal["silence", "surfaced"]
    message: ChatMessage | None = None
    reason: SurfaceSilenceReason | None = None


def _latest_chat_message_id(db: Session, space_id: int, user_id: int) -> int:
    """The evaluation anchor — a monotonic, persisted-DB-evidence
    marker of "the conversation state right before this proactive
    evaluation began." 0 (never None) when no ChatMessage exists yet
    for this (space_id, user_id), so the later ">" comparison in
    `_newer_user_message_exists` works uniformly with no special-cased
    None handling."""
    return db.execute(
        select(func.coalesce(func.max(ChatMessage.id), 0)).where(
            ChatMessage.space_id == space_id, ChatMessage.user_id == user_id
        )
    ).scalar_one()


def _newer_user_message_exists(db: Session, space_id: int, user_id: int, anchor_message_id: int) -> bool:
    """USER MESSAGE WINS — locked semantics: specifically a NEW USER
    message strictly after the anchor, never any role. An assistant
    row that appeared after the anchor (e.g. a concurrent duplicate
    APP_OPENED request that already persisted its own opening) is
    deliberately NOT grounds to abandon this one via THIS check —
    that race is instead what the frequency recheck
    (frequency_gate_passes) and the atomic-persistence race below
    (only one writer's commit can win) already handle.
    """
    return db.execute(
        select(ChatMessage.id)
        .where(
            ChatMessage.space_id == space_id,
            ChatMessage.user_id == user_id,
            ChatMessage.role == "user",
            ChatMessage.id > anchor_message_id,
        )
        .limit(1)
    ).scalar_one_or_none() is not None


def evaluate_and_surface_app_opened(
    db: Session, space_id: int, user_id: int, timezone_name: str
) -> AppOpenedSurfaceResult:
    """The single production entry point this checkpoint's own API
    endpoint calls. `timezone_name` is the one piece of information the
    client legitimately supplies (the same accepted timezone mechanism
    already used elsewhere) — `now` is always generated here, server-
    side, fresh for each phase that needs one; the caller (the HTTP
    endpoint) never passes a `now` of its own.

    Deterministic-silence conditions discovered here (proactive
    frequency, pending action, user-message-wins, a stale candidate)
    are NOT errors — they return a normal, successful
    AppOpenedSurfaceResult(status="silence", ...). A genuine database
    error always propagates normally; nothing here ever converts an
    unexpected failure into a silent, successful-looking result.
    """
    # Phase A — read/evaluate. No advisory lock held. The anchor is
    # captured first, before anything else (including the 4.5c decision
    # call itself), so it reflects the EARLIEST possible instant —
    # conservatively catching a race even if one occurred during 4.5c's
    # own (brief) evaluation window, not only during narration.
    anchor_message_id = _latest_chat_message_id(db, space_id, user_id)

    now_initial = datetime.now(timezone.utc)
    decision = app_opened_service.evaluate_app_opened(db, space_id, user_id, now_initial, timezone_name)
    if decision.outcome == "silence":
        # Required: zero narration calls, zero verifier calls, zero
        # writes of any kind for an initial 4.5c silence.
        return AppOpenedSurfaceResult(status="silence", reason=decision.reason)

    candidate = decision.candidate

    # Phase B — provider narration/verification. Still no advisory
    # lock held — this is the only phase that may take real seconds,
    # and it must never hold the database lock while it does.
    narration_result = narration_service.generate_app_opened_narration(candidate)

    # Phase C — short, local-only authoritative finalization
    # transaction. Acquires the SAME (space_id, user_id) advisory-lock
    # key chat/service.py's own ordinary conversation turns already
    # use (chat_service.acquire_conversation_lock) — never a second
    # lock namespace — so a proactive opening and an ordinary chat
    # turn for the same conversation are serialized against each
    # other, and so two concurrent APP_OPENED requests for the same
    # conversation are serialized against each other too.
    chat_service.acquire_conversation_lock(db, space_id, user_id)
    now_final = datetime.now(timezone.utc)

    # USER MESSAGE WINS (locked): a newer user message means Ahmed is
    # actively in the middle of a real conversational turn — do not
    # interrupt it with an opening generated from an older evaluation.
    if _newer_user_message_exists(db, space_id, user_id, anchor_message_id):
        db.commit()  # releases the advisory lock; nothing was written
        return AppOpenedSurfaceResult(status="silence", reason="user_message_won_race")

    # PENDING ACTION WINS (locked): an unresolved write confirmation
    # must never be pushed aside by unrelated proactive speech.
    if actions_service.get_latest_pending(db, space_id, user_id) is not None:
        db.commit()
        return AppOpenedSurfaceResult(status="silence", reason="pending_action_appeared")

    # Surface-wide frequency recheck (locked) — re-reads
    # AttentionExposure.surfaced_at fresh, under this lock. Catches the
    # two-tabs-both-pass-4.5c race: whichever request's finalization
    # transaction commits FIRST makes the loser's own recheck here see
    # that fresh exposure and correctly return silence.
    if not app_opened_service.frequency_gate_passes(db, space_id, now_final):
        db.commit()
        return AppOpenedSurfaceResult(status="silence", reason="frequency_became_fresh")

    # Same-Concern Repeat Gate recheck (Checkpoint 4.7) — the narrower,
    # 24h, signal_type+snapshot-scoped counterpart to the frequency
    # recheck above. Catches the identical two-tabs race for THIS
    # specific gate: both requests may have passed the pre-narration
    # same_concern_recently_surfaced check in evaluate_app_opened before
    # either had recorded an exposure; whichever commits first here
    # makes the loser's own recheck see that fresh exposure and
    # correctly return silence instead of double-surfacing the same
    # concern.
    if app_opened_service.same_concern_recently_surfaced(db, space_id, candidate, now_final):
        db.commit()
        return AppOpenedSurfaceResult(status="silence", reason="same_concern_recently_surfaced")

    # Selected-candidate revalidation (locked): is the ONE concern this
    # narration describes still real right now? Never a rerank, never
    # a different candidate — see revalidation.py's own docstring.
    if not revalidation.candidate_is_still_valid(db, space_id, candidate, now_final, timezone_name):
        db.commit()
        return AppOpenedSurfaceResult(status="silence", reason="candidate_became_stale")

    # All finalization checks passed — persist BOTH rows in the SAME
    # transaction as this one authoritative commit. Neither helper
    # commits on its own (commit=False), matching the exact
    # commit:bool=True convention 3.H2 already established — there is
    # no path here where one row could durably exist without the
    # other.
    assistant_message = chat_service.record_assistant_message(
        db, space_id, user_id, narration_result.text, commit=False
    )
    history.record_exposure(
        db, space_id, user_id, candidate, "app_opened", now_final, timezone_name, commit=False
    )
    db.commit()
    db.refresh(assistant_message)
    return AppOpenedSurfaceResult(status="surfaced", message=assistant_message)
