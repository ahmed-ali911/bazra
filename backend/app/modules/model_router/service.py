import logging
import secrets
import time
from decimal import Decimal

import anthropic
from anthropic import Anthropic
from anthropic.types import Message
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import SessionLocal
from app.modules.model_router import gemini_service
from app.modules.model_router.models import AiTrace
from app.modules.model_router.schemas import (
    VALID_PROVIDERS,
    VALID_PURPOSES,
    VALID_TIERS,
    IntelligenceTier,
    ModelCallPurpose,
    ModelFailureCategory,
    ModelProvider,
    ModelResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

# Module-level and swappable — deliberately NOT a hardcoded import used
# inline, so tests can redirect trace writes to the isolated bazra_test
# database (see conftest.py's autouse fixture in this module's own
# tests). In production this stays the app's real, existing sessionmaker.
_trace_session_factory: sessionmaker = SessionLocal

# The system prompt's own guidance is to default to the latest and most
# capable Claude model when building AI applications — used here as the
# Model Router's default; trivially overridable once a real Orchestrator
# (Checkpoint 3.2) has an actual reason to choose otherwise per-purpose.
_DEFAULT_MODEL = "claude-sonnet-5"

# Checkpoint 3.25 — the smallest explicit purpose->model override: a
# plain dict, not a general multi-tier routing system. Every purpose
# not listed here keeps using _DEFAULT_MODEL, unchanged from before this
# checkpoint. claim_verification is the one purpose that deliberately
# wants a cheaper, faster model — see chat_service's own claim verifier,
# the only caller of this purpose today. Confirmed via a real,
# disposable call (Checkpoint 3.25 inspection) that the bare alias
# below resolves server-side to the exact same snapshot already keyed
# in _COST_PER_MILLION_TOKENS_USD, so cost estimation keeps working
# with no new table entry needed.
_MODEL_BY_PURPOSE: dict[str, str] = {
    "claim_verification": "claude-haiku-4-5",
}


def _resolve_model(purpose: str) -> str:
    return _MODEL_BY_PURPOSE.get(purpose, _DEFAULT_MODEL)


# Checkpoint 5.3 — the centralized, explicit, auditable purpose->tier
# policy: a SEPARATE table from _MODEL_BY_PURPOSE above, deliberately —
# tier (capability conceptually required) and model (which concrete
# product executes it) are two different questions, and this checkpoint
# introduces the contract for the first without letting it influence
# the second (see _resolve_tier's own docstring and complete()'s own
# docstring on tier/model independence).
#
# Every value here was chosen by inspecting each purpose's OWN real,
# current task shape (not the illustrative examples in this
# checkpoint's own brief, which explicitly warns against blind reuse):
#   - claim_verification: a single forced boolean tool call over just
#     the candidate text, no history/context/tools beyond one strict
#     schema — the narrowest, most bounded task in this codebase.
#   - proactive_narration: exactly three scalar facts in (signal_type,
#     title, priority), one short natural-language opening line out, no
#     tools — see generate_app_opened_narration_text's own docstring:
#     "there is no larger object available here to accidentally leak
#     more out of." Equally narrow/bounded as claim_verification, by
#     the same reasoning, even though its CURRENT concrete model
#     (unchanged by this checkpoint) differs.
#   - chat_completion: the broadest task in this codebase — full
#     history, full Context Assembly, 9 offered tools, general-purpose
#     reasoning and correct tool selection.
#   - tool_result_reasoning: grounded judgment over one fetched factual
#     result (e.g. "do I need a jacket, given this weather") — genuine
#     reasoning, not a narrow structured classification.
#   - memory_extraction: declared in VALID_PURPOSES but has ZERO
#     production call sites (confirmed by repo-wide grep during this
#     checkpoint's own discovery) — defaulted to the conservative,
#     capability-preserving "standard" rather than guessed at, since
#     its real future shape is unknown.
_TIER_BY_PURPOSE: dict[str, IntelligenceTier] = {
    "claim_verification": "lightweight",
    "proactive_narration": "lightweight",
    "chat_completion": "standard",
    "tool_result_reasoning": "standard",
    "memory_extraction": "standard",
}


def _resolve_tier(purpose: str, tier: IntelligenceTier | None) -> IntelligenceTier:
    """An explicit, caller-supplied tier always wins (no production
    caller supplies one today — see complete()'s own docstring for
    why) — this is the "explicit tier at each caller" half of the 5.3
    contract, available but not currently exercised. Otherwise resolved
    from the centralized _TIER_BY_PURPOSE table above.

    The "standard" fallback for a purpose NOT in that table is
    defense-in-depth only, never expected to actually execute in
    production: complete()'s own purpose gate (VALID_PURPOSES) already
    rejects any purpose this table doesn't cover before this function
    is ever reached, and a dedicated test asserts
    _TIER_BY_PURPOSE's own keys exactly equal VALID_PURPOSES. Per the
    5.3 brief's own explicit requirement: an unresolvable purpose must
    never silently become "lightweight" merely to save cost — fail
    safe toward capability, not toward cost.
    """
    if tier is not None:
        return tier
    return _TIER_BY_PURPOSE.get(purpose, "standard")


_MAX_TOKENS = 1024

# USD per 1,000,000 tokens — a static, hand-maintained table, never a
# live pricing lookup. Sourced from Anthropic's published pricing at
# implementation time (2026-09-23), not guessed from training data,
# which may be stale. Update by hand when pricing changes or a new
# model is added — this is an ESTIMATE, never billing-accurate.
_COST_PER_MILLION_TOKENS_USD: dict[str, dict[str, Decimal]] = {
    "claude-sonnet-5": {"input": Decimal("2.00"), "output": Decimal("10.00")},
    "claude-haiku-4-5": {"input": Decimal("1.00"), "output": Decimal("5.00")},
}


class ModelRouterError(Exception):
    """Raised after the failure trace has already been (best-effort)
    recorded — see complete()'s docstring for the exact guarantee.

    category (Checkpoint 5.1) is the normalized, provider-neutral
    ModelFailureCategory for this failure — the exact same value
    already written to AiTrace.error_summary for the same row (see
    _classify_failure). Callers that need to distinguish failure kinds
    (Chat's own degradation wording, the claim verifier's observability
    distinction) read this attribute rather than parsing str(exc) or
    importing anthropic's own exception classes — this module remains
    the only place that knows those.
    """

    def __init__(self, category: ModelFailureCategory):
        self.category = category
        super().__init__(category)


def _get_client() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def _serialize_block(block: TextBlock | ToolUseBlock | ToolResultBlock) -> dict:
    """The ONLY place in the codebase that knows Anthropic's literal
    wire-format content-block keys (Checkpoint 3.8) — callers outside
    this module construct/receive TextBlock/ToolUseBlock/ToolResultBlock
    only, never a raw dict with a "type" key."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        result: dict = {"type": "tool_result", "tool_use_id": block.tool_use_id, "content": block.content}
        if block.is_error:
            result["is_error"] = True
        return result
    raise TypeError(f"Unknown content block type: {type(block)!r}")


def _serialize_messages(messages: list[dict]) -> list[dict]:
    """A message's content is either a plain string (unchanged since
    3.2 — passed through untouched) or, as of 3.8, a list of content-
    block dataclasses (used to replay an assistant tool_use turn and to
    supply a tool_result) — converted to Anthropic's own dict shape
    only here, immediately before the SDK call."""
    serialized = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            serialized.append(message)
        else:
            serialized.append({"role": message["role"], "content": [_serialize_block(block) for block in content]})
    return serialized


def _call_anthropic(
    model: str,
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    cacheable_system_prefix: str | None = None,
) -> Message:
    """The only function in this module that talks to the Anthropic SDK
    directly — everything else in complete() is boundary/bookkeeping
    logic around this one call. system is Anthropic's own separate
    top-level parameter, not a role inside `messages` — the Messages API
    has no "system" message role. tools is additive (Checkpoint 3.3) —
    omitted entirely when not passed, so existing callers see no change
    in the request shape at all.

    tool_choice (Checkpoint 3.23) is additive the same way — a plain
    dict forwarded to the SDK verbatim (e.g. {"type": "any",
    "disable_parallel_tool_use": True}), never interpreted or
    constructed here. This module stays generic and BAZRA-action-
    agnostic: the POLICY of which tool_choice shape to use for which
    call purpose lives entirely in Orchestrator (see generate_reply's
    own _PRIMARY_CHAT_TOOL_CHOICE), not here. Omitted entirely when
    not passed, so a caller that never passes it (every purpose except
    the primary chat call) sees byte-for-byte the same request shape
    as before this checkpoint.

    cacheable_system_prefix (Checkpoint 5.7J) is additive and OPTIONAL —
    omitted (every caller before this checkpoint, and every purpose
    except chat_completion's own default-provider path) means
    `system`/`tools` are sent EXACTLY as before: `system` as a bare
    string, `tools` as the plain list with no `cache_control` key
    anywhere. Only when explicitly supplied does this function instead:
    (1) split `system` into TWO Anthropic content blocks — the supplied
    prefix first, marked with `cache_control: {"type": "ephemeral"}`
    (the SDK's own stable, non-beta `CacheControlEphemeralParam` —
    confirmed via direct SDK introspection, not a deprecated beta
    feature), then the caller's own `system` string (if any) as a
    second, UNMARKED block — and (2) mark the LAST element of `tools`
    (on a COPY, never mutating the caller's own list/dicts — tools may
    be a shared module-level constant like chat_service._TOOLS_OFFERED)
    with the same cache_control key, so the ENTIRE tools array becomes
    one cached prefix (Anthropic's own documented semantics: a
    breakpoint caches everything from the start of its own parameter UP
    TO AND INCLUDING the marked block — tools and system are separate
    top-level parameters, so each needs its own breakpoint to both be
    cached). This changes ONLY caching metadata and request SHAPE — the
    semantic text content sent is byte-for-byte identical either way
    (see test_model_router.py's own structural-equivalence tests).
    """
    kwargs = {"model": model, "max_tokens": _MAX_TOKENS, "messages": _serialize_messages(messages)}
    if cacheable_system_prefix is not None:
        system_blocks = [{"type": "text", "text": cacheable_system_prefix, "cache_control": {"type": "ephemeral"}}]
        if system is not None:
            system_blocks.append({"type": "text", "text": system})
        kwargs["system"] = system_blocks
    elif system is not None:
        kwargs["system"] = system
    if tools is not None:
        if cacheable_system_prefix is not None and tools:
            tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    return _get_client().messages.create(**kwargs)


def _extract_response_parts(message: Message) -> tuple[str | None, list[ToolUseBlock]]:
    """Replaces the old _extract_text (Checkpoint 3.2) now that a
    response can legitimately contain a tool_use block instead of, or
    alongside, text — a pure tool-call response with no text at all is
    an expected, valid shape when tools were offered, not an error.
    Still raises if there is NEITHER text NOR a tool use at all, which
    remains a genuinely malformed/empty response — the same failure
    boundary as before, just no longer over-triggering on the new,
    valid tool-only case.
    """
    text_parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    tool_uses = [
        ToolUseBlock(id=block.id, name=block.name, input=block.input)
        for block in message.content
        if getattr(block, "type", None) == "tool_use"
    ]
    text = "".join(text_parts) if text_parts else None
    if text is None and not tool_uses:
        raise ValueError("No text content or tool use in provider response")
    return text, tool_uses


# Checkpoint 5.7J — Anthropic's own documented prompt-caching price
# multipliers, relative to the SAME model's base input price already in
# _COST_PER_MILLION_TOKENS_USD above (verified live against
# platform.claude.com/docs/en/build-with-claude/prompt-caching at
# implementation time, 2026-10-07 — not guessed from training data).
# 5-minute cache writes: 1.25x base input. 1-hour cache writes: 2x base
# input (BAZRA never requests the 1h TTL — see _call_anthropic's own
# cache_control, which omits `ttl` and so gets the SDK's own "5m"
# default — this constant is recorded for completeness/documentation
# only, never read by any code path). Cache reads: 0.1x base input,
# with two DOCUMENTED per-model exceptions in Anthropic's own pricing
# page ("Claude Opus 5.5"/"Claude Sonnet 5.5" read at 0.05x, "Claude
# Fable 5.1"/"Claude Mythos 5.1" at 0.025x) — neither "claude-sonnet-5"
# nor "claude-haiku-4-5" (this table's own two keys) is one of those
# four named exception models, so the general 0.1x multiplier is
# applied to both; revisit this constant by hand if Anthropic's own
# pricing page ever lists either of BAZRA's actual model strings among
# the exceptions.
_CACHE_WRITE_5M_MULTIPLIER = Decimal("1.25")
_CACHE_READ_MULTIPLIER = Decimal("0.1")


def _estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> Decimal | None:
    """cache_creation_input_tokens/cache_read_input_tokens both default
    to 0 — every pre-5.7J caller omits them, producing the exact same
    arithmetic as before this checkpoint (adding zero changes nothing).
    When non-zero (an Anthropic call that used cacheable_system_prefix),
    each of the four token categories is priced at its OWN documented
    rate — never folded into `prompt_tokens` at the base rate, which
    would silently misstate cost in both directions (overstating a
    cache-read turn's cost, understating a cache-write turn's cost).
    `prompt_tokens` here is the caller's own base/non-cached count only
    (Anthropic's own `usage.input_tokens` — confirmed via official docs
    to EXCLUDE both cache categories, summed separately) — see
    complete()'s own docstring for what gets persisted to AiTrace.
    """
    rates = _COST_PER_MILLION_TOKENS_USD.get(model)
    if rates is None:
        return None
    base_input_cost = Decimal(prompt_tokens) * rates["input"]
    cache_write_cost = Decimal(cache_creation_input_tokens) * rates["input"] * _CACHE_WRITE_5M_MULTIPLIER
    cache_read_cost = Decimal(cache_read_input_tokens) * rates["input"] * _CACHE_READ_MULTIPLIER
    output_cost = Decimal(completion_tokens) * rates["output"]
    return (base_input_cost + cache_write_cost + cache_read_cost + output_cost) / Decimal(1_000_000)


def _safe_estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> Decimal | None:
    """A bad rate-table lookup is a bug in bookkeeping, not a failure of
    the model call — it must not prevent a successful call from
    returning to its caller or from being traced at all. Logs the
    failure (never prompt/response/key material) and returns None, so
    the trace still records with estimated_cost_usd=NULL rather than
    propagating.
    """
    try:
        return _estimate_cost(model, prompt_tokens, completion_tokens, cache_creation_input_tokens, cache_read_input_tokens)
    except Exception:
        logger.exception("model_router: cost estimation failed for model=%s", model)
        return None


def _classify_failure(exc: Exception) -> ModelFailureCategory:
    """Normalized, provider-neutral failure classification (Checkpoint
    5.1) — the ONLY place in the codebase that needs to know Anthropic's
    own exception classes or its documented error-body "type" strings.
    Both ModelRouterError.category and AiTrace.error_summary are always
    this function's return value, verbatim — never a separately
    "summarized" string, so the two stay identical by construction.

    Precedence, most to least specific (Checkpoint 5.1's own explicit
    requirement that a broad SDK parent class must never swallow a more
    specific failure):

    1. anthropic.APIStatusError.type — Anthropic's own documented,
       stable error-body field (confirmed by live SDK inspection:
       anthropic._exceptions.APIStatusError.__init__ populates `.type`
       straight from the parsed JSON body's `error.type`, and
       anthropic.types.shared.error_type.ErrorType is a closed set of 9
       literal strings, including "billing_error" — the exact stable,
       machine-readable signal needed to classify the real credit-
       exhaustion incident reliably, WITHOUT brittle free-text
       matching). Checked first because it is strictly more specific
       than the exception's own Python class: a billing failure and a
       plain invalid request can both arrive as the same
       BadRequestError (HTTP 400) class, and only `.type` tells them
       apart. NOT reproduced against a real failing call (no real
       provider calls were made for this checkpoint, per its own
       constraints) — this mapping rests on the SDK's own documented
       contract, not an observed response body.
    2. The exception's own Python class/HTTP status code, for the rarer
       case `.type` is absent or not one of the 9 known literals (e.g.
       a malformed error body, or a future Anthropic error type this
       set doesn't recognize yet) — most specific subclass checked
       first so e.g. AuthenticationError is never swallowed by checking
       the common APIStatusError parent before it.
    3. unknown_provider_error — the final, honest fallback for
       anything not covered above; never guessed into a more specific,
       falsely-precise bucket (the same "prefer honest over falsely
       precise" instruction given for billing specifically, applied
       here as the general policy).
    """
    if isinstance(exc, RuntimeError) and "ANTHROPIC_API_KEY" in str(exc):
        return "authentication"

    if isinstance(exc, anthropic.APIStatusError):
        error_type = exc.type
        if error_type == "billing_error":
            return "billing_or_credits"
        if error_type in ("authentication_error", "permission_error"):
            return "authentication"
        if error_type == "not_found_error":
            return "model_unavailable"
        if error_type == "rate_limit_error":
            return "rate_limited"
        if error_type == "timeout_error":
            return "timeout"
        if error_type in ("overloaded_error", "api_error"):
            return "provider_unavailable"
        if error_type == "invalid_request_error":
            return "invalid_request"

        # `.type` absent or not one of the 9 known literals — fall back
        # to the exception's own class, most specific first.
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return "authentication"
        if isinstance(exc, anthropic.NotFoundError):
            return "model_unavailable"
        if isinstance(exc, anthropic.RateLimitError):
            return "rate_limited"
        if isinstance(exc, anthropic.DeadlineExceededError):
            return "timeout"
        if isinstance(
            exc, (anthropic.OverloadedError, anthropic.ServiceUnavailableError, anthropic.InternalServerError)
        ):
            return "provider_unavailable"
        if isinstance(
            exc, (anthropic.BadRequestError, anthropic.RequestTooLargeError, anthropic.UnprocessableEntityError, anthropic.ConflictError)
        ):
            return "invalid_request"
        return "unknown_provider_error"

    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.APIConnectionError):
        return "connection"
    if isinstance(exc, ValueError):
        return "unparseable_response"
    return "unknown_provider_error"


def _record_trace(**fields) -> None:
    """Writes exactly one AiTrace row on its OWN independent session —
    never the caller's request-scoped session — and commits it
    immediately. This is what makes the failure trace durable even if
    the broader Chat/Orchestrator request that triggered this call later
    rolls back its own transaction for an unrelated reason: this commit
    already happened, on a connection that rollback can't reach.
    """
    session = _trace_session_factory()
    try:
        session.add(AiTrace(**fields))
        session.commit()
    finally:
        session.close()


def _safe_record_trace(**fields) -> bool:
    """Writes exactly one AiTrace row (via _record_trace's own
    independent session). Never raises — a failure to persist the trace
    itself must not be conflated with a failure of the model call, and
    must never cascade into a second failing attempt to record it from
    inside an except block. Returns whether the write succeeded;
    complete() logs but does not otherwise act on a False.
    """
    try:
        _record_trace(**fields)
        return True
    except Exception:
        logger.exception(
            "model_router: failed to persist AiTrace (purpose=%s, status=%s)",
            fields.get("purpose"),
            fields.get("status"),
        )
        return False


def complete(
    purpose: ModelCallPurpose,
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
    correlation_id: str | None = None,
    tool_choice: dict | None = None,
    tier: IntelligenceTier | None = None,
    cacheable_system_prefix: str | None = None,
) -> ModelResponse:
    """The only function other modules call to reach a model provider.

    The model itself is resolved from `purpose` (Checkpoint 3.25) —
    _resolve_model looks up `_MODEL_BY_PURPOSE`, a small explicit
    override table, falling back to `_DEFAULT_MODEL` for every purpose
    not listed there (every purpose that existed before 3.25). Callers
    never pass a model literal — this keeps model selection a Model
    Router concern, not something scattered through Chat/Orchestrator.

    system is an additive, backward-compatible parameter (Checkpoint
    3.2) — Anthropic's Messages API takes system instructions as their
    own top-level parameter, not a role inside `messages`. Existing
    callers that never pass it are unaffected.

    tools is additive (Checkpoint 3.3) — when provided, the model may
    respond with a tool_use block instead of, or alongside, text; when
    omitted, behavior is byte-for-byte identical to before this
    checkpoint. ModelResponse.text may be None only when tools were
    offered and the model chose to call one with no accompanying text.

    tool_choice (Checkpoint 3.23) is additive and purpose-agnostic —
    forwarded to _call_anthropic verbatim, never inspected or built
    here. Omitted (every purpose except the primary chat call today)
    means byte-for-byte the same request shape as before this
    checkpoint; this module has no idea what "any" or
    disable_parallel_tool_use mean, and no BAZRA action name ever
    appears here — see _call_anthropic's own docstring.

    tier (Checkpoint 5.3) is the provider-neutral INTELLIGENCE TIER
    contract — separate from both `purpose` (why) and the concrete
    model `_resolve_model` picks (which, currently, this parameter
    NEVER influences — see _resolve_tier's own docstring). Optional:
    every production caller today omits it, relying on the centralized
    `_TIER_BY_PURPOSE` default for its own purpose; an explicit value
    is accepted (and wins over that default) for a future caller that
    needs to diverge, or for direct testing of the contract itself
    (e.g. proving "powerful" is a valid value even though it has zero
    production callers today). The RESOLVED tier is exposed on the
    returned ModelResponse.tier for observability/tests — never
    persisted to AiTrace (no migration in this checkpoint) and never
    surfaced in any Chat-facing API response.

    correlation_id (Checkpoint 3.8) is an opaque grouping identifier for
    AiTrace rows that belong to the same logical model workflow — no
    user/prompt/tool content, no timestamp/semantic encoding, generated
    server-side via secrets.token_hex, the same primitive already used
    for session tokens (auth/service.py). When omitted (every ordinary,
    single-call turn), one is generated fresh here. A caller reusing an
    earlier call's OWN ModelResponse.correlation_id (the only real
    consumer today: a tool_result_reasoning continuation reusing its
    initiating chat_completion's id) is what links two AiTrace rows as
    one workflow — this is an explicit input, never hidden/thread-local
    state, and is the ONLY thing "correlation" means here: no span
    trees, no parent/child relationships, no distributed tracing.

    Guarantees, stated explicitly rather than assumed:
    - The provider call failing (auth, network, rate limit, missing key)
      raises ModelRouterError and records at most one error trace.
    - The provider call succeeding but the response failing to parse
      raises ModelRouterError and records at most one error trace —
      never a success trace followed by a separate error trace for the
      same single attempt. If token usage was readable before the
      failure, it (and cost estimated from it) is preserved in that
      error trace rather than discarded.
    - The provider call and parsing both succeeding always returns a
      valid ModelResponse — even if trace persistence itself fails
      (e.g. the database is unavailable) or cost estimation fails (e.g.
      a bad rate-table lookup). Both are logged, never raised, and
      never allowed to mask a result the caller actually has.
    - Tracing is therefore unconditional with respect to the MODEL
      CALL's own outcome, but not with respect to independent tracing-
      infrastructure failures — that is a real, stated boundary, not an
      oversold guarantee.

    cacheable_system_prefix (Checkpoint 5.7J) is additive and OPTIONAL,
    forwarded to _call_anthropic verbatim — see that function's own
    docstring for the exact request-shape change it causes. Omitted
    (every caller before this checkpoint) means byte-for-byte the same
    request as before. AiTrace/cost-accounting note: Anthropic's own
    `usage.input_tokens` EXCLUDES both `cache_creation_input_tokens` and
    `cache_read_input_tokens` (confirmed via official docs: `total_input
    _tokens = cache_read_input_tokens + cache_creation_input_tokens +
    input_tokens`) — this function therefore persists the SUM of all
    three to AiTrace's existing `prompt_tokens` column (a meaningful
    "how much input was processed" total, not a silent undercount),
    while the per-category BREAKDOWN is exposed only on the returned
    ModelResponse (cache_creation_input_tokens/cache_read_input_tokens
    — same "observability only, never persisted" treatment as `tier`/
    `stop_reason`). Durably storing the breakdown itself would need a
    schema migration (two new nullable integer columns) — not performed
    in this checkpoint; see this checkpoint's own required-output report
    for the exact minimum schema change this would require.
    """
    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Unknown purpose: {purpose!r}")
    if tier is not None and tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier: {tier!r}")

    model = _resolve_model(purpose)
    resolved_tier = _resolve_tier(purpose, tier)
    resolved_correlation_id = correlation_id or secrets.token_hex(16)
    start = time.monotonic()

    # Boundary 1: the provider call itself. A failure here means no
    # usable response exists at all — the only case treated as "the
    # call failed" with no possibility of partial data.
    try:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        raw = _call_anthropic(
            model, messages, system=system, tools=tools, tool_choice=tool_choice,
            cacheable_system_prefix=cacheable_system_prefix,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        category = _classify_failure(exc)
        _safe_record_trace(
            provider="anthropic",
            model=model,
            purpose=purpose,
            status="error",
            latency_ms=latency_ms,
            prompt_tokens=None,
            completion_tokens=None,
            estimated_cost_usd=None,
            error_summary=category,
            correlation_id=resolved_correlation_id,
        )
        raise ModelRouterError(category) from exc

    latency_ms = int((time.monotonic() - start) * 1000)

    # Boundary 2: parsing the response. Separate from boundary 1 on
    # purpose — the provider call already succeeded here, so a parsing
    # failure must not be treated as "also record a success," since no
    # success trace has been written yet at this point.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    try:
        prompt_tokens = raw.usage.input_tokens
        completion_tokens = raw.usage.output_tokens
        # Checkpoint 5.7J — both default to 0 via getattr: every
        # pre-caching response (real or test-fake) simply lacks these
        # attributes, and 0 is the correct, honest value when no
        # caching was requested, not a guess.
        cache_creation_input_tokens = getattr(raw.usage, "cache_creation_input_tokens", None) or 0
        cache_read_input_tokens = getattr(raw.usage, "cache_read_input_tokens", None) or 0
        text, tool_uses = _extract_response_parts(raw)
    except Exception as exc:
        # prompt_tokens/completion_tokens survive from above even if the
        # failure happened on the _extract_text line — real,
        # already-incurred token/cost data is preserved here rather than
        # discarded, when it was actually available.
        total_prompt_tokens = (
            prompt_tokens + cache_creation_input_tokens + cache_read_input_tokens
            if prompt_tokens is not None else None
        )
        estimated_cost_usd = (
            _safe_estimate_cost(model, prompt_tokens, completion_tokens, cache_creation_input_tokens, cache_read_input_tokens)
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
        category = _classify_failure(exc)
        _safe_record_trace(
            provider="anthropic",
            model=model,
            purpose=purpose,
            status="error",
            latency_ms=latency_ms,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost_usd,
            error_summary=category,
            correlation_id=resolved_correlation_id,
        )
        raise ModelRouterError(category) from exc

    # Boundary 3: the call succeeded and parsed cleanly. Cost estimation
    # and trace persistence each get their own failure boundary — if
    # either fails, the caller still gets their ModelResponse.
    #
    # Checkpoint 5.7J — total_prompt_tokens (= base + cache-write +
    # cache-read) is what's persisted to AiTrace's existing
    # `prompt_tokens` column (see this function's own docstring for why
    # this is a meaningful total, not a silent lie); estimated_cost_usd
    # is computed from the three categories at their own correct rates,
    # never a flat rate applied to the total.
    total_prompt_tokens = prompt_tokens + cache_creation_input_tokens + cache_read_input_tokens
    estimated_cost_usd = _safe_estimate_cost(model, prompt_tokens, completion_tokens, cache_creation_input_tokens, cache_read_input_tokens)
    if not _safe_record_trace(
        provider="anthropic",
        model=model,
        purpose=purpose,
        status="success",
        latency_ms=latency_ms,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
        error_summary=None,
        correlation_id=resolved_correlation_id,
    ):
        logger.error("model_router: trace persistence failed for a successful call (purpose=%s)", purpose)

    return ModelResponse(
        text=text,
        model=model,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=completion_tokens,
        tool_uses=tool_uses,
        correlation_id=resolved_correlation_id,
        # Checkpoint 5.7J — the per-category breakdown, observability
        # only (see ModelResponse.cache_creation_input_tokens's own
        # docstring).
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        # Checkpoint 3.23 — exposed for Orchestrator's own defensive
        # exactly-one-tool-call cross-check (see generate_reply). Read
        # straight off the raw provider response, never persisted
        # (AiTrace gets no new field — see _record_trace above, whose
        # own fields are completely unchanged by this checkpoint).
        stop_reason=getattr(raw, "stop_reason", None),
        # Checkpoint 5.3 — the resolved tier, same "exposed for
        # observability, never persisted" treatment as stop_reason
        # above (see ModelResponse.tier's own docstring).
        tier=resolved_tier,
        # Checkpoint 5.7 — every call through complete() is, and
        # remains, Anthropic-served; see ModelResponse.provider's own
        # docstring. Explicit, not merely the dataclass default, so a
        # reader never has to wonder whether this was deliberate.
        provider="anthropic",
    )


# Checkpoint 5.7 — per-provider cost-estimation dispatch, kept as one
# small dict-of-callables rather than an if/elif chain repeated in
# complete_with_explicit_provider's own boundary-3 step below. Anthropic's
# estimator stays the existing, unchanged _safe_estimate_cost/_estimate_cost
# pair (reused here verbatim) — this is purely additive wiring, not a
# refactor of either existing function.
def _safe_estimate_gemini_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
    try:
        return gemini_service.estimate_gemini_cost(model, prompt_tokens, completion_tokens)
    except Exception:
        logger.exception("model_router: Gemini cost estimation failed for model=%s", model)
        return None


def complete_with_explicit_provider(
    provider: ModelProvider,
    model: str,
    purpose: ModelCallPurpose,
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    correlation_id: str | None = None,
    tier: IntelligenceTier | None = None,
    max_output_tokens: int | None = None,
    cacheable_system_prefix: str | None = None,
) -> ModelResponse:
    """Checkpoint 5.7, section 20 — the ONE deliberate, explicit,
    traced path capable of reaching Gemini (or, symmetrically,
    Anthropic) outside the purpose-based default `complete()` takes.

    Both `provider` AND `model` are REQUIRED, explicit arguments — no
    purpose->provider or purpose->model auto-resolution happens here at
    all (contrast with complete()'s own _resolve_model/_MODEL_BY_PURPOSE,
    completely untouched by this function). This is the structural
    guarantee behind section 20's own requirements:
    - "cannot accidentally become production default": no existing
      production call site calls this function (confirmed: grep finds
      zero references outside this module's own tests) — `complete()`
      remains the only function any of the 4 real orchestrator call
      sites use, byte-for-byte unchanged by this checkpoint.
    - "does not inspect prompt text to choose provider": `provider` is
      a plain, explicit parameter — nothing here ever reads `messages`
      to decide which provider to call.
    - "no hidden fallback": a failure on the named provider raises
      ModelRouterError exactly like complete() does — it never silently
      retries on the other provider.
    - "deterministic configuration, testable with provider mocked": the
      two provider branches below call plain, already-independently-
      mockable module-level functions (_call_anthropic /
      gemini_service._call_gemini) — a test can monkeypatch either in
      isolation, the same convention this module's own existing test
      suite already uses for _call_anthropic.

    Unlike complete(), this function's AiTrace row records the REAL
    `provider` value (never the hardcoded "anthropic" literal
    complete() itself writes) — AiTrace's own `provider` column already
    supports this with no migration (confirmed in this checkpoint's own
    discovery).

    max_output_tokens (optional) is passed through to the Gemini branch
    only (_call_gemini's own additive parameter) — ignored for
    provider="anthropic", which keeps using _call_anthropic's own fixed
    _MAX_TOKENS, completely unchanged. Exists for a caller (e.g. a bounded
    connectivity smoke test) that wants a hard, small cost ceiling
    independent of whatever the model would naturally produce.

    cacheable_system_prefix (Checkpoint 5.7J) is, symmetrically, passed
    through to the Anthropic branch only — ignored for provider=
    "google_gemini" (Gemini's own adapter never receives or reads it; no
    cross-provider cache abstraction is introduced, per that
    checkpoint's own explicit instruction). No real caller passes this
    here today (chat_completion's own caching lives in complete()'s own
    default path, not this explicit-provider one) — added for symmetry
    with complete()'s own identical parameter, so a future Anthropic-
    explicit caller does not require redesigning this signature again.
    """
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}")
    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Unknown purpose: {purpose!r}")
    if tier is not None and tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier: {tier!r}")

    resolved_tier = _resolve_tier(purpose, tier)
    resolved_correlation_id = correlation_id or secrets.token_hex(16)
    start = time.monotonic()

    # Boundary 1: the provider call itself — same "no usable response
    # exists at all on failure" semantics as complete()'s own boundary 1.
    try:
        if provider == "anthropic":
            if not settings.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            raw = _call_anthropic(
                model, messages, system=system, tools=tools, tool_choice=tool_choice,
                cacheable_system_prefix=cacheable_system_prefix,
            )
        else:
            if not settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY is not configured")
            raw = gemini_service._call_gemini(
                model, messages, system=system, tools=tools, tool_choice=tool_choice,
                max_output_tokens=max_output_tokens,
            )
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        category = _classify_failure(exc) if provider == "anthropic" else gemini_service._classify_gemini_failure(exc)
        _safe_record_trace(
            provider=provider,
            model=model,
            purpose=purpose,
            status="error",
            latency_ms=latency_ms,
            prompt_tokens=None,
            completion_tokens=None,
            estimated_cost_usd=None,
            error_summary=category,
            correlation_id=resolved_correlation_id,
        )
        raise ModelRouterError(category) from exc

    latency_ms = int((time.monotonic() - start) * 1000)

    # Boundary 2: parsing the response — same "provider call already
    # succeeded, so a parsing failure must not also be treated as a
    # success" semantics as complete()'s own boundary 2.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    try:
        if provider == "anthropic":
            prompt_tokens = raw.usage.input_tokens
            completion_tokens = raw.usage.output_tokens
            cache_creation_input_tokens = getattr(raw.usage, "cache_creation_input_tokens", None) or 0
            cache_read_input_tokens = getattr(raw.usage, "cache_read_input_tokens", None) or 0
            text, tool_uses = _extract_response_parts(raw)
        else:
            prompt_tokens = raw.usage_metadata.prompt_token_count if raw.usage_metadata else 0
            completion_tokens = raw.usage_metadata.candidates_token_count if raw.usage_metadata else 0
            text, tool_uses = gemini_service._extract_response_parts_gemini(raw)
    except Exception as exc:
        total_prompt_tokens = (
            prompt_tokens + cache_creation_input_tokens + cache_read_input_tokens
            if prompt_tokens is not None else None
        )
        estimated_cost_usd = (
            (
                _safe_estimate_cost(model, prompt_tokens, completion_tokens, cache_creation_input_tokens, cache_read_input_tokens)
                if provider == "anthropic"
                else _safe_estimate_gemini_cost(model, prompt_tokens, completion_tokens)
            )
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
        category = _classify_failure(exc) if provider == "anthropic" else gemini_service._classify_gemini_failure(exc)
        _safe_record_trace(
            provider=provider,
            model=model,
            purpose=purpose,
            status="error",
            latency_ms=latency_ms,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost_usd,
            error_summary=category,
            correlation_id=resolved_correlation_id,
        )
        raise ModelRouterError(category) from exc

    # Boundary 3: success — cost estimation and trace persistence each
    # get their own failure boundary, same guarantee as complete()'s own.
    total_prompt_tokens = prompt_tokens + cache_creation_input_tokens + cache_read_input_tokens
    estimated_cost_usd = (
        _safe_estimate_cost(model, prompt_tokens, completion_tokens, cache_creation_input_tokens, cache_read_input_tokens)
        if provider == "anthropic"
        else _safe_estimate_gemini_cost(model, prompt_tokens, completion_tokens)
    )
    if not _safe_record_trace(
        provider=provider,
        model=model,
        purpose=purpose,
        status="success",
        latency_ms=latency_ms,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
        error_summary=None,
        correlation_id=resolved_correlation_id,
    ):
        logger.error("model_router: trace persistence failed for a successful call (purpose=%s, provider=%s)", purpose, provider)

    return ModelResponse(
        text=text,
        model=model,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=completion_tokens,
        tool_uses=tool_uses,
        correlation_id=resolved_correlation_id,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        stop_reason=getattr(raw, "stop_reason", None),
        tier=resolved_tier,
        provider=provider,
    )
