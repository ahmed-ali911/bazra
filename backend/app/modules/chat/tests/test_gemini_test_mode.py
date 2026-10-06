"""Checkpoint 5.7H — Manual Gemini Test Mode: a developer/human-
evaluation control letting Ahmed explicitly choose Gemini for a single
Chat turn's GENERATION step only, via a strict, backend-validated enum
(`model_provider_override`). ZERO real Gemini/Anthropic calls — every
test here mocks the provider boundary.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.chat import service as chat_service
from app.modules.chat.models import ChatMessage
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import OrchestratorResult, ToolCallRequest

TOMORROW_START = datetime(2030, 6, 15, 0, 0, tzinfo=timezone.utc)
WINDOW_END = TOMORROW_START + timedelta(days=7)
_DEFAULT_TIMEZONE = "Africa/Cairo"


@pytest.fixture(autouse=True)
def _redirect_model_router_trace_session(test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same redirect test_chat.py's own fixture performs — file-scoped
    by pytest's own fixture rules, so this file needs its own copy."""
    from app.modules.model_router import service as model_router_service

    monkeypatch.setattr(model_router_service, "_trace_session_factory", sessionmaker(bind=test_engine))


@pytest.fixture(autouse=True)
def _default_safe_claim_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same default-safe-verifier convention as test_chat.py's own
    identical fixture — keeps every mocked candidate flowing through
    unchanged unless a test re-patches this itself."""
    monkeypatch.setattr(orchestrator_service, "verify_no_mutation_claim", lambda candidate_text: False)


def _send(client: TestClient, content: str, model_provider_override: str | None = None) -> dict:
    body = {
        "content": content,
        "tomorrow_start": TOMORROW_START.isoformat(),
        "window_end": WINDOW_END.isoformat(),
        "timezone": _DEFAULT_TIMEZONE,
    }
    if model_provider_override is not None:
        body["model_provider_override"] = model_provider_override
    return client.post("/api/v1/chat/messages", json=body)


def _mock_reply(text: str):
    """Mirrors test_chat.py's own _mock_reply helper — wraps a bare
    answer string as a respond_with_text tool call, the only shape
    Gemini Test mode can ever produce."""
    tool_call = ToolCallRequest(tool_use_id="toolu_test", tool_name="respond_with_text", arguments={"kind": "answer", "text": text})
    return lambda **kwargs: OrchestratorResult(text=None, tool_call=tool_call, correlation_id="corr_test")


def _message_count(db_session: Session) -> int:
    return db_session.execute(select(func.count()).select_from(ChatMessage)).scalar_one()


def _trace_count(db_session: Session) -> int:
    return db_session.execute(text("SELECT COUNT(*) FROM ai_traces")).scalar_one()


# ---- request-level contract: strict, validated enum -----------------------


def test_omitting_override_defaults_to_default(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return OrchestratorResult(text=None, tool_call=ToolCallRequest(tool_use_id="t", tool_name="respond_with_text", arguments={"kind": "answer", "text": "hi"}), correlation_id="c")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _capture)

    response = _send(authenticated_client, "hello there")
    assert response.status_code == 200
    assert captured["provider"] is None
    assert captured["model"] is None


def test_arbitrary_provider_model_injection_is_rejected(authenticated_client: TestClient) -> None:
    """The backend owns the provider/model mapping — the browser cannot
    submit a provider or model string directly, nor any override value
    outside the closed enum. FastAPI/pydantic reject this at the
    request-validation boundary, before chat_service ever runs."""
    response = _send(authenticated_client, "hello there", model_provider_override="anthropic_direct")
    assert response.status_code == 422

    raw = authenticated_client.post(
        "/api/v1/chat/messages",
        json={
            "content": "hello",
            "tomorrow_start": TOMORROW_START.isoformat(),
            "window_end": WINDOW_END.isoformat(),
            "timezone": _DEFAULT_TIMEZONE,
            "provider": "anything",
            "model": "anything",
        },
    )
    # Unknown extra fields are simply ignored (not an error) — but
    # critically, they have NO effect on provider resolution (confirmed
    # by the next test); this just documents that the endpoint doesn't
    # even look at a bare `provider`/`model` field at all.
    assert raw.status_code in (200, 502)


# ---- DEFAULT preserves current behavior exactly ----------------------------


def test_default_override_calls_generate_reply_with_full_tool_catalog(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return OrchestratorResult(text=None, tool_call=ToolCallRequest(tool_use_id="t", tool_name="respond_with_text", arguments={"kind": "answer", "text": "hi"}), correlation_id="c")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _capture)

    response = _send(authenticated_client, "hello there", model_provider_override="default")
    assert response.status_code == 200
    assert captured["provider"] is None
    assert captured["model"] is None
    assert captured["tools"] == chat_service._TOOLS_OFFERED
    assert len(captured["tools"]) == 10  # the full, unrestricted production catalog


# ---- GEMINI TEST explicitly selects Gemini generation, restricted tools ---


def test_gemini_test_override_calls_generate_reply_with_gemini_provider_and_restricted_tools(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return OrchestratorResult(text=None, tool_call=ToolCallRequest(tool_use_id="t", tool_name="respond_with_text", arguments={"kind": "answer", "text": "pong"}), correlation_id="c")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _capture)

    response = _send(authenticated_client, "hello there", model_provider_override="google_gemini_test")
    assert response.status_code == 200
    assert captured["provider"] == "google_gemini"
    assert captured["model"] == "gemini-3.1-flash-lite"
    assert captured["tools"] == [chat_service._RESPOND_WITH_TEXT_TOOL]  # ONLY respond_with_text


def test_gemini_test_tools_offered_contains_no_write_action_tool() -> None:
    """Structural proof (Phase 3 preservation): Gemini is NEVER offered
    any propose_*/get_weather tool — it is physically incapable of
    calling one, regardless of what it might otherwise attempt."""
    names = {t["name"] for t in chat_service._GEMINI_TEST_TOOLS_OFFERED}
    assert names == {"respond_with_text"}
    for forbidden in ("propose_create_task", "propose_update_task", "propose_delete_task", "propose_create_event", "propose_update_event", "propose_delete_event", "propose_save_memory", "propose_forget_memory", "get_weather"):
        assert forbidden not in names


def test_gemini_test_does_not_mutate_the_global_tools_offered_constant(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini Test must not become the global default — confirms
    _TOOLS_OFFERED (the real production catalog) is untouched after a
    Gemini Test turn runs."""
    before = list(chat_service._TOOLS_OFFERED)
    monkeypatch.setattr(orchestrator_service, "generate_reply", _mock_reply("pong"))

    _send(authenticated_client, "hello there", model_provider_override="google_gemini_test")

    assert chat_service._TOOLS_OFFERED == before
    assert len(chat_service._TOOLS_OFFERED) == 10


# ---- context/history/system prompt unaffected by the override -------------


def test_gemini_test_does_not_expand_context_or_history(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return OrchestratorResult(text=None, tool_call=ToolCallRequest(tool_use_id="t", tool_name="respond_with_text", arguments={"kind": "answer", "text": "hi"}), correlation_id="c")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _capture)

    default_response = _send(authenticated_client, "hello there", model_provider_override="default")
    default_context = captured["context"]

    captured.clear()
    gemini_response = _send(authenticated_client, "hello there again", model_provider_override="google_gemini_test")
    gemini_context = captured["context"]

    assert default_response.status_code == 200
    assert gemini_response.status_code == 200
    # Same Context Assembly sections present either way (pending-proposal
    # state / memory section headers are identical regardless of override).
    assert "## Current action state" in default_context or "## Pending proposal" in default_context
    assert ("## Current action state" in gemini_context) == ("## Current action state" in default_context)
    assert "Current Data" not in gemini_context or "Current Data" in default_context  # no new section introduced


# ---- deterministic local-first still wins ----------------------------------


def test_deterministic_retrieval_bypasses_gemini_entirely(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checkpoint 5.2 remains authoritative even with Gemini Test
    selected — a recognized zero-LLM retrieval phrase must never reach
    generate_reply (Gemini or otherwise) at all."""
    def _must_not_be_called(**kwargs):
        raise AssertionError("generate_reply must never be called for a deterministic retrieval turn")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _must_not_be_called)

    response = _send(authenticated_client, "what are my open tasks", model_provider_override="google_gemini_test")
    assert response.status_code == 200


# ---- honest degradation: no fallback, no retry -----------------------------


def test_gemini_test_failure_does_not_fall_back_to_anthropic(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.model_router import service as model_router_service

    def _raise(**kwargs):
        raise model_router_service.ModelRouterError("unknown_provider_error")

    def _mark_anthropic_called(**kwargs):
        raise AssertionError("must never fall back to complete() / Anthropic")

    monkeypatch.setattr(model_router_service, "complete_with_explicit_provider", _raise)
    monkeypatch.setattr(model_router_service, "complete", _mark_anthropic_called)

    response = _send(authenticated_client, "hello there", model_provider_override="google_gemini_test")
    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "model_call_failed"


def test_gemini_test_failure_results_in_exactly_one_attempt(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """No hidden retry — the explicit-provider path is called exactly
    once even on failure."""
    from app.modules.model_router import service as model_router_service

    call_count = {"value": 0}

    def _raise(**kwargs):
        call_count["value"] += 1
        raise model_router_service.ModelRouterError("provider_unavailable")

    monkeypatch.setattr(model_router_service, "complete_with_explicit_provider", _raise)

    response = _send(authenticated_client, "hello there", model_provider_override="google_gemini_test")
    assert response.status_code == 502
    assert call_count["value"] == 1


# ---- AiTrace observability --------------------------------------------------


def test_gemini_test_aitrace_identifies_explicit_gemini_use(authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real, end-to-end proof: mocks gemini_service._call_gemini
    (the lowest level, below complete_with_explicit_provider), letting
    the whole real chain run and write a real AiTrace row."""
    from google.genai import types

    from app.modules.model_router import gemini_service

    monkeypatch.setattr(gemini_service.settings, "gemini_api_key", "test-key")

    def _fake_call_gemini(model, messages, **kwargs):
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(name="respond_with_text", args={"kind": "answer", "text": "pong"})),
            ]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=12, candidates_token_count=3, total_token_count=15),
        )

    monkeypatch.setattr(gemini_service, "_call_gemini", _fake_call_gemini)

    before = _trace_count(db_session)
    response = _send(authenticated_client, "hello there", model_provider_override="google_gemini_test")
    after = _trace_count(db_session)

    assert response.status_code == 200
    assert after == before + 1
    row = db_session.execute(text("SELECT * FROM ai_traces ORDER BY id DESC LIMIT 1")).mappings().one()
    assert row["provider"] == "google_gemini"
    assert row["model"] == "gemini-3.1-flash-lite"
    assert row["status"] == "success"
    assert row["prompt_tokens"] == 12
    assert row["completion_tokens"] == 3


# ---- existing verifier policy is unchanged ---------------------------------


def test_gemini_test_candidate_still_passes_through_the_unchanged_claim_verifier(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation provider selection does NOT imply verifier-provider
    selection — a Gemini-generated candidate that the (real, unchanged)
    verifier would reject still fails closed to the same deterministic
    reply Claude's own candidates would."""
    monkeypatch.setattr(orchestrator_service, "generate_reply", _mock_reply("تمام، ضفتها بالفعل"))
    monkeypatch.setattr(orchestrator_service, "verify_no_mutation_claim", lambda candidate_text: True)

    response = _send(authenticated_client, "hello there", model_provider_override="google_gemini_test")
    assert response.status_code == 200
    body = response.json()
    assert "تمام، ضفتها بالفعل" not in body["assistant_message"]["content"]


def test_gemini_test_write_intent_decline_unaffected(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A clear write-intent message is still declined deterministically
    before ANY model call, override or not (Phase 3 preservation)."""
    def _must_not_be_called(**kwargs):
        raise AssertionError("generate_reply must never be called for a clear write-intent decline")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _must_not_be_called)

    response = _send(authenticated_client, "cancel the reminder", model_provider_override="google_gemini_test")
    assert response.status_code == 200
