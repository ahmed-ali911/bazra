"""Checkpoint 4.5d — Proactive Narration + Verification + Deterministic
Fallback.

APP_OPENED V1 has three distinct responsibilities, deliberately owned
by three different modules:

    1. WHAT deserves attention?      attention/service.py + scoring.py (4.2/4.3)
    2. WHETHER BAZRA should speak?   attention/app_opened.py (4.5c)
    3. HOW should BAZRA say it?      THIS module

This module owns ONLY #3. It receives exactly one already-selected
AttentionCandidate (the 4.5c Winner Invariant's own output — never a
list, never the raw signal pool) and turns it into one short, natural
opening line. It never re-runs or second-guesses selection: it does
not call generate_signals, load_suppression_states, or
rank_for_surface, and it has no `db: Session` parameter at all — it is
structurally incapable of querying anything, let alone writing
anything.

Privacy minimization: only three scalar facts ever leave this
boundary toward the model — signal_type, title, and priority (the last
omitted entirely for non-task signals) — see _extract_candidate_facts.
No score, reason_codes, snapshot, source_id, relevant_timestamp,
Context Assembly, Memory, or chat history is ever sent. There is
nothing here resembling gather_context; the input surface is narrower
by construction, not by filtering a larger payload down after the
fact.

Truthfulness, two layers deep:
  - Every MODEL-authored candidate narration is passed through the
    existing, unmodified orchestrator_service.verify_no_mutation_claim
    (Checkpoint 3.25) before it may ever be returned as `source="model"`
    — the exact same fail-closed policy chat_service's own
    `_candidate_reply_is_safe_to_show` already established (a verifier
    call that raises ClaimVerificationFailed is treated exactly like an
    explicit unsafe certification, never retried, never "repaired").
  - IMPORTANT LIMITATION, stated here because 4.5d's own brief requires
    it be documented rather than silently assumed: verify_no_mutation_claim
    proves only one narrow thing — that the candidate text does not
    claim, in BAZRA's own voice, that a mutation was already completed.
    It is NOT a general factual-grounding verifier; it does not
    independently re-check that the narrated title/signal_type
    actually match the candidate, that no other fact was hallucinated,
    or that the invitation-style wording avoided an action-confirmation
    shape (that is enforced only by the prompt instructions in
    orchestrator_service._PROACTIVE_NARRATION_INSTRUCTIONS, which are
    prompt-level guidance, not an independently-verified guarantee).
    V1's mitigation is structural, not a second verifier: the model is
    handed so little (three scalar facts, no tools) that there is
    almost nothing left for it to hallucinate ABOUT — see
    orchestrator_service.generate_app_opened_narration_text's own
    docstring for the exact input contract.

Any failure before a model narration is fully certified — a provider
error, an empty/malformed result, an explicit unsafe verifier
certification, or a verifier contract failure — discards the model
text completely (never returned, never repaired, never retried with a
second call or a different provider) and returns a DETERMINISTIC,
application-authored fallback instead, built ONLY from the same
authoritative candidate facts already extracted — never from any
model output, not even a substring of it. Because the fallback is not
model-authored, it is never passed through verify_no_mutation_claim
(there is nothing to verify — the application wrote every word of it).

NOT implemented here (later checkpoints own these): persisting
anything (ChatMessage/AttentionExposure), revalidating that the
candidate's underlying source is still true/unsuppressed right before
actual surfacing (candidate_became_stale, user_message_won_race — both
4.5e), any API endpoint, and any frontend trigger.
"""

from dataclasses import dataclass

from app.modules.attention.schemas import AttentionCandidate, SignalType
from app.modules.narration.schemas import NarrationResult
from app.modules.orchestrator import service as orchestrator_service

#: One fixed, minimal template per currently-supported APP_OPENED
#: signal type (attention/schemas.py's own locked v1 SIGNAL_TYPE_ORDER)
#: — exhaustive by construction: _build_fallback_text raises rather
#: than silently falling through if a signal_type ever appears here
#: that this table doesn't recognize, so adding a 6th signal type
#: elsewhere without updating this table fails loudly instead of
#: producing a blank/wrong fallback.
_FALLBACK_TEMPLATES: dict[SignalType, str] = {
    "TASK_OVERDUE": "عندك مهمة متأخرة: {title}.",
    "TASK_DUE_TODAY": "عندك مهمة مستحقة النهارده: {title}.",
    "TASK_DUE_SOON": "عندك مهمة ميعادها قرب: {title}.",
    "EVENT_UPCOMING": "عندك موعد قريب: {title}.",
    "INBOX_NEEDS_ATTENTION": "عندك حاجة في الـ Inbox محتاجة انتباه: {title}.",
}

#: Appended only when the candidate's OWN authoritative priority is
#: exactly "high" — never inferred, never added for "normal"/"low"/
#: None, since those carry no particular urgency worth calling out.
_HIGH_PRIORITY_SUFFIX = " (أولوية عالية)"


@dataclass(frozen=True)
class _CandidateFacts:
    """The exact, narrow set of fields this module ever reads off an
    AttentionCandidate — everything else on it (score, reason_codes,
    suppression, the Signal's own source_id/relevant_timestamp/
    measurement_seconds/snapshot) is never touched past this point."""

    signal_type: SignalType
    title: str
    priority: str | None


def _extract_candidate_facts(candidate: AttentionCandidate) -> _CandidateFacts:
    signal = candidate.signal
    return _CandidateFacts(signal_type=signal.signal_type, title=signal.title, priority=signal.priority)


def _build_fallback_text(facts: _CandidateFacts) -> str:
    template = _FALLBACK_TEMPLATES.get(facts.signal_type)
    if template is None:
        raise ValueError(f"Unsupported signal_type for narration fallback: {facts.signal_type!r}")
    text = template.format(title=facts.title)
    if facts.priority == "high":
        text += _HIGH_PRIORITY_SUFFIX
    return text


def generate_app_opened_narration(candidate: AttentionCandidate) -> NarrationResult:
    """The single public entry point for this checkpoint. Pure with
    respect to application/domain state (no `db`, no writes) — the only
    real side effects are the (at most two) outbound provider calls
    this makes through orchestrator_service.

    Call budget, exactly: a successful model path makes exactly one
    narration call plus one verification call (two total). A narration
    provider failure makes exactly one call attempt and zero
    verification calls. A verifier block or verifier failure makes
    exactly one narration call plus one verification call (two total).
    There is never a second narration call, a retry, a "repair" call,
    or a different-provider fallback attempt — any failure path falls
    straight through to the deterministic, non-model fallback below.
    """
    facts = _extract_candidate_facts(candidate)
    # Computed FIRST, from candidate facts alone — before any model call
    # is even attempted, so it can never be influenced by, or derived
    # from, model output on any path below.
    fallback_text = _build_fallback_text(facts)

    try:
        model_text = orchestrator_service.generate_app_opened_narration_text(
            signal_type=facts.signal_type, title=facts.title, priority=facts.priority
        )
    except orchestrator_service.OrchestratorError:
        return NarrationResult(text=fallback_text, source="fallback", fallback_reason="narration_failed")

    if not model_text or not model_text.strip():
        return NarrationResult(text=fallback_text, source="fallback", fallback_reason="narration_empty")

    try:
        # verify_no_mutation_claim returns claims_bazra_mutation_completed
        # LITERALLY (True means the text claims a completed mutation —
        # i.e. UNSAFE), never pre-inverted to "is safe" — the same
        # fail-closed inversion chat_service's own
        # _candidate_reply_is_safe_to_show already performs is repeated
        # here, not reused directly, since that helper is chat/service.py-
        # private.
        claims_mutation_completed = orchestrator_service.verify_no_mutation_claim(model_text)
    except orchestrator_service.ClaimVerificationFailed:
        return NarrationResult(text=fallback_text, source="fallback", fallback_reason="verification_failed")

    if claims_mutation_completed:
        return NarrationResult(text=fallback_text, source="fallback", fallback_reason="verification_blocked")

    return NarrationResult(text=model_text, source="model")
