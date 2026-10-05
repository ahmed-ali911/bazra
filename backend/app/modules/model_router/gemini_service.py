"""Checkpoint 5.7 — the Google Gemini provider adapter.

Mirrors model_router/service.py's own low-level Anthropic primitives
(_call_anthropic / _extract_response_parts / _classify_failure)
structurally, but is a COMPLETELY SEPARATE module — nothing here is
imported by service.py's complete() or by any of the 4 existing
production purposes, and nothing in this module writes an AiTrace row
itself (that remains service.py's own job, in
complete_with_explicit_provider, so there is exactly one place that
decides what gets traced).

OFFICIAL INTEGRATION DECISION (verified live against ai.google.dev and
the googleapis/python-genai source at implementation time, 2026-10-06
— never from training-data memory, per this checkpoint's own explicit
instruction):

- SDK: `google-genai` (import `from google import genai`), the current
  unified, officially-recommended Python SDK (GA since May 2025;
  replaces the deprecated `google-generativeai`). Source:
  ai.google.dev/gemini-api/docs/quickstart.
- API surface: the classic, STATELESS `client.models.generate_content(
  model=, contents=, config=)` — confirmed still fully current and
  documented (ai.google.dev/gemini-api/docs/generate-content/
  get-started), not the newer, more heavily-promoted Interactions API
  (`client.interactions.create(..., previous_interaction_id=...)`).
  Deliberately NOT the Interactions API: it is stateful (Google holds
  conversation state server-side between turns via an opaque
  interaction id), while BAZRA's entire Model Router contract — both
  the existing Anthropic integration and every orchestrator caller — is
  stateless per-call (full history reconstructed and passed explicitly
  every time; no server-side session concept anywhere in this
  codebase). Adopting a stateful API would be a structural mismatch for
  no benefit; `generate_content` is the direct, minimal-translation
  analogue of `_call_anthropic`.
- Exceptions: `google.genai.errors.APIError` (base), `ClientError`
  (4xx), `ServerError` (5xx) — confirmed directly against the actual
  installed SDK source (2.28.0). `.code` (int, HTTP status), `.status`
  (str, a Google RPC-style status name like "NOT_FOUND",
  "RESOURCE_EXHAUSTED" — read straight off the response body's own
  `error.status`/`status` field), `.message` — the same kind of
  structured, documented evidence `.type` gives on Anthropic's
  `APIStatusError`, used here the same way (see _classify_gemini_failure).

NOT IMPLEMENTED HERE (out of this checkpoint's scope, per its own
brief): Google Search grounding, any tool EXECUTION authority, response
caching, streaming, multi-modal (image/audio/video) input — only what
the existing BAZRA provider-neutral contract (TextBlock/ToolUseBlock/
ToolResultBlock, text in, text+tool_uses out) actually needs.
"""

import logging
import time
from decimal import Decimal

import httpx
from google import genai
from google.genai import errors, types

from app.config import settings
from app.modules.model_router.schemas import (
    ModelFailureCategory,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


def _get_gemini_client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


# ---- request translation (BAZRA's provider-neutral shape -> Gemini) ----
#
# Gemini has no "system" message role (same shape Anthropic has — a
# separate top-level parameter — see GenerateContentConfig.system_
# instruction) but uses "model" where Anthropic/BAZRA use "assistant"
# for the prior-turn role name. This is the ONLY role translation
# needed; "user" is spelled the same in both.
_ROLE_TO_GEMINI = {"user": "user", "assistant": "model"}


def _tool_use_id_to_name_map(messages: list[dict]) -> dict[str, str]:
    """Gemini's function_response Part is keyed by the function's own
    NAME, not by a call id the way Anthropic's tool_result is keyed by
    tool_use_id — BAZRA's own ToolResultBlock (schemas.py) carries only
    tool_use_id/content/is_error, matching Anthropic's shape, with no
    name field at all (confirmed: the one real production caller,
    orchestrator.service.generate_tool_result_reply, never needs a name
    there since Anthropic's own continuation never requires one).
    Recovering the name requires scanning the SAME messages list for
    the ToolUseBlock that originally made this exact call — by
    construction (see generate_tool_result_reply's own docstring: "the
    reconstructed assistant turn ... must immediately precede the
    tool_result message, with nothing in between"), that ToolUseBlock
    is always present earlier in this same list for any ToolResultBlock
    BAZRA ever constructs."""
    mapping: dict[str, str] = {}
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            continue
        for block in content:
            if isinstance(block, ToolUseBlock):
                mapping[block.id] = block.name
    return mapping


def _part_for_block(block: TextBlock | ToolUseBlock | ToolResultBlock, tool_names: dict[str, str]) -> types.Part:
    if isinstance(block, TextBlock):
        return types.Part(text=block.text)
    if isinstance(block, ToolUseBlock):
        return types.Part(function_call=types.FunctionCall(name=block.name, args=block.input))
    if isinstance(block, ToolResultBlock):
        # BAZRA's ToolResultBlock.content is already a plain string
        # (see schemas.py's own docstring: "callers serialize whatever
        # structured payload they have into a JSON string themselves"
        # — produced for Anthropic's own string-shaped tool_result
        # content). Gemini's function_response.response wants a dict,
        # not a bare string — wrapped under one fixed key rather than
        # attempting to re-parse caller-produced text (parsing it back
        # would assume every caller's JSON always decodes to a dict,
        # which the existing contract never promised).
        name = tool_names.get(block.tool_use_id)
        if name is None:
            raise ValueError(
                f"no matching ToolUseBlock found for tool_use_id={block.tool_use_id!r} "
                "— Gemini's function_response requires the original function name"
            )
        key = "error" if block.is_error else "result"
        return types.Part(function_response=types.FunctionResponse(name=name, response={key: block.content}))
    raise TypeError(f"Unknown content block type: {type(block)!r}")


def _serialize_messages_for_gemini(messages: list[dict]) -> list[types.Content]:
    tool_names = _tool_use_id_to_name_map(messages)
    contents: list[types.Content] = []
    for message in messages:
        role = _ROLE_TO_GEMINI[message["role"]]
        content = message["content"]
        if isinstance(content, str):
            parts = [types.Part(text=content)]
        else:
            parts = [_part_for_block(block, tool_names) for block in content]
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _translate_tools_for_gemini(tools: list[dict] | None) -> list[types.Tool] | None:
    """BAZRA's tools are passed in Anthropic's own native shape
    (confirmed: orchestrator/service.py's _CERTIFY_CLAIM_TOOL — `{name,
    description, input_schema}`) — Gemini's FunctionDeclaration wants
    `parameters` instead of `input_schema`, otherwise the same JSON
    Schema shape. Translated here, inside the adapter, per this
    checkpoint's own section 7 instruction ("translate inside the
    provider adapter... do not force production callers to understand
    Gemini-specific message schemas") — callers still only ever
    construct Anthropic-shaped tool dicts."""
    if not tools:
        return None
    declarations = [
        {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}
        for t in tools
    ]
    return [types.Tool(function_declarations=declarations)]


def _translate_tool_choice_for_gemini(tool_choice: dict | None) -> types.ToolConfig | None:
    """Best-effort translation of the two real Anthropic tool_choice
    shapes this codebase actually constructs (orchestrator/service.py's
    own _PRIMARY_CHAT_TOOL_CHOICE = {"type": "any", ...} and
    _CERTIFY_CLAIM_TOOL_CHOICE = {"type": "tool", "name": ..., ...}) —
    NOT a general Anthropic-tool-choice-grammar translator. Anthropic's
    own `disable_parallel_tool_use` has no documented Gemini
    FunctionCallingConfig equivalent — honestly dropped, not faked, the
    same "do not pretend a distinction exists" principle this
    checkpoint's own brief applies to error-taxonomy mapping (section
    10), applied here to request translation instead."""
    if tool_choice is None:
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "any":
        return types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY"))
    if choice_type == "auto":
        return types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO"))
    if choice_type == "tool" and tool_choice.get("name"):
        return types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=[tool_choice["name"]])
        )
    # An unrecognized shape is left untranslated (None) rather than
    # guessed at — Gemini's own default (AUTO) then applies, the same
    # "fail toward the provider's own safe default, never invent a
    # stricter constraint than was actually requested" posture.
    return None


def _call_gemini(
    model: str,
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
) -> types.GenerateContentResponse:
    """The only function in this module that talks to the google-genai
    SDK directly — mirrors _call_anthropic's own role exactly. Raises
    whatever the SDK itself raises (google.genai.errors.APIError and
    subclasses on an HTTP error response; plain httpx transport
    exceptions — TimeoutException/HTTPError — on a pre-response
    transport failure, since the SDK is itself httpx-based); never
    swallows or wraps here — that is _classify_gemini_failure's job,
    called by the caller, not this function."""
    contents = _serialize_messages_for_gemini(messages)
    config_kwargs: dict = {}
    if system is not None:
        config_kwargs["system_instruction"] = system
    translated_tools = _translate_tools_for_gemini(tools)
    if translated_tools is not None:
        config_kwargs["tools"] = translated_tools
    translated_tool_choice = _translate_tool_choice_for_gemini(tool_choice)
    if translated_tool_choice is not None:
        config_kwargs["tool_config"] = translated_tool_choice
    config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

    return _get_gemini_client().models.generate_content(model=model, contents=contents, config=config)


# ---- response translation (Gemini -> BAZRA's provider-neutral shape) ----


def _extract_response_parts_gemini(response: types.GenerateContentResponse) -> tuple[str | None, list[ToolUseBlock]]:
    """Mirrors _extract_response_parts's own contract exactly: text may
    be None only alongside at least one tool use; neither present at
    all is a genuinely malformed/empty response.

    Gemini's FunctionCall has no stable call-id field populated by
    default (confirmed empirically against the installed SDK: `.id` is
    None unless the caller supplies one) — unlike Anthropic's tool_use,
    which always carries a real id BAZRA later echoes back as
    tool_use_id. A synthetic, stable-within-this-response id is
    generated here (index-based, deterministic, never random) so a
    caller that reuses this response's own ToolUseBlock.id in a later
    ToolResultBlock (the one real production pattern — see
    _tool_use_id_to_name_map's own docstring) has something consistent
    to reference; this id is BAZRA-adapter-local and never sent to
    Gemini on a later call (Gemini is re-keyed by name, not id — see
    _part_for_block's own ToolResultBlock branch).
    """
    candidates = response.candidates or []
    parts = candidates[0].content.parts if candidates and candidates[0].content else []
    tool_uses: list[ToolUseBlock] = []
    for index, part in enumerate(parts or []):
        if part.function_call is not None:
            synthetic_id = f"gemini_call_{index}"
            tool_uses.append(ToolUseBlock(id=synthetic_id, name=part.function_call.name, input=dict(part.function_call.args or {})))
    text = response.text
    if text is None and not tool_uses:
        raise ValueError("No text content or tool use in provider response")
    return text, tool_uses


# ---- failure classification --------------------------------------------


def _classify_gemini_failure(exc: Exception) -> ModelFailureCategory:
    """Normalized, provider-neutral failure classification — the
    Gemini-adapter counterpart to model_router/service.py's own
    _classify_failure, same precedence philosophy (most specific,
    structured evidence first; honest unknown_provider_error fallback
    last; never brittle free-text message matching).

    Precedence:
    1. google.genai.errors.APIError.code (the HTTP status — the single
       most stable, documented axis; confirmed directly against the
       installed SDK: every APIError the SDK's own raise_for_response
       constructs always sets this from the real HTTP response status).
    2. .status (a Google RPC-style name like "RESOURCE_EXHAUSTED",
       "NOT_FOUND" — read straight from the response body's own
       status field; used as a cross-check/fallback when .code is
       missing or an unrecognized value) — confirmed from the actual
       APIError._get_status implementation, not assumed.
    3. httpx transport-level exceptions (TimeoutException/HTTPError) —
       the SDK is itself httpx-based; a failure BEFORE any HTTP
       response exists at all surfaces as a plain httpx exception, the
       same two-tier structure model_router/service.py's own
       _classify_failure already applies for Anthropic's APITimeoutError/
       APIConnectionError, and the same pattern this repo's own
       weather/service.py already uses for Open-Meteo.
    4. unknown_provider_error — the final, honest fallback.

    HONEST, DOCUMENTED AMBIGUITY (section 10's own explicit allowance
    to document rather than invent): Google's classic error-body shape
    (confirmed via the installed SDK's own APIError._get_status) uses
    a SINGLE status, "RESOURCE_EXHAUSTED", for BOTH daily-quota
    exhaustion and short-term per-minute rate limiting — both surface
    as HTTP 429 with no further documented, stable, structured signal
    to tell them apart (a separate, newer-looking "quota_exceeded" vs
    "rate_limit_exceeded" vocabulary appears on one Google doc page,
    but could not be confirmed against this SDK's own actual
    .status-extraction code, which reads a single `status` string, not
    two). Rather than guess, every HTTP 429 maps to "rate_limited"
    here — the more conservative, "potentially transient" reading
    (matching this taxonomy's own documented retryability notes) — and
    this ambiguity is named here explicitly, not silently resolved.
    NOT reproduced against a real failing call (0 real Gemini calls
    were made in this checkpoint, per its own constraints) — this
    mapping rests on the SDK's own documented/source-confirmed
    contract, not an observed response body, exactly like
    _classify_failure's own billing_error precedent.
    """
    if isinstance(exc, RuntimeError) and "GEMINI_API_KEY" in str(exc):
        return "authentication"

    if isinstance(exc, errors.APIError):
        code = exc.code
        if code == 401:
            return "authentication"
        if code == 403:
            return "authentication"
        if code == 404:
            return "model_unavailable"
        if code == 429:
            return "rate_limited"
        if code == 400:
            return "invalid_request"
        if code is not None and 500 <= code < 600:
            return "provider_unavailable"

        # .code absent/unrecognized — fall back to the RPC-style
        # .status string, same precedence philosophy as _classify_failure's
        # own "`.type` absent -> exception class" fallback step.
        status = exc.status
        if status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
            return "authentication"
        if status == "NOT_FOUND":
            return "model_unavailable"
        if status == "RESOURCE_EXHAUSTED":
            return "rate_limited"
        if status in ("INVALID_ARGUMENT", "FAILED_PRECONDITION"):
            return "invalid_request"
        if status in ("UNAVAILABLE", "INTERNAL", "ABORTED"):
            return "provider_unavailable"
        if status == "DEADLINE_EXCEEDED":
            return "timeout"
        return "unknown_provider_error"

    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "connection"
    if isinstance(exc, ValueError):
        return "unparseable_response"
    return "unknown_provider_error"


# ---- cost estimation ------------------------------------------------------
#
# USD per 1,000,000 tokens — a static, hand-maintained table, the same
# "ESTIMATE, never billing-accurate" convention as model_router/
# service.py's own _COST_PER_MILLION_TOKENS_USD. Sourced from Google's
# published pricing at ai.google.dev/gemini-api/docs/pricing, verified
# live at implementation time (2026-10-06) — standard PAID-TIER API
# token pricing only (never free-tier, batch, or cached-input rates,
# which this checkpoint's own brief explicitly says not to mix in
# unlabeled). gemini-2.5-pro's own published per-million rate is
# threshold-dependent (≤200k vs >200k prompt tokens); this table stores
# only the ≤200k rate — the common case for BAZRA's own narrow, bounded
# call shapes — and documents the omission rather than silently
# guessing at the >200k rate.
_GEMINI_COST_PER_MILLION_TOKENS_USD: dict[str, dict[str, Decimal]] = {
    "gemini-3.1-flash-lite": {"input": Decimal("0.25"), "output": Decimal("1.50")},
    "gemini-2.5-pro": {"input": Decimal("1.25"), "output": Decimal("10.00")},  # <=200k prompt tokens only
}


def estimate_gemini_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
    rates = _GEMINI_COST_PER_MILLION_TOKENS_USD.get(model)
    if rates is None:
        return None
    return (Decimal(prompt_tokens) * rates["input"] + Decimal(completion_tokens) * rates["output"]) / Decimal(
        1_000_000
    )


# ---- the explicit, isolated invocation entry point (section 20) ---------


def generate_with_explicit_gemini_model(
    model: str,
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
) -> tuple[str | None, list[ToolUseBlock], int, int, int]:
    """The ONE deliberate, explicit entry point for invoking Gemini —
    requires an explicit model string, never resolved from any purpose/
    tier table. No production code calls this (confirmed: grep finds
    zero references outside this module's own tests and this
    docstring) — it exists so development/evaluation code (a future
    Gemini-vs-Claude benchmark, mirroring evals/benchmarks/generation.py's
    own existing bypass-AiTrace pattern for Anthropic) has a real,
    testable, mockable way to reach Gemini without touching
    model_router/service.py's complete() or its purpose-based
    resolution tables at all.

    Deliberately bypasses AiTrace (same as evals/benchmarks/
    generation.py's own Anthropic equivalent) — this path is for
    offline evaluation, not live production traffic; see
    model_router/service.py's own complete_with_explicit_provider for
    the traced, production-adjacent explicit-provider path instead.

    Returns (text, tool_uses, prompt_tokens, completion_tokens,
    latency_ms) — a plain tuple, not ModelResponse, matching
    evals/benchmarks/generation.py's own existing
    generate_with_explicit_model return shape for its Anthropic
    equivalent (kept symmetric so a future benchmark runner can treat
    both providers identically).

    Raises whatever _call_gemini raises, uncaught — callers (a future
    benchmark runner) classify failures themselves via
    _classify_gemini_failure, exactly like the existing Anthropic
    bypass path's own documented contract.
    """
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    start = time.monotonic()
    response = _call_gemini(model, messages, system=system, tools=tools, tool_choice=tool_choice)
    latency_ms = int((time.monotonic() - start) * 1000)
    text, tool_uses = _extract_response_parts_gemini(response)
    prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
    completion_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
    return text, tool_uses, prompt_tokens or 0, completion_tokens or 0, latency_ms
