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
    def __init__(self, name: str, input: dict):
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeMessageToolOnly:
    """A pure tool-call response, no accompanying text — a valid,
    expected shape once tools are offered (Checkpoint 3.3), not an
    error the way _FakeMessageNoText's genuinely empty response is."""

    def __init__(self, tool_name: str, tool_input: dict, input_tokens: int = 30, output_tokens: int = 15):
        self.content = [_FakeToolUseBlock(tool_name, tool_input)]
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
