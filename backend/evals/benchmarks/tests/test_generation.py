import pytest

from app.modules.model_router import service as model_router_service
from evals.benchmarks.generation import generate_with_explicit_model


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text: str, input_tokens: int = 10, output_tokens: int = 5):
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage(input_tokens, output_tokens)


def test_generate_with_explicit_model_uses_the_exact_model_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model actually sent to _call_anthropic must be EXACTLY what
    was requested — no purpose-based resolution, no override."""
    captured = {}

    def _fake_call_anthropic(model, messages, system=None, **kwargs):
        captured["model"] = model
        return _FakeMessage("narration text")

    monkeypatch.setattr(model_router_service, "_call_anthropic", _fake_call_anthropic)

    outcome = generate_with_explicit_model("claude-haiku-4-5", "sys", "user msg")

    assert captured["model"] == "claude-haiku-4-5"
    assert outcome.model == "claude-haiku-4-5"
    assert outcome.text == "narration text"
    assert outcome.failure_category is None
    assert outcome.prompt_tokens == 10
    assert outcome.completion_tokens == 5


def test_generate_with_explicit_model_never_calls_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves this module bypasses complete() (and therefore its
    AiTrace-writing and purpose-based model resolution) entirely."""
    def _must_not_be_called(**kwargs):
        raise AssertionError("complete() must never be called by the benchmark's generation module")

    monkeypatch.setattr(model_router_service, "complete", _must_not_be_called)
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("ok"))

    generate_with_explicit_model("claude-sonnet-5", "sys", "user msg")  # must not raise


def test_provider_failure_is_classified_with_the_real_5_1_taxonomy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(model, messages, **kwargs):
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr(model_router_service, "_call_anthropic", _raise)

    outcome = generate_with_explicit_model("claude-sonnet-5", "sys", "user msg")

    assert outcome.text is None
    assert outcome.failure_category == "authentication"
    assert outcome.prompt_tokens is None
    assert outcome.completion_tokens is None


def test_generation_writes_zero_aitrace_rows(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import func, select, text

    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("ok"))

    before = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()
    generate_with_explicit_model("claude-sonnet-5", "sys", "user msg")
    after = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()

    assert after == before
