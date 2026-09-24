import logging
import time
from decimal import Decimal

import anthropic
from anthropic import Anthropic
from anthropic.types import Message
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import SessionLocal
from app.modules.model_router.models import AiTrace
from app.modules.model_router.schemas import ModelCallPurpose, ModelResponse, VALID_PURPOSES

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


def _call_anthropic(model: str, messages: list[dict[str, str]], system: str | None = None) -> Message:
    """The only function in this module that talks to the Anthropic SDK
    directly — everything else in complete() is boundary/bookkeeping
    logic around this one call. system is Anthropic's own separate
    top-level parameter, not a role inside `messages` — the Messages API
    has no "system" message role."""
    kwargs = {"model": model, "max_tokens": _MAX_TOKENS, "messages": messages}
    if system is not None:
        kwargs["system"] = system
    return _get_client().messages.create(**kwargs)


def _extract_text(message: Message) -> str:
    text_parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    if not text_parts:
        raise ValueError("No text content in provider response")
    return "".join(text_parts)


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


def complete(purpose: ModelCallPurpose, messages: list[dict[str, str]], system: str | None = None) -> ModelResponse:
    """The only function other modules call to reach a model provider.

    system is an additive, backward-compatible parameter (Checkpoint
    3.2) — Anthropic's Messages API takes system instructions as their
    own top-level parameter, not a role inside `messages`. Existing
    callers that never pass it are unaffected.

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

    model = _DEFAULT_MODEL
    start = time.monotonic()

    # Boundary 1: the provider call itself. A failure here means no
    # usable response exists at all — the only case treated as "the
    # call failed" with no possibility of partial data.
    try:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        raw = _call_anthropic(model, messages, system=system)
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
        text = _extract_text(raw)
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
    ):
        logger.error("model_router: trace persistence failed for a successful call (purpose=%s)", purpose)

    return ModelResponse(text=text, model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
