import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.model_router import service as model_router_service
from app.modules.model_router.models import AiTrace
from app.modules.model_router.schemas import ModelResponse


@pytest.fixture(autouse=True)
def _redirect_trace_session(test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file must write AiTrace rows to the isolated
    bazra_test database — _trace_session_factory defaults to the app's
    real SessionLocal (the real dev database), which every other test in
    this suite is redirected away from only via the get_db dependency
    override. complete() bypasses get_db entirely by design (that's the
    whole point of its independent-durability guarantee), so it needs
    its own redirect here instead.
    """
    monkeypatch.setattr(model_router_service, "_trace_session_factory", sessionmaker(bind=test_engine))


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeMessage:
    """Stands in for anthropic.types.Message — has both .usage and text
    .content, i.e. a fully well-formed response."""

    def __init__(self, text: str, input_tokens: int = 10, output_tokens: int = 20):
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeMessageNoUsage:
    """Well-formed content, but no .usage attribute at all — simulates a
    response so malformed that even token counts can't be read."""

    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeMessageNoText:
    """Valid .usage, but no text content blocks — simulates the case
    where the provider call succeeded and consumed real tokens, but the
    response body couldn't be turned into displayable text."""

    def __init__(self, input_tokens: int = 15, output_tokens: int = 25):
        self.content = []
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeToolUseBlock:
    def __init__(self, name: str, input: dict, id: str = "toolu_test"):
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input


class _FakeMessageToolOnly:
    """A pure tool-call response, no accompanying text — a valid,
    expected shape once tools are offered (Checkpoint 3.3), not an
    error the way _FakeMessageNoText's genuinely empty response is."""

    def __init__(self, tool_name: str, tool_input: dict, input_tokens: int = 30, output_tokens: int = 15, id: str = "toolu_test"):
        self.content = [_FakeToolUseBlock(tool_name, tool_input, id=id)]
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeMessageTextAndToolUse:
    """Both text and a tool_use block together — the common real shape:
    the model explains what it's proposing AND calls the tool."""

    def __init__(self, text: str, tool_name: str, tool_input: dict, input_tokens: int = 30, output_tokens: int = 20):
        self.content = [_FakeTextBlock(text), _FakeToolUseBlock(tool_name, tool_input)]
        self.usage = _FakeUsage(input_tokens, output_tokens)


def _trace_count(db_session: Session) -> int:
    return db_session.execute(select(func.count()).select_from(AiTrace)).scalar_one()


def _latest_trace(db_session: Session) -> AiTrace:
    return db_session.execute(select(AiTrace).order_by(AiTrace.id.desc()).limit(1)).scalar_one()


def _raise(exc: Exception):
    def _fn(*args, **kwargs):
        raise exc

    return _fn


# ---- the three success/failure boundaries ---------------------------------


def test_complete_success_records_exactly_one_success_trace(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("Hello there", 10, 20))

    before = _trace_count(db_session)
    result = model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert isinstance(result, ModelResponse)
    assert result.text == "Hello there"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 20

    assert after == before + 1
    trace = _latest_trace(db_session)
    assert trace.status == "success"
    assert trace.provider == "anthropic"
    assert trace.purpose == "chat_completion"
    assert trace.latency_ms >= 0
    assert trace.prompt_tokens == 10
    assert trace.completion_tokens == 20
    assert trace.estimated_cost_usd is not None
    assert trace.estimated_cost_usd >= 0
    assert trace.error_summary is None


def test_complete_passes_system_prompt_through_to_the_provider_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.2's additive change: system is Anthropic's own
    top-level parameter, not a role inside messages — confirms it
    actually reaches _call_anthropic rather than being silently dropped.
    """
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    captured = {}

    def _capture(model, messages, **kwargs):
        captured["system"] = kwargs.get("system")
        return _FakeMessage("Hello there", 10, 20)

    monkeypatch.setattr(model_router_service, "_call_anthropic", _capture)

    model_router_service.complete(
        purpose="chat_completion",
        messages=[{"role": "user", "content": "hi"}],
        system="You are a helpful assistant.",
    )

    assert captured["system"] == "You are a helpful assistant."


def test_complete_passes_tool_choice_through_to_the_provider_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.23: tool_choice is additive and purpose-agnostic —
    same "prove it reaches _call_anthropic" discipline as system's own
    test above. This module never inspects or builds the dict itself,
    just forwards whatever the caller (Orchestrator) supplies."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    captured = {}

    def _capture(model, messages, **kwargs):
        captured["tool_choice"] = kwargs.get("tool_choice")
        return _FakeMessageToolOnly("respond_with_text", {"kind": "answer", "text": "hi"})

    monkeypatch.setattr(model_router_service, "_call_anthropic", _capture)

    model_router_service.complete(
        purpose="chat_completion",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "respond_with_text"}],
        tool_choice={"type": "any", "disable_parallel_tool_use": True},
    )

    assert captured["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}


def test_complete_omitted_tool_choice_preserves_existing_call_behavior(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that never passes tool_choice (every purpose except the
    primary chat call, and that call itself whenever no tools are
    offered) must see byte-for-byte the same kwargs as before this
    checkpoint — None reaches _call_anthropic, which itself omits the
    key entirely (see the dedicated _call_anthropic-level tests below)."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    captured = {}

    def _capture(model, messages, **kwargs):
        captured.update(kwargs)
        return _FakeMessage("Hello there", 10, 20)

    monkeypatch.setattr(model_router_service, "_call_anthropic", _capture)

    model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])

    assert captured.get("tool_choice") is None


def test_tool_result_reasoning_call_never_receives_tool_choice(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.23 — the interpretive-weather continuation must
    remain byte-for-byte unaffected: tools=None, no tool_choice, ever,
    regardless of what the primary chat call now does. This test calls
    complete() directly with the exact shape generate_tool_result_reply
    itself uses (tools=None, no tool_choice kwarg at all)."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    captured = {}

    def _capture(model, messages, **kwargs):
        captured.update(kwargs)
        return _FakeMessage("some reasoning text", 10, 20)

    monkeypatch.setattr(model_router_service, "_call_anthropic", _capture)

    model_router_service.complete(
        purpose="tool_result_reasoning",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
    )

    assert captured.get("tools") is None
    assert captured.get("tool_choice") is None


def test_complete_exposes_stop_reason_on_model_response(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.22/3.23: stop_reason is read straight off the raw
    provider response and exposed on ModelResponse for Orchestrator's
    own defensive cross-check — never persisted to AiTrace (see this
    file's own absence tests elsewhere for that guarantee, unchanged)."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")

    class _FakeMessageWithStopReason(_FakeMessageToolOnly):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stop_reason = "tool_use"

    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda model, messages, **kwargs: _FakeMessageWithStopReason("respond_with_text", {"kind": "answer", "text": "hi"}),
    )

    response = model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    assert response.stop_reason == "tool_use"


def test_complete_stop_reason_defaults_to_none_when_absent_from_the_raw_response(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every pre-3.23 fake in this file (_FakeMessage etc.) has no
    stop_reason attribute at all — confirms getattr's own safe default
    keeps all of them working unchanged, never raising."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("hi", 5, 5))

    response = model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    assert response.stop_reason is None


class _CapturingMessages:
    def __init__(self, captured: dict, response):
        self._captured = captured
        self._response = response

    def create(self, **kwargs):
        self._captured.update(kwargs)
        return self._response


class _CapturingClient:
    def __init__(self, captured: dict, response):
        self.messages = _CapturingMessages(captured, response)


def test_call_anthropic_omits_tool_choice_key_entirely_when_not_supplied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checkpoint 3.23 — tool_choice=None must NOT add a literal
    "tool_choice": None key to the real SDK kwargs, omitted entirely,
    the exact same "additive, no request-shape change for existing
    callers" discipline tools/system already follow (see
    _call_anthropic's own docstring). Tests _call_anthropic directly,
    bypassing complete()'s own bookkeeping, against a fake client that
    captures the literal kwargs .messages.create() would receive."""
    captured: dict = {}
    monkeypatch.setattr(
        model_router_service, "_get_client",
        lambda: _CapturingClient(captured, _FakeMessage("hi", 5, 5)),
    )
    model_router_service._call_anthropic("claude-sonnet-5", [{"role": "user", "content": "hi"}])
    assert "tool_choice" not in captured


def test_call_anthropic_forwards_tool_choice_dict_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dict is forwarded exactly as given — this module never
    inspects, validates, or reconstructs it (see _call_anthropic's own
    docstring on staying BAZRA-action-agnostic)."""
    captured: dict = {}
    monkeypatch.setattr(
        model_router_service, "_get_client",
        lambda: _CapturingClient(captured, _FakeMessageToolOnly("respond_with_text", {"kind": "answer", "text": "hi"})),
    )
    model_router_service._call_anthropic(
        "claude-sonnet-5", [{"role": "user", "content": "hi"}],
        tools=[{"name": "respond_with_text"}], tool_choice={"type": "any", "disable_parallel_tool_use": True},
    )
    assert captured["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}


def test_provider_call_failure_records_exactly_one_error_trace(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", _raise(RuntimeError("simulated provider failure")))

    before = _trace_count(db_session)
    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert after == before + 1
    trace = _latest_trace(db_session)
    assert trace.status == "error"
    assert trace.latency_ms >= 0
    assert trace.prompt_tokens is None
    assert trace.completion_tokens is None
    assert trace.estimated_cost_usd is None
    assert trace.error_summary is not None


def test_missing_api_key_records_error_trace_without_calling_provider(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", None)
    call_count = 0

    def _track(model, messages, **kwargs):
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(model_router_service, "_call_anthropic", _track)

    before = _trace_count(db_session)
    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert call_count == 0
    assert after == before + 1
    trace = _latest_trace(db_session)
    assert trace.status == "error"
    assert trace.error_summary == "missing_api_key"


def test_response_parsing_failure_when_usage_unreadable_records_exactly_one_error_trace_with_null_tokens(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly one row, not two — the specific regression case for the
    original bug: a successful provider call whose parsing then fails
    must not be recorded once as success and again as error."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessageNoUsage("some text"))

    before = _trace_count(db_session)
    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert after == before + 1  # exactly one row — never a success trace plus a separate error trace
    trace = _latest_trace(db_session)
    assert trace.status == "error"
    assert trace.prompt_tokens is None
    assert trace.completion_tokens is None
    assert trace.estimated_cost_usd is None


def test_parsing_failure_preserves_token_usage_and_cost_when_usage_was_readable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider call DID consume real tokens at real cost before
    text extraction failed — that data must be preserved, not discarded
    as NULL, since it's genuinely available."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessageNoText(15, 25))

    before = _trace_count(db_session)
    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert after == before + 1
    trace = _latest_trace(db_session)
    assert trace.status == "error"
    assert trace.prompt_tokens == 15
    assert trace.completion_tokens == 25
    assert trace.estimated_cost_usd is not None
    assert trace.estimated_cost_usd >= 0


# ---- cost estimation and trace persistence each get their own boundary -----


def test_cost_estimation_failure_does_not_prevent_success_trace_or_response(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("Hello", 10, 20))
    monkeypatch.setattr(model_router_service, "_estimate_cost", _raise(RuntimeError("bad rate table")))

    before = _trace_count(db_session)
    result = model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert isinstance(result, ModelResponse)
    assert result.text == "Hello"

    assert after == before + 1
    trace = _latest_trace(db_session)
    assert trace.status == "success"
    assert trace.prompt_tokens == 10
    assert trace.completion_tokens == 20
    assert trace.estimated_cost_usd is None


def test_trace_write_failure_does_not_mask_a_successful_model_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("Hello", 10, 20))
    monkeypatch.setattr(model_router_service, "_record_trace", _raise(RuntimeError("simulated DB unavailability")))

    before = _trace_count(db_session)
    result = model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert isinstance(result, ModelResponse)
    assert result.text == "Hello"
    assert after == before  # the write genuinely failed — no row, but the caller still got their result


def test_trace_write_failure_during_error_path_does_not_raise_a_second_exception(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", _raise(RuntimeError("simulated provider failure")))
    monkeypatch.setattr(model_router_service, "_record_trace", _raise(RuntimeError("simulated DB unavailability")))

    before = _trace_count(db_session)
    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])
    after = _trace_count(db_session)

    assert after == before  # trace write failed too — no row, but ModelRouterError (not some other exception) still surfaced


# ---- independent durability -------------------------------------------------


def test_error_trace_survives_caller_transaction_rollback(
    db_session: Session, test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The centerpiece test: the trace write must survive a rollback of
    a transaction it was never part of. db_session stands in for the
    broader Chat/Orchestrator request's own session; a THIRD, separate
    session (not db_session) confirms durability afterward.
    """
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", _raise(RuntimeError("simulated provider failure")))

    fresh_session = sessionmaker(bind=test_engine)()
    try:
        before = fresh_session.execute(select(func.count()).select_from(AiTrace)).scalar_one()
    finally:
        fresh_session.close()

    # Simulate the caller's broader in-flight transaction doing other work.
    db_session.execute(text("SELECT 1"))

    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])

    # Simulate the caller's own request failing afterward and rolling
    # back ITS transaction — must not affect the already-committed trace.
    db_session.rollback()

    fresh_session = sessionmaker(bind=test_engine)()
    try:
        after = fresh_session.execute(select(func.count()).select_from(AiTrace)).scalar_one()
    finally:
        fresh_session.close()

    assert after == before + 1


# ---- no leaked content -------------------------------------------------------


def test_no_prompt_or_response_text_or_api_key_in_any_trace_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "MARKER_SECRET_PROMPT_XYZ123"
    fake_key = "sk-ant-fake-marker-key-999"
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", fake_key)

    monkeypatch.setattr(
        model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage(f"response containing {marker}", 5, 5)
    )
    model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": marker}])

    monkeypatch.setattr(model_router_service, "_call_anthropic", _raise(RuntimeError(f"failure touching {marker}")))
    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": marker}])

    rows = db_session.execute(text("SELECT * FROM ai_traces")).mappings().all()
    assert len(rows) >= 2
    for row in rows:
        for value in row.values():
            assert marker not in str(value)
            assert fake_key not in str(value)


# ---- purpose validation -------------------------------------------------------


def test_unknown_purpose_rejected_without_any_trace_or_provider_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def _track(model, messages, **kwargs):
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(model_router_service, "_call_anthropic", _track)

    before = _trace_count(db_session)
    with pytest.raises(ValueError):
        model_router_service.complete(purpose="not_a_real_purpose", messages=[])  # type: ignore[arg-type]
    after = _trace_count(db_session)

    assert call_count == 0
    assert after == before


# ---- Checkpoint 3.3: tools passthrough and structured tool-call extraction --------


def test_complete_passes_tools_through_to_the_provider_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    captured = {}

    def _capture(model, messages, **kwargs):
        captured["tools"] = kwargs.get("tools")
        return _FakeMessage("Hello", 10, 20)

    monkeypatch.setattr(model_router_service, "_call_anthropic", _capture)

    tools = [{"name": "propose_create_task", "description": "...", "input_schema": {}}]
    model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}], tools=tools)

    assert captured["tools"] == tools


def test_complete_with_tool_only_response_returns_none_text_and_populated_tool_uses(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        model_router_service,
        "_call_anthropic",
        lambda model, messages, **kwargs: _FakeMessageToolOnly("propose_create_task", {"title": "Call Hussein"}),
    )

    result = model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": "add a task"}], tools=[{"name": "propose_create_task"}]
    )

    assert result.text is None
    assert len(result.tool_uses) == 1
    assert result.tool_uses[0].name == "propose_create_task"
    assert result.tool_uses[0].input == {"title": "Call Hussein"}

    trace = _latest_trace(db_session)
    assert trace.status == "success"
    assert trace.prompt_tokens == 30
    assert trace.completion_tokens == 15


def test_complete_with_text_and_tool_use_together_returns_both(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        model_router_service,
        "_call_anthropic",
        lambda model, messages, **kwargs: _FakeMessageTextAndToolUse(
            "I'll add that task.", "propose_create_task", {"title": "Call Hussein"}
        ),
    )

    result = model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": "add a task"}], tools=[{"name": "propose_create_task"}]
    )

    assert result.text == "I'll add that task."
    assert len(result.tool_uses) == 1
    assert result.tool_uses[0].name == "propose_create_task"


def test_complete_without_tools_offered_is_unaffected_by_the_tool_extraction_change(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing 3.2 behavior, re-confirmed: no tools offered -> tool_uses
    is always empty, text always populated exactly as before."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("plain answer", 5, 5))

    result = model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])

    assert result.text == "plain answer"
    assert result.tool_uses == []


# ---- Checkpoint 3.8: tool_use.id, structured content, correlation_id -----------


def test_tool_use_id_is_preserved_from_the_provider_response(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda model, messages, **kwargs: _FakeMessageToolOnly(
            "get_weather", {"location": "Cairo"}, id="toolu_specific_id"
        ),
    )

    result = model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": "weather?"}], tools=[{"name": "get_weather"}]
    )

    assert result.tool_uses[0].id == "toolu_specific_id"


def test_complete_generates_a_correlation_id_when_none_is_supplied(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("hi", 5, 5))

    result = model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])

    assert result.correlation_id
    assert isinstance(result.correlation_id, str)

    trace = _latest_trace(db_session)
    assert trace.correlation_id == result.correlation_id


def test_complete_reuses_a_caller_supplied_correlation_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one real consumer: a tool_result_reasoning continuation
    reusing its initiating chat_completion call's own correlation_id —
    proving complete() honors an explicit input rather than always
    generating a fresh one."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("hi", 5, 5))

    result = model_router_service.complete(
        purpose="tool_result_reasoning", messages=[{"role": "user", "content": "hi"}], correlation_id="corr_reused_123",
    )

    assert result.correlation_id == "corr_reused_123"
    trace = _latest_trace(db_session)
    assert trace.correlation_id == "corr_reused_123"


def test_correlation_id_contains_no_user_or_tool_content(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cheap but real guard: the generated id must never embed the
    purpose string, message content, or anything else recognizable —
    it is meant to be an opaque, content-free grouping key."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("hi", 5, 5))

    marker = "SUPER_SECRET_USER_CONTENT_MARKER"
    result = model_router_service.complete(
        purpose="chat_completion", messages=[{"role": "user", "content": marker}]
    )

    assert marker not in result.correlation_id
    assert "chat_completion" not in result.correlation_id


def test_error_path_also_records_the_resolved_correlation_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda model, messages, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(model_router_service.ModelRouterError):
        model_router_service.complete(
            purpose="chat_completion", messages=[{"role": "user", "content": "hi"}], correlation_id="corr_error_case",
        )

    trace = _latest_trace(db_session)
    assert trace.status == "error"
    assert trace.correlation_id == "corr_error_case"


def test_existing_ai_trace_rows_with_null_correlation_id_remain_valid(db_session: Session) -> None:
    """Additive-migration guarantee: a pre-3.8 row (no correlation_id at
    all) must still read back correctly, not be treated as invalid or
    require a backfill."""
    row = AiTrace(
        provider="anthropic", model="claude-sonnet-5", purpose="chat_completion", status="success", latency_ms=100,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    assert row.correlation_id is None


def test_serialize_messages_converts_blocks_to_anthropic_dict_shape() -> None:
    from app.modules.model_router.schemas import TextBlock, ToolResultBlock, ToolUseBlock

    messages = [
        {"role": "user", "content": "plain string unaffected"},
        {"role": "assistant", "content": [TextBlock(text="I'll check."), ToolUseBlock(id="toolu_1", name="get_weather", input={"location": "Cairo"})]},
        {"role": "user", "content": [ToolResultBlock(tool_use_id="toolu_1", content='{"temp": 20}')]},
        {"role": "user", "content": [ToolResultBlock(tool_use_id="toolu_1", content="boom", is_error=True)]},
    ]

    serialized = model_router_service._serialize_messages(messages)

    assert serialized[0] == {"role": "user", "content": "plain string unaffected"}
    assert serialized[1]["content"] == [
        {"type": "text", "text": "I'll check."},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"location": "Cairo"}},
    ]
    assert serialized[2]["content"] == [{"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"temp": 20}'}]
    assert serialized[3]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "boom", "is_error": True}
    ]


# ---- Checkpoint 3.25: purpose->model selection ------------------------------------


def test_resolve_model_uses_the_override_table_for_claim_verification() -> None:
    assert model_router_service._resolve_model("claim_verification") == "claude-haiku-4-5"


def test_resolve_model_falls_back_to_default_for_every_other_purpose() -> None:
    for purpose in ("chat_completion", "memory_extraction", "tool_result_reasoning"):
        assert model_router_service._resolve_model(purpose) == model_router_service._DEFAULT_MODEL


def test_complete_uses_the_resolved_model_for_claim_verification(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.25: complete() must actually call _resolve_model,
    not just have it exist unused — proven by checking the model
    literal that reaches _call_anthropic for this purpose."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    captured = {}

    def _capture(model, messages, **kwargs):
        captured["model"] = model
        return _FakeMessageToolOnly("certify_claim", {"claims_bazra_mutation_completed": False})

    monkeypatch.setattr(model_router_service, "_call_anthropic", _capture)

    response = model_router_service.complete(
        purpose="claim_verification",
        messages=[{"role": "user", "content": "candidate text"}],
    )

    assert captured["model"] == "claude-haiku-4-5"
    assert response.model == "claude-haiku-4-5"


def test_complete_still_uses_default_model_for_chat_completion(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: adding the purpose->model override must not change
    model selection for any purpose that predates Checkpoint 3.25."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    captured = {}

    def _capture(model, messages, **kwargs):
        captured["model"] = model
        return _FakeMessage("hi", 5, 5)

    monkeypatch.setattr(model_router_service, "_call_anthropic", _capture)

    model_router_service.complete(purpose="chat_completion", messages=[{"role": "user", "content": "hi"}])

    assert captured["model"] == "claude-sonnet-5"


def test_claim_verification_cost_estimation_uses_the_existing_haiku_rate_table_entry(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolved model string ("claude-haiku-4-5") must match the
    existing _COST_PER_MILLION_TOKENS_USD key exactly, or cost
    estimation silently degrades to None — confirms no drift between
    the two tables."""
    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        model_router_service, "_call_anthropic",
        lambda model, messages, **kwargs: _FakeMessageToolOnly("certify_claim", {"claims_bazra_mutation_completed": False}),
    )

    response = model_router_service.complete(
        purpose="claim_verification", messages=[{"role": "user", "content": "candidate"}],
    )

    row = db_session.execute(
        text("SELECT estimated_cost_usd FROM ai_traces WHERE purpose = 'claim_verification' ORDER BY id DESC LIMIT 1")
    ).mappings().first()
    assert row is not None
    assert row["estimated_cost_usd"] is not None
