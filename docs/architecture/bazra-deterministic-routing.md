# BAZRA Deterministic / Zero-LLM Routing

**This is a standing architecture principle, not an ADR.** Like
`bazra-identity-independence.md` and `bazra-capability-routing.md`, it
locks in no new code decision — the deterministic routes it describes
already existed in `chat/service.py` before Checkpoint 3.9, built
incrementally across Checkpoints 3.3–3.4. This note exists so the
reasoning behind them, and the boundary around extending them, is on
record before the next attempt to add one.

## The two concepts, kept explicitly separate

**A. Zero-LLM execution based on strong structural/workflow evidence.**
The system already knows the operation from trusted, already-persisted
workflow state — not from interpreting the user's words — and can
execute or render it deterministically. The evidence is a concrete
row or flag the code itself created earlier, not a guess about intent.

**B. Zero-LLM natural-language understanding.**
The system infers an operation directly from arbitrary user language
without a model — a regex/keyword classifier standing in for semantic
judgment.

**BAZRA deliberately prefers A and treats B as something to avoid, not
a cheaper alternative to reach for.** A regex/keyword classifier is
exactly the failure mode this project has forensic evidence against
(`write_intent.py`'s own documented false-positive/negative rate), and
"it's cheaper" or "it's usually right" is explicitly not sufficient —
a deterministic route must be safe and unambiguous for that specific
case, not merely inexpensive.

## The three existing zero-LLM routes (`chat/service.py::send_message`)

1. **Clear write-intent decline** (`detect_clear_write_intent`). This
   is the one route that leans on pattern-matching the current
   message, and it is explicitly type B — but it is safe *only because
   its failure direction is safe*: a false positive just declines
   politely (an annoyance, not a corruption), and a false negative
   simply lets the message reach the model (an extra call, not a
   wrong action). No route whose failure could instead cause a wrong
   *action* is permitted to work this way.
2. **Bare confirm ("yes") against a pending `ProposedAction`** →
   `actions_service.confirm_and_execute`. Type A. The safety argument
   is structural: `_classify_narrow_yes_no` only recognizes the
   *entire* trimmed message as one of a small, closed, bilingual
   phrase set — and it is only ever *acted on* deterministically when
   a concrete, already-stored `ProposedAction` row exists to give that
   phrase a specific referent. The phrase match alone is not the
   evidence; the phrase match *plus* the pending row together are.
3. **Bare reject ("no") against a pending `ProposedAction`** →
   `actions_service.reject`. Same evidence structure as (2).

All three are structurally incapable of creating an `AiTrace` row or a
`correlation_id`: `model_router_service.complete()` is the only place
either is produced, and none of these three routes ever calls
`orchestrator_service.generate_reply` (the only caller of `complete()`
for a live turn) at all — confirmed by reading the code, not assumed.

## The boundary: what does NOT qualify, and why

**A bare "yes"/"no" with no pending `ProposedAction` does not qualify**,
even though it is the same phrase-match mechanism as route (2)/(3)
above. The reason is not caution for its own sake: the safety of
routes (2)/(3) comes specifically from the pending row narrowing what
"yes" could mean to one concrete, known referent. Remove that row and
the same word reverts to being genuinely conversational and
context-dependent — it could be agreeing to something several turns
back in ordinary dialogue. `chat/tests/test_chat.py` already has a
test (`test_unrelated_message_after_a_proposal_leaves_it_pending_and_still_confirmable`)
demonstrating this app already cares about exactly this kind of
conversational nuance; overriding it with a deterministic "nothing
pending" reply would be a regression, not an optimization.

Similarly out of bounds: any request whose *recognition* — "the user
wants X" — requires semantic interpretation, even when the underlying
read (Tasks, Calendar, Memory) is itself perfectly deterministic once
recognized. "Show me what matters today" is not a zero-LLM candidate
merely because `context_module.gather_context` is deterministic — the
step that decided *that this sentence means that query* is not.

## Where this leaves the routing decision

The routing decision already lives at the top of
`chat/service.py::send_message`, as a plain, ordered set of early
returns — not a registry, not a classifier, not a new abstraction. A
future genuinely new type-A candidate (a UI-originated structured
command that supplies its own operation explicitly, for example,
rather than free text) would extend this same ordered check, gated on
the same standard: real, already-persisted structural evidence, never
a natural-language guess dressed up as one.

## Correlation — explicitly left open, not decided here

Checkpoint 3.9 does not generate an `AiTrace.correlation_id` for a
zero-LLM turn, and does not fabricate a trace row for one — there is
no model call to correlate, and inventing one would misrepresent what
happened. The turn's own real identifier, `ChatMessage.id`, is what
the deterministic-route log lines use instead (see
`chat/service.py`'s logging at each of the three branches above).

**This is not a permanent rule that correlation belongs only to model
calls.** A future checkpoint may introduce a broader turn/workflow-level
correlation concept spanning both model and non-model operations —
that design is explicitly out of scope here, and this note takes no
position on it either way.
