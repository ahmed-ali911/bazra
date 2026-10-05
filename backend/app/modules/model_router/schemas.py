from dataclasses import dataclass
from typing import Literal

# A fixed, code-level closed set — stored as a plain String column on
# AiTrace, not a native DB enum, matching Task.status's own precedent
# (validated at a code boundary, not requiring an ALTER TYPE to extend).
# Extend this set as real callers are added (Chat in 3.2, Memory
# extraction in 3.4, tool-result continuation in 3.8, the mutation-claim
# verifier in Checkpoint 3.25) rather than accepting an arbitrary string.
ModelCallPurpose = Literal[
    "chat_completion", "memory_extraction", "tool_result_reasoning", "claim_verification",
    "proactive_narration",
]
VALID_PURPOSES: frozenset[str] = frozenset({
    "chat_completion", "memory_extraction", "tool_result_reasoning", "claim_verification",
    "proactive_narration",
})

# Checkpoint 5.1 — a small, provider-neutral, stable failure taxonomy.
# This is the ONLY vocabulary callers outside model_router/service.py
# ever see for a failed complete() call (via ModelRouterError.category,
# propagated through OrchestratorError.category/ClaimVerificationFailed.
# category) — they never need to know Anthropic's own exception classes
# or its documented error-body "type" strings. Deliberately a small,
# stable set rather than one entry per Anthropic SDK exception class —
# see service.py's _classify_failure for the exact, evidence-based
# mapping and its documented precedence.
#
# Retryability (conceptual only — no retry is implemented anywhere as
# of this checkpoint):
#   NON-RETRYABLE:       authentication, billing_or_credits, invalid_request
#   POTENTIALLY TRANSIENT: rate_limited, timeout, connection, provider_unavailable
#   UNKNOWN (depends on cause): model_unavailable, unparseable_response,
#                                unknown_provider_error
ModelFailureCategory = Literal[
    "authentication",
    "billing_or_credits",
    "rate_limited",
    "timeout",
    "connection",
    "provider_unavailable",
    "invalid_request",
    "model_unavailable",
    "unparseable_response",
    "unknown_provider_error",
]

# Checkpoint 5.7 — a provider-neutral PROVIDER identity, introduced only
# now that a second real provider genuinely exists (section 4 of the
# 5.7 brief: "do not create provider identity from model-name string
# heuristics if a cleaner explicit contract is available" — this is
# that explicit contract, not a guess derived from e.g. a model string
# prefix). Deliberately separate from both ModelCallPurpose (WHY) and
# IntelligenceTier (WHAT CAPABILITY) — see complete_with_explicit_provider's
# own docstring in service.py for how this is actually supplied; every
# EXISTING production caller (complete(), unchanged) never supplies
# this explicitly and always resolves to "anthropic", matching AiTrace's
# own pre-existing `provider` column, which has stored this exact
# literal string since Checkpoint 3.1 — no migration needed.
ModelProvider = Literal["anthropic", "google_gemini"]
VALID_PROVIDERS: frozenset[str] = frozenset({"anthropic", "google_gemini"})

# Checkpoint 5.3 — a provider-neutral INTELLIGENCE TIER, a concept
# deliberately separate from both ModelCallPurpose (WHY a call
# happens) and the concrete model string (WHICH model currently
# executes it — see service.py's own _resolve_model/_MODEL_BY_PURPOSE,
# completely unchanged by this checkpoint). Tier describes the
# capability level a call conceptually requires — never a vendor
# product name (no HAIKU/SONNET/OPUS here), so a future mapping from
# tier to model can change freely without callers ever needing to
# change. This checkpoint is the CONTRACT only: nothing in this
# codebase yet makes model selection depend on tier (see
# service.py's own _resolve_tier — called purely for observability/
# future use, never consulted by _resolve_model).
IntelligenceTier = Literal["lightweight", "standard", "powerful"]
VALID_TIERS: frozenset[str] = frozenset({"lightweight", "standard", "powerful"})


@dataclass
class ToolUseBlock:
    """One structured tool-call the model made, if any — Checkpoint 3.3.
    name/input come straight from the provider's own tool_use content
    block; input is NOT re-validated here (that happens at the domain
    boundary, e.g. against TaskCreate, in whichever module owns the
    tool). id (Checkpoint 3.8) is the provider's own tool_use_id — kept
    so a later tool_result continuation can reference exactly this
    call; also reused as an OUTBOUND representation (see
    ToolResultBlock) when a caller replays this exact block back to the
    model as part of the assistant's own prior turn.
    """

    id: str
    name: str
    input: dict


@dataclass
class TextBlock:
    """A plain text content block — Checkpoint 3.8. Used only on the
    OUTBOUND side, to replay the model's own accompanying text as part
    of a reconstructed assistant turn in a tool-result continuation;
    the inbound/response side still just uses ModelResponse.text."""

    text: str


@dataclass
class ToolResultBlock:
    """The OUTBOUND counterpart to a ToolUseBlock — Checkpoint 3.8:
    supplies the deterministically-executed tool's result back to the
    model in a continuation call. content is a plain string (Anthropic
    supports a bare string as tool_result content) — callers serialize
    whatever structured payload they have (e.g. a normalized domain
    result) into a JSON string themselves; this module never inspects
    or re-validates that content, matching ToolUseBlock.input's own
    "not re-validated here" precedent.
    """

    tool_use_id: str
    content: str
    is_error: bool = False


# A message's content may be a plain string (unchanged since 3.2) or,
# as of 3.8, a list of the blocks above — used to replay a prior
# assistant tool_use turn and to supply a tool_result. Anthropic's own
# wire-format dict shapes (the literal "type": "tool_use" keys etc.)
# are constructed ONLY inside model_router/service.py's
# _serialize_content — nothing outside this module ever needs to know
# that shape.
MessageContent = str | list["TextBlock | ToolUseBlock | ToolResultBlock"]


@dataclass
class ModelResponse:
    text: str | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    tool_uses: list[ToolUseBlock]
    correlation_id: str
    stop_reason: str | None = None
    # Checkpoint 5.3 — the resolved IntelligenceTier for this call,
    # exposed the same way stop_reason already is: an in-memory,
    # never-persisted observability/test seam (see model_router's own
    # AiTrace — no new column; "standard" default here only matters for
    # the one pre-5.3 direct ModelResponse(...) test construction that
    # predates this field and doesn't care about it; complete() itself
    # always passes an explicit, resolved value).
    tier: IntelligenceTier = "standard"
    # Checkpoint 5.7 — the provider that actually served this call.
    # Defaults to "anthropic" for the same reason `tier` defaults to
    # "standard" above: every production caller today only ever gets an
    # Anthropic-served response, and a handful of pre-5.7 tests
    # construct ModelResponse(...) directly without caring about this
    # field. complete() always sets this explicitly (to "anthropic",
    # unchanged); complete_with_explicit_provider (new in 5.7) sets it
    # to whichever provider was explicitly requested.
    provider: ModelProvider = "anthropic"
