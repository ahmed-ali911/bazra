"""Checkpoint 5.7J — Anthropic prompt caching. Zero real provider calls.

Covers: _call_anthropic's cache_control request-shape change (and its
ABSENCE when not requested), cache-aware cost arithmetic with frozen
synthetic values, ModelResponse's new cache fields, AiTrace accounting,
no-fallback/no-hidden-retry, and that Gemini/the claim verifier/
tool_result_reasoning are completely untouched.
"""

from decimal import ROUND_HALF_UP, Decimal

import anthropic
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.model_router import gemini_service
from app.modules.model_router import service as model_router_service
from app.modules.model_router.models import AiTrace


@pytest.fixture(autouse=True)
def _redirect_trace_session(test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router_service, "_trace_session_factory", sessionmaker(bind=test_engine))


def _latest_trace(db_session: Session) -> AiTrace:
    return db_session.execute(select(AiTrace).order_by(AiTrace.id.desc()).limit(1)).scalar_one()


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeUsageWithCache:
    """A response that DID use caching — carries the two additional
    fields the real anthropic SDK's Usage type exposes (confirmed via
    direct SDK introspection: cache_creation_input_tokens,
    cache_read_input_tokens)."""

    def __init__(self, input_tokens, output_tokens, cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text: str, usage):
        self.content = [_FakeTextBlock(text)]
        self.usage = usage


# ---- A. _call_anthropic request shape (zero network — just inspecting kwargs) ----


def test_no_cacheable_system_prefix_leaves_request_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core backward-compatibility guarantee: omitting
    cacheable_system_prefix (every pre-5.7J caller) must produce the
    EXACT same request shape as before this checkpoint — plain string
    system, plain tools list, no cache_control anywhere."""
    captured = {}

    class _FakeMessagesAPI:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage("hi", _FakeUsage(10, 5))

    class _FakeClient:
        messages = _FakeMessagesAPI()

    monkeypatch.setattr(model_router_service, "_get_client", lambda: _FakeClient())

    tools = [{"name": "respond_with_text", "description": "d", "input_schema": {}}]
    model_router_service._call_anthropic(
        "claude-sonnet-5", [{"role": "user", "content": "hi"}], system="dynamic part", tools=tools,
    )

    assert captured["system"] == "dynamic part"  # plain string, not a list of blocks
    assert captured["tools"] == tools  # unchanged, no cache_control key anywhere
    assert "cache_control" not in captured["tools"][-1]


def test_cacheable_system_prefix_splits_system_into_two_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _FakeMessagesAPI:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage("hi", _FakeUsage(10, 5))

    class _FakeClient:
        messages = _FakeMessagesAPI()

    monkeypatch.setattr(model_router_service, "_get_client", lambda: _FakeClient())

    model_router_service._call_anthropic(
        "claude-sonnet-5", [{"role": "user", "content": "hi"}],
        system="dynamic part", cacheable_system_prefix="static identity+instructions",
    )

    assert captured["system"] == [
        {"type": "text", "text": "static identity+instructions", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic part"},
    ]


def test_cacheable_system_prefix_with_no_dynamic_system_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _FakeMessagesAPI:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage("hi", _FakeUsage(10, 5))

    class _FakeClient:
        messages = _FakeMessagesAPI()

    monkeypatch.setattr(model_router_service, "_get_client", lambda: _FakeClient())

    model_router_service._call_anthropic(
        "claude-sonnet-5", [{"role": "user", "content": "hi"}],
        system=None, cacheable_system_prefix="static only",
    )

    assert captured["system"] == [{"type": "text", "text": "static only", "cache_control": {"type": "ephemeral"}}]


def test_cacheable_system_prefix_marks_only_the_last_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _FakeMessagesAPI:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage("hi", _FakeUsage(10, 5))

    class _FakeClient:
        messages = _FakeMessagesAPI()

    monkeypatch.setattr(model_router_service, "_get_client", lambda: _FakeClient())

    tools = [
        {"name": "propose_create_task", "description": "d1", "input_schema": {}},
        {"name": "get_weather", "description": "d2", "input_schema": {}},
        {"name": "respond_with_text", "description": "d3", "input_schema": {}},
    ]
    model_router_service._call_anthropic(
        "claude-sonnet-5", [{"role": "user", "content": "hi"}],
        system="dyn", cacheable_system_prefix="static", tools=tools,
    )

    assert "cache_control" not in captured["tools"][0]
    assert "cache_control" not in captured["tools"][1]
    assert captured["tools"][2]["cache_control"] == {"type": "ephemeral"}
    assert captured["tools"][2]["name"] == "respond_with_text"


def test_cacheable_system_prefix_never_mutates_the_caller_own_tools_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tools may be a SHARED module-level constant (chat_service.
    _TOOLS_OFFERED) — marking the last one for caching must never
    mutate the caller's own list or its dict objects in place, or every
    SUBSEQUENT call (cached or not, any purpose) would be corrupted."""
    class _FakeMessagesAPI:
        def create(self, **kwargs):
            return _FakeMessage("hi", _FakeUsage(10, 5))

    class _FakeClient:
        messages = _FakeMessagesAPI()

    monkeypatch.setattr(model_router_service, "_get_client", lambda: _FakeClient())

    shared_tool = {"name": "respond_with_text", "description": "d", "input_schema": {}}
    shared_tools_list = [shared_tool]

    model_router_service._call_anthropic(
        "claude-sonnet-5", [{"role": "user", "content": "hi"}],
        system="dyn", cacheable_system_prefix="static", tools=shared_tools_list,
    )

    assert "cache_control" not in shared_tool  # the ORIGINAL dict, untouched
    assert shared_tools_list == [shared_tool]  # the ORIGINAL list, untouched (still len 1, same object)


# ---- B. cost arithmetic — frozen synthetic values -------------------------


def test_estimate_cost_with_no_caching_matches_pre_5_7j_arithmetic() -> None:
    """cache_creation_input_tokens/cache_read_input_tokens both default
    to 0 — must reproduce the EXACT pre-5.7J cost for the same
    prompt/completion tokens."""
    cost = model_router_service._estimate_cost("claude-sonnet-5", 1000, 500)
    assert cost == (Decimal(1000) * Decimal("2.00") + Decimal(500) * Decimal("10.00")) / Decimal(1_000_000)
    assert cost == Decimal("0.007000")


def test_estimate_cost_with_cache_write_applies_1_25x_multiplier() -> None:
    # sonnet: input $2.00/1M -> cache write = 2.00 * 1.25 = $2.50/1M
    cost = model_router_service._estimate_cost(
        "claude-sonnet-5", prompt_tokens=0, completion_tokens=0, cache_creation_input_tokens=1_000_000,
    )
    assert cost == Decimal("2.50")


def test_estimate_cost_with_cache_read_applies_0_1x_multiplier() -> None:
    # sonnet: input $2.00/1M -> cache read = 2.00 * 0.1 = $0.20/1M
    cost = model_router_service._estimate_cost(
        "claude-sonnet-5", prompt_tokens=0, completion_tokens=0, cache_read_input_tokens=1_000_000,
    )
    assert cost == Decimal("0.20")


def test_estimate_cost_combines_all_four_categories_at_their_own_rates() -> None:
    """Frozen synthetic values exercising every category at once —
    base input, cache write, cache read, and output, each at its own
    documented rate, summed."""
    cost = model_router_service._estimate_cost(
        "claude-haiku-4-5",
        prompt_tokens=100_000, completion_tokens=50_000,
        cache_creation_input_tokens=200_000, cache_read_input_tokens=1_000_000,
    )
    # haiku: input $1.00/1M, output $5.00/1M
    expected = (
        Decimal(100_000) * Decimal("1.00")
        + Decimal(200_000) * Decimal("1.00") * Decimal("1.25")
        + Decimal(1_000_000) * Decimal("1.00") * Decimal("0.1")
        + Decimal(50_000) * Decimal("5.00")
    ) / Decimal(1_000_000)
    assert cost == expected
    assert cost == Decimal("0.700000")


def test_safe_estimate_cost_cache_aware_signature_backward_compatible() -> None:
    """_safe_estimate_cost's own two-positional-arg call shape (used
    everywhere before this checkpoint) still works unchanged."""
    cost = model_router_service._safe_estimate_cost("claude-sonnet-5", 1000, 500)
    assert cost == Decimal("0.007000")


# ---- C. complete() end-to-end: ModelResponse + AiTrace accounting ---------


def test_complete_with_caching_exposes_cache_fields_on_model_response(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda *a, **k: _FakeMessage("hi", _FakeUsageWithCache(50, 20, cache_creation_input_tokens=0, cache_read_input_tokens=9000)),
    )

    result = model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": "hi"}],
        system="dynamic", cacheable_system_prefix="static",
    )

    assert result.cache_creation_input_tokens == 0
    assert result.cache_read_input_tokens == 9000
    # Checkpoint 5.7J — prompt_tokens on ModelResponse is now the TOTAL
    # (base + write + read), per complete()'s own documented contract.
    assert result.prompt_tokens == 50 + 0 + 9000


def test_complete_without_caching_cache_fields_default_to_zero(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overwhelming majority of calls (every purpose except
    chat_completion's own default path) — cache fields must be exactly
    0, never None, never missing."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda *a, **k: _FakeMessage("hi", _FakeUsage(50, 20)))

    result = model_router_service.complete(purpose="claim_verification", messages=[{"role": "user", "content": "hi"}])

    assert result.cache_creation_input_tokens == 0
    assert result.cache_read_input_tokens == 0
    assert result.prompt_tokens == 50  # unchanged — no cache tokens to add


def test_complete_aitrace_prompt_tokens_is_the_total_including_cache(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda *a, **k: _FakeMessage("hi", _FakeUsageWithCache(50, 20, cache_creation_input_tokens=14637, cache_read_input_tokens=0)),
    )

    model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": "hi"}],
        system="dynamic", cacheable_system_prefix="static",
    )

    trace = _latest_trace(db_session)
    assert trace.prompt_tokens == 50 + 14637
    # cost must use the cache-write multiplier for the 14637 cached
    # tokens, not the flat base rate. Quantized to 6 decimal places
    # (ROUND_HALF_UP, matching PostgreSQL's own NUMERIC column rounding
    # — confirmed empirically to differ from Python Decimal's own
    # default ROUND_HALF_EVEN) to match AiTrace.estimated_cost_usd's
    # Numeric(10, 6) column precision — a real, pre-existing DB-level
    # rounding behavior, not a caching-specific concern.
    expected_cost = (
        Decimal(50) * Decimal("2.00") + Decimal(14637) * Decimal("2.00") * Decimal("1.25") + Decimal(20) * Decimal("10.00")
    ) / Decimal(1_000_000)
    assert trace.estimated_cost_usd == expected_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def test_complete_cache_read_turn_cost_is_cheaper_than_equivalent_uncached_turn(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual point of the feature: an otherwise-identical request
    costs LESS when the static block is served from cache."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")

    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda *a, **k: _FakeMessage("hi", _FakeUsage(14637 + 50, 20)),
    )
    model_router_service.complete(purpose="claim_verification", messages=[{"role": "user", "content": "hi"}])
    uncached_trace = _latest_trace(db_session)

    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda *a, **k: _FakeMessage("hi", _FakeUsageWithCache(50, 20, cache_read_input_tokens=14637)),
    )
    model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": "hi"}],
        system="dynamic", cacheable_system_prefix="static",
    )
    cached_trace = _latest_trace(db_session)

    assert cached_trace.prompt_tokens == uncached_trace.prompt_tokens  # same total size
    assert cached_trace.estimated_cost_usd < uncached_trace.estimated_cost_usd  # but cheaper


def test_complete_cache_miss_makes_exactly_one_attempt_no_hidden_retry(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    call_count = {"value": 0}

    def _fake_call(*a, **k):
        call_count["value"] += 1
        # A cache MISS still succeeds normally (input_tokens includes
        # what would have been cached; cache_creation fires instead of
        # cache_read) — this is not a provider error at all.
        return _FakeMessage("hi", _FakeUsageWithCache(50, 20, cache_creation_input_tokens=14637))

    monkeypatch.setattr(model_router_service, "_call_anthropic", _fake_call)
    model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": "hi"}],
        system="dynamic", cacheable_system_prefix="static",
    )
    assert call_count["value"] == 1


def test_complete_provider_failure_with_caching_requested_still_uses_normal_5_1_taxonomy(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caching must not create a new failure path — a provider error
    classifies exactly like any other, no fallback, no retry. Raises a
    real `anthropic.APIStatusError`-shaped exception (not a hand-wrapped
    ModelRouterError — _call_anthropic itself never raises that type;
    only complete() wraps into it), matching _classify_failure's own
    documented precedence (checked via this exact mechanism already in
    test_model_router.py)."""
    import httpx2

    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    call_count = {"value": 0}

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(503, request=request, json={"type": "error", "error": {"type": "overloaded_error", "message": "simulated"}})
    real_exc = anthropic.OverloadedError("simulated", response=response, body={"type": "error", "error": {"type": "overloaded_error"}})

    def _raise(*a, **k):
        call_count["value"] += 1
        raise real_exc

    monkeypatch.setattr(model_router_service, "_call_anthropic", _raise)
    with pytest.raises(model_router_service.ModelRouterError) as exc_info:
        model_router_service.complete(
            purpose="chat_completion", messages=[{"role": "user", "content": "hi"}],
            system="dynamic", cacheable_system_prefix="static",
        )
    assert exc_info.value.category == "provider_unavailable"
    assert call_count["value"] == 1


# ---- D. Gemini / verifier / tool_result_reasoning completely untouched ----


def test_gemini_service_module_has_no_cache_control_concept_at_all() -> None:
    """Structural proof: no cross-provider cache abstraction was
    introduced — gemini_service.py has no cacheable_system_prefix
    parameter, no cache_control construction, anywhere."""
    import inspect

    source = inspect.getsource(gemini_service)
    assert "cacheable_system_prefix" not in source
    assert "cache_control" not in source


def test_complete_with_explicit_provider_gemini_branch_ignores_cacheable_system_prefix(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if a future caller mistakenly passed cacheable_system_prefix
    alongside provider=google_gemini, the Gemini branch must never
    read or act on it — _call_gemini's own signature has no such
    parameter, so passing it would be structurally impossible to wire
    through; this test proves the Gemini call succeeds normally,
    confirming the parameter is simply never touched on that branch."""
    monkeypatch.setattr(model_router_service.settings, "gemini_api_key", "test-key")

    from google.genai import types

    monkeypatch.setattr(
        gemini_service, "_call_gemini",
        lambda *a, **k: types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text="pong")]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=8, candidates_token_count=1, total_token_count=9),
        ),
    )

    result = model_router_service.complete_with_explicit_provider(
        provider="google_gemini", model="gemini-3.1-flash-lite", purpose="chat_completion",
        messages=[{"role": "user", "content": "hi"}], cacheable_system_prefix="irrelevant-for-gemini",
    )
    assert result.provider == "google_gemini"
    assert result.cache_creation_input_tokens == 0
    assert result.cache_read_input_tokens == 0
