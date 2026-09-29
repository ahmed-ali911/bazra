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
from app.modules.model_router.models import AiTrace
from app.modules.model_router.schemas import (
    VALID_PURPOSES,
    ModelCallPurpose,
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
    recorded — see complete()'s docstring for the exact guarantee."""


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
    """
    kwargs = {"model": model, "max_tokens": _MAX_TOKENS, "messages": _serialize_messages(messages)}
    if system is not None:
        kwargs["system"] = system
    if tools is not None:
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


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
    rates = _COST_PER_MILLION_TOKENS_USD.get(model)
    if rates is None:
        return None
    return (Decimal(prompt_tokens) * rates["input"] + Decimal(completion_tokens) * rates["output"]) / Decimal(
        1_000_000
    )


def _safe_estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
    """A bad rate-table lookup is a bug in bookkeeping, not a failure of
    the model call — it must not prevent a successful call from
    returning to its caller or from being traced at all. Logs the
    failure (never prompt/response/key material) and returns None, so
    the trace still records with estimated_cost_usd=NULL rather than
    propagating.
    """
    try:
        return _estimate_cost(model, prompt_tokens, completion_tokens)
    except Exception:
        logger.exception("model_router: cost estimation failed for model=%s", model)
        return None


def _summarize_error(exc: Exception) -> str:
    """Short, categorized reason — never the raw exception message,
    which isn't safe to assume is free of request-derived detail."""
    if isinstance(exc, RuntimeError) and "ANTHROPIC_API_KEY" in str(exc):
        return "missing_api_key"
    if isinstance(exc, anthropic.AuthenticationError):
        return "invalid_api_key"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limited"
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.APIConnectionError):
        return "connection_error"
    if isinstance(exc, anthropic.APIStatusError):
        return "provider_error"
    if isinstance(exc, ValueError):
        return "unparseable_response"
    return "unknown_error"


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
    """
    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Unknown purpose: {purpose!r}")

    model = _resolve_model(purpose)
    resolved_correlation_id = correlation_id or secrets.token_hex(16)
    start = time.monotonic()

    # Boundary 1: the provider call itself. A failure here means no
    # usable response exists at all — the only case treated as "the
    # call failed" with no possibility of partial data.
    try:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        raw = _call_anthropic(model, messages, system=system, tools=tools, tool_choice=tool_choice)
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        _safe_record_trace(
            provider="anthropic",
            model=model,
            purpose=purpose,
            status="error",
            latency_ms=latency_ms,
            prompt_tokens=None,
            completion_tokens=None,
            estimated_cost_usd=None,
            error_summary=_summarize_error(exc),
            correlation_id=resolved_correlation_id,
        )
        raise ModelRouterError(_summarize_error(exc)) from exc

    latency_ms = int((time.monotonic() - start) * 1000)

    # Boundary 2: parsing the response. Separate from boundary 1 on
    # purpose — the provider call already succeeded here, so a parsing
    # failure must not be treated as "also record a success," since no
    # success trace has been written yet at this point.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    try:
        prompt_tokens = raw.usage.input_tokens
        completion_tokens = raw.usage.output_tokens
        text, tool_uses = _extract_response_parts(raw)
    except Exception as exc:
        # prompt_tokens/completion_tokens survive from above even if the
        # failure happened on the _extract_text line — real,
        # already-incurred token/cost data is preserved here rather than
        # discarded, when it was actually available.
        estimated_cost_usd = (
            _safe_estimate_cost(model, prompt_tokens, completion_tokens)
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
        _safe_record_trace(
            provider="anthropic",
            model=model,
            purpose=purpose,
            status="error",
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost_usd,
            error_summary=_summarize_error(exc),
            correlation_id=resolved_correlation_id,
        )
        raise ModelRouterError(_summarize_error(exc)) from exc

    # Boundary 3: the call succeeded and parsed cleanly. Cost estimation
    # and trace persistence each get their own failure boundary — if
    # either fails, the caller still gets their ModelResponse.
    estimated_cost_usd = _safe_estimate_cost(model, prompt_tokens, completion_tokens)
    if not _safe_record_trace(
        provider="anthropic",
        model=model,
        purpose=purpose,
        status="success",
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
        error_summary=None,
        correlation_id=resolved_correlation_id,
    ):
        logger.error("model_router: trace persistence failed for a successful call (purpose=%s)", purpose)

    return ModelResponse(
        text=text,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_uses=tool_uses,
        correlation_id=resolved_correlation_id,
        # Checkpoint 3.23 — exposed for Orchestrator's own defensive
        # exactly-one-tool-call cross-check (see generate_reply). Read
        # straight off the raw provider response, never persisted
        # (AiTrace gets no new field — see _record_trace above, whose
        # own fields are completely unchanged by this checkpoint).
        stop_reason=getattr(raw, "stop_reason", None),
    )
