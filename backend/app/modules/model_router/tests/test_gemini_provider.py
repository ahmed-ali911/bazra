"""Checkpoint 5.7 — tests for the Gemini provider adapter
(model_router/gemini_service.py) and the explicit, traced dispatch path
(model_router/service.py's complete_with_explicit_provider).

ZERO REAL GEMINI CALLS. ZERO REAL ANTHROPIC CALLS. Every test here
mocks the provider boundary (gemini_service._call_gemini /
model_router_service._call_anthropic) — the same house convention
test_model_router.py already uses for Anthropic.
"""

from decimal import Decimal

import pytest
from google.genai import errors, types
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.model_router import gemini_service
from app.modules.model_router import service as model_router_service
from app.modules.model_router.models import AiTrace
from app.modules.model_router.schemas import ModelResponse, TextBlock, ToolResultBlock, ToolUseBlock


@pytest.fixture(autouse=True)
def _redirect_trace_session(test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same redirect test_model_router.py's own fixture performs — file-
    scoped by pytest's own fixture rules, so this file needs its own
    copy rather than importing the other file's fixture."""
    monkeypatch.setattr(model_router_service, "_trace_session_factory", sessionmaker(bind=test_engine))


def _trace_count(db_session: Session) -> int:
    return db_session.execute(select(func.count()).select_from(AiTrace)).scalar_one()


def _latest_trace(db_session: Session) -> AiTrace:
    return db_session.execute(select(AiTrace).order_by(AiTrace.id.desc()).limit(1)).scalar_one()


def _fake_gemini_response(text: str | None = None, function_calls: list[tuple[str, dict]] = (), prompt_tokens: int = 10, completion_tokens: int = 5) -> types.GenerateContentResponse:
    parts = []
    if text is not None:
        parts.append(types.Part(text=text))
    for name, args in function_calls:
        parts.append(types.Part(function_call=types.FunctionCall(name=name, args=args)))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=types.FinishReason.STOP)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens, candidates_token_count=completion_tokens, total_token_count=prompt_tokens + completion_tokens
        ),
    )


# ---- request translation --------------------------------------------------


def test_serialize_simple_string_messages() -> None:
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    contents = gemini_service._serialize_messages_for_gemini(messages)
    assert [c.role for c in contents] == ["user", "model"]
    assert contents[0].parts[0].text == "hi"
    assert contents[1].parts[0].text == "hello"


def test_serialize_tool_use_and_tool_result_round_trip() -> None:
    """Mirrors the exact 3-message shape
    orchestrator.service.generate_tool_result_reply constructs."""
    messages = [
        {"role": "user", "content": "what's the weather"},
        {"role": "assistant", "content": [TextBlock(text="let me check"), ToolUseBlock(id="call_1", name="get_weather", input={"location": "Cairo"})]},
        {"role": "user", "content": [ToolResultBlock(tool_use_id="call_1", content='{"temp": 25}')]},
    ]
    contents = gemini_service._serialize_messages_for_gemini(messages)
    assert contents[1].parts[1].function_call.name == "get_weather"
    assert contents[1].parts[1].function_call.args == {"location": "Cairo"}
    assert contents[2].parts[0].function_response.name == "get_weather"
    assert contents[2].parts[0].function_response.response == {"result": '{"temp": 25}'}


def test_tool_result_error_wraps_under_error_key() -> None:
    messages = [
        {"role": "assistant", "content": [ToolUseBlock(id="call_1", name="get_weather", input={})]},
        {"role": "user", "content": [ToolResultBlock(tool_use_id="call_1", content="provider unavailable", is_error=True)]},
    ]
    contents = gemini_service._serialize_messages_for_gemini(messages)
    assert contents[1].parts[0].function_response.response == {"error": "provider unavailable"}


def test_tool_result_without_matching_tool_use_raises() -> None:
    messages = [{"role": "user", "content": [ToolResultBlock(tool_use_id="unknown_id", content="x")]}]
    with pytest.raises(ValueError, match="no matching ToolUseBlock"):
        gemini_service._serialize_messages_for_gemini(messages)


def test_translate_tools_converts_input_schema_to_parameters() -> None:
    tools = [{"name": "certify_claim", "description": "desc", "input_schema": {"type": "object", "properties": {}}}]
    translated = gemini_service._translate_tools_for_gemini(tools)
    assert len(translated) == 1
    decl = translated[0].function_declarations[0]
    assert decl.name == "certify_claim"
    assert decl.parameters.type == types.Type.OBJECT


def test_translate_tools_none_when_no_tools() -> None:
    assert gemini_service._translate_tools_for_gemini(None) is None
    assert gemini_service._translate_tools_for_gemini([]) is None


def test_translate_tool_choice_any() -> None:
    config = gemini_service._translate_tool_choice_for_gemini({"type": "any", "disable_parallel_tool_use": True})
    assert config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY


def test_translate_tool_choice_specific_tool() -> None:
    config = gemini_service._translate_tool_choice_for_gemini({"type": "tool", "name": "certify_claim"})
    assert config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY
    assert config.function_calling_config.allowed_function_names == ["certify_claim"]


def test_translate_tool_choice_none_when_absent() -> None:
    assert gemini_service._translate_tool_choice_for_gemini(None) is None


def test_translate_tool_choice_unrecognized_shape_returns_none() -> None:
    assert gemini_service._translate_tool_choice_for_gemini({"type": "something_new"}) is None


# ---- response translation --------------------------------------------------


def test_extract_response_parts_text_only() -> None:
    response = _fake_gemini_response(text="Hello there")
    text, tool_uses = gemini_service._extract_response_parts_gemini(response)
    assert text == "Hello there"
    assert tool_uses == []


def test_extract_response_parts_tool_only_generates_synthetic_id() -> None:
    response = _fake_gemini_response(function_calls=[("get_weather", {"location": "Cairo"})])
    text, tool_uses = gemini_service._extract_response_parts_gemini(response)
    assert text is None
    assert len(tool_uses) == 1
    assert tool_uses[0].name == "get_weather"
    assert tool_uses[0].input == {"location": "Cairo"}
    assert tool_uses[0].id  # non-empty, synthesized


def test_extract_response_parts_text_and_tool_use_together() -> None:
    response = _fake_gemini_response(text="checking now", function_calls=[("get_weather", {"location": "Cairo"})])
    text, tool_uses = gemini_service._extract_response_parts_gemini(response)
    assert text == "checking now"
    assert len(tool_uses) == 1


def test_extract_response_parts_empty_response_raises() -> None:
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=5, candidates_token_count=0, total_token_count=5),
    )
    with pytest.raises(ValueError, match="No text content or tool use"):
        gemini_service._extract_response_parts_gemini(response)


# ---- failure classification (REAL google.genai.errors instances) ---------


def _client_error(code: int, status: str) -> errors.ClientError:
    return errors.ClientError(code=code, response_json={"error": {"code": code, "status": status, "message": "simulated"}})


def _server_error(code: int, status: str) -> errors.ServerError:
    return errors.ServerError(code=code, response_json={"error": {"code": code, "status": status, "message": "simulated"}})


@pytest.mark.parametrize(
    "label,exc,expected_category",
    [
        ("missing_api_key", RuntimeError("GEMINI_API_KEY is not configured"), "authentication"),
        ("401_unauthenticated_code", _client_error(401, "UNAUTHENTICATED"), "authentication"),
        ("403_permission_denied_code", _client_error(403, "PERMISSION_DENIED"), "authentication"),
        ("404_not_found_code", _client_error(404, "NOT_FOUND"), "model_unavailable"),
        ("429_resource_exhausted_code", _client_error(429, "RESOURCE_EXHAUSTED"), "rate_limited"),
        ("400_invalid_argument_code", _client_error(400, "INVALID_ARGUMENT"), "invalid_request"),
        ("500_internal_code", _server_error(500, "INTERNAL"), "provider_unavailable"),
        ("503_unavailable_code", _server_error(503, "UNAVAILABLE"), "provider_unavailable"),
        # .code absent/unrecognized -> fall back to .status
        ("status_fallback_not_found", _client_error(0, "NOT_FOUND"), "model_unavailable"),
        ("status_fallback_resource_exhausted", _client_error(0, "RESOURCE_EXHAUSTED"), "rate_limited"),
        ("status_fallback_deadline_exceeded", _client_error(0, "DEADLINE_EXCEEDED"), "timeout"),
        ("status_fallback_failed_precondition", _client_error(0, "FAILED_PRECONDITION"), "invalid_request"),
        ("unrecognized_status_and_code", _client_error(0, "SOME_FUTURE_STATUS"), "unknown_provider_error"),
        ("plain_value_error", ValueError("bad response"), "unparseable_response"),
        ("plain_exception", RuntimeError("something else entirely"), "unknown_provider_error"),
    ],
)
def test_classify_gemini_failure(label, exc, expected_category) -> None:
    assert gemini_service._classify_gemini_failure(exc) == expected_category, label


def test_classify_gemini_failure_httpx_timeout() -> None:
    import httpx

    exc = httpx.TimeoutException("timed out")
    assert gemini_service._classify_gemini_failure(exc) == "timeout"


def test_classify_gemini_failure_httpx_connection_error() -> None:
    import httpx

    exc = httpx.ConnectError("connection refused")
    assert gemini_service._classify_gemini_failure(exc) == "connection"


def test_ambiguous_429_documented_not_guessed_as_billing() -> None:
    """The one honestly-documented ambiguity (section 10): an
    undifferentiated 429 maps to rate_limited, never billing_or_credits
    — this test exists so that choice stays a deliberate, asserted
    decision, not an accident future code could silently flip."""
    assert gemini_service._classify_gemini_failure(_client_error(429, "RESOURCE_EXHAUSTED")) == "rate_limited"


# ---- cost estimation --------------------------------------------------------


def test_estimate_gemini_cost_known_model() -> None:
    cost = gemini_service.estimate_gemini_cost("gemini-3.1-flash-lite", 1_000_000, 1_000_000)
    assert cost == Decimal("0.25") + Decimal("1.50")


def test_estimate_gemini_cost_unknown_model_returns_none() -> None:
    assert gemini_service.estimate_gemini_cost("some-future-model", 100, 100) is None


# ---- the explicit, isolated, untraced invocation path (section 20) -------


def test_generate_with_explicit_gemini_model_requires_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_service.settings, "gemini_api_key", None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        gemini_service.generate_with_explicit_gemini_model("gemini-3.1-flash-lite", [{"role": "user", "content": "hi"}])


def test_generate_with_explicit_gemini_model_returns_plain_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_service.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(gemini_service, "_call_gemini", lambda *a, **k: _fake_gemini_response(text="hello"))

    text, tool_uses, prompt_tokens, completion_tokens, latency_ms = gemini_service.generate_with_explicit_gemini_model(
        "gemini-3.1-flash-lite", [{"role": "user", "content": "hi"}]
    )
    assert text == "hello"
    assert tool_uses == []
    assert prompt_tokens == 10
    assert completion_tokens == 5
    assert latency_ms >= 0


def test_generate_with_explicit_gemini_model_makes_zero_aitrace_rows(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline-eval bypass path, mirroring evals/benchmarks/
    generation.py's own Anthropic equivalent exactly."""
    monkeypatch.setattr(gemini_service.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(gemini_service, "_call_gemini", lambda *a, **k: _fake_gemini_response(text="hello"))

    before = _trace_count(db_session)
    gemini_service.generate_with_explicit_gemini_model("gemini-3.1-flash-lite", [{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)
    assert after == before


# ---- complete_with_explicit_provider: the traced, explicit-only path -----


def test_complete_with_explicit_provider_gemini_success_records_trace(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router_service.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(gemini_service, "_call_gemini", lambda *a, **k: _fake_gemini_response(text="hello from gemini"))

    before = _trace_count(db_session)
    result = model_router_service.complete_with_explicit_provider(
        provider="google_gemini", model="gemini-3.1-flash-lite", purpose="chat_completion",
        messages=[{"role": "user", "content": "hi"}],
    )
    after = _trace_count(db_session)

    assert isinstance(result, ModelResponse)
    assert result.text == "hello from gemini"
    assert result.provider == "google_gemini"
    assert result.model == "gemini-3.1-flash-lite"

    assert after == before + 1
    trace = _latest_trace(db_session)
    assert trace.provider == "google_gemini"
    assert trace.model == "gemini-3.1-flash-lite"
    assert trace.status == "success"
    assert trace.estimated_cost_usd is not None


def test_complete_with_explicit_provider_gemini_failure_records_error_trace(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router_service.settings, "gemini_api_key", "test-key")

    def _raise(*a, **k):
        raise _client_error(429, "RESOURCE_EXHAUSTED")

    monkeypatch.setattr(gemini_service, "_call_gemini", _raise)

    with pytest.raises(model_router_service.ModelRouterError) as exc_info:
        model_router_service.complete_with_explicit_provider(
            provider="google_gemini", model="gemini-3.1-flash-lite", purpose="chat_completion",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert exc_info.value.category == "rate_limited"

    trace = _latest_trace(db_session)
    assert trace.provider == "google_gemini"
    assert trace.status == "error"
    assert trace.error_summary == "rate_limited"


def test_complete_with_explicit_provider_anthropic_still_works_unchanged(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Symmetric proof: the SAME explicit-provider function also serves
    Anthropic correctly — provider is a real, two-way parameter, not a
    Gemini-only special case bolted onto complete()."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")

    class _FakeUsage:
        input_tokens = 10
        output_tokens = 5

    class _FakeTextBlock:
        type = "text"
        text = "hi from claude"

    class _FakeMessage:
        content = [_FakeTextBlock()]
        usage = _FakeUsage()

    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda *a, **k: _FakeMessage())

    result = model_router_service.complete_with_explicit_provider(
        provider="anthropic", model="claude-sonnet-5", purpose="chat_completion",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.provider == "anthropic"
    assert result.text == "hi from claude"

    trace = _latest_trace(db_session)
    assert trace.provider == "anthropic"


def test_complete_with_explicit_provider_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        model_router_service.complete_with_explicit_provider(
            provider="openai", model="gpt-4", purpose="chat_completion", messages=[{"role": "user", "content": "hi"}]
        )


def test_complete_with_explicit_provider_never_falls_back(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """No automatic Anthropic<->Gemini fallback (section 11/J) — a
    Gemini failure must never cause an Anthropic call, and vice versa."""
    monkeypatch.setattr(model_router_service.settings, "gemini_api_key", "test-key")
    anthropic_called = {"value": False}

    def _raise(*a, **k):
        raise _client_error(500, "INTERNAL")

    def _mark_anthropic_called(*a, **k):
        anthropic_called["value"] = True
        raise AssertionError("must never be called")

    monkeypatch.setattr(gemini_service, "_call_gemini", _raise)
    monkeypatch.setattr(model_router_service, "_call_anthropic", _mark_anthropic_called)

    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete_with_explicit_provider(
            provider="google_gemini", model="gemini-3.1-flash-lite", purpose="chat_completion",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert anthropic_called["value"] is False


# ---- production stability: existing purposes are completely untouched ----


def test_complete_itself_never_imports_or_calls_gemini_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hard guarantee behind 'no production Gemini traffic exists':
    complete() must reach its result without gemini_service._call_gemini
    ever being invoked, for every one of the 4 real production purposes."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")

    def _poison(*a, **k):
        raise AssertionError("complete() must never call Gemini")

    monkeypatch.setattr(gemini_service, "_call_gemini", _poison)

    class _FakeUsage:
        input_tokens = 5
        output_tokens = 5

    class _FakeTextBlock:
        type = "text"
        text = "ok"

    class _FakeMessage:
        content = [_FakeTextBlock()]
        usage = _FakeUsage()

    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda *a, **k: _FakeMessage())

    for purpose in ("chat_completion", "tool_result_reasoning", "proactive_narration", "claim_verification"):
        result = model_router_service.complete(purpose=purpose, messages=[{"role": "user", "content": "hi"}])
        assert result.provider == "anthropic"


def test_complete_resolves_the_same_model_per_purpose_as_before_5_7() -> None:
    """Snapshot proof: every production purpose resolves to EXACTLY the
    same model string as the accepted pre-5.7 baseline — the literal
    "production stability matrix" from this checkpoint's own required
    output, expressed as a real, enforced test."""
    expected = {
        "chat_completion": "claude-sonnet-5",
        "tool_result_reasoning": "claude-sonnet-5",
        "proactive_narration": "claude-sonnet-5",
        "claim_verification": "claude-haiku-4-5",
    }
    for purpose, expected_model in expected.items():
        assert model_router_service._resolve_model(purpose) == expected_model


# ---- credential redaction ---------------------------------------------------


def test_gemini_api_key_never_appears_in_aitrace(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router_service.settings, "gemini_api_key", "secret-gemini-key-xyz")
    monkeypatch.setattr(gemini_service, "_call_gemini", lambda *a, **k: _fake_gemini_response(text="hello"))

    model_router_service.complete_with_explicit_provider(
        provider="google_gemini", model="gemini-3.1-flash-lite", purpose="chat_completion",
        messages=[{"role": "user", "content": "hi"}],
    )
    trace = _latest_trace(db_session)
    for column in ("provider", "model", "purpose", "status", "error_summary"):
        value = getattr(trace, column)
        if value:
            assert "secret-gemini-key-xyz" not in str(value)


def test_gemini_api_key_never_appears_in_error_trace_or_exception(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router_service.settings, "gemini_api_key", "secret-gemini-key-xyz")

    def _raise(*a, **k):
        raise _client_error(401, "UNAUTHENTICATED")

    monkeypatch.setattr(gemini_service, "_call_gemini", _raise)

    with pytest.raises(model_router_service.ModelRouterError) as exc_info:
        model_router_service.complete_with_explicit_provider(
            provider="google_gemini", model="gemini-3.1-flash-lite", purpose="chat_completion",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert "secret-gemini-key-xyz" not in str(exc_info.value)
    trace = _latest_trace(db_session)
    assert "secret-gemini-key-xyz" not in str(trace.error_summary)


# ---- isolation / SDK hygiene -------------------------------------------------


def test_gemini_service_module_never_imports_anthropic() -> None:
    import inspect

    import_lines = [
        line for line in inspect.getsource(gemini_service).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("anthropic" in line.lower() for line in import_lines)


def test_no_production_module_outside_model_router_references_gemini() -> None:
    """Structural proof (sections 12/27/28): Chat, Orchestrator,
    Attention, ProposedAction, Memory, and every other production
    module must have zero CODE-LEVEL awareness Gemini exists — only
    model_router/ itself (and app/config.py's own inert settings field)
    may reference it. Deliberately checks specific code-level tokens
    (gemini_service / google.genai / google_gemini / GEMINI_API_KEY),
    NOT the bare substring "gemini" — BAZRA's own identity prompt
    (orchestrator/identity.py) legitimately lists "Gemini" as one of
    several brand names in its provider-concealment instructions
    ("You never identify yourself as ... ChatGPT, Gemini, Anthropic,
    ..."), predating this checkpoint and unrelated to it; a bare
    substring search would wrongly flag that prose as a violation."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[3]
    allowed = {app_dir / "config.py"}
    needles = ("gemini_service", "google.genai", "google_gemini", "GEMINI_API_KEY", "from google import genai")
    offenders = []
    for path in app_dir.rglob("*.py"):
        if "model_router" in path.parts or path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            offenders.append(str(path))
    assert offenders == [], f"production code outside model_router/ must never reference Gemini: {offenders}"
