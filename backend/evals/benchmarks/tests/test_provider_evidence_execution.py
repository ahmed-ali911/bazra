"""Checkpoint 5.8B, section 9 — offline safety tests with FAKE provider
responses, proving every safeguard BEFORE any real request is allowed.
Zero network calls anywhere in this file.
"""

import inspect
import json
import os

import pytest

from evals.benchmarks.provider_evidence_discriminator_subset import DISCRIMINATOR_SUBSET_CASE_IDS
from evals.benchmarks.provider_evidence_execution import (
    MAX_ATTEMPTS,
    GenerationEvidence,
    disabled_retry_real_dispatch,
    planned_attempts,
    run_discriminator_benchmark,
    write_blind_review_artifact,
    write_execution_summary_artifact,
    write_identity_mapping_artifact,
    write_technical_evidence_artifact,
)
from evals.benchmarks.provider_evidence_ledger import AttemptLedger, BudgetExceededError, DuplicateAttemptError
from evals.benchmarks.provider_evidence_plan import PROVIDER_MODEL_CANDIDATES


def _fake_dispatcher(provider: str, model: str, request: dict) -> GenerationEvidence:
    return GenerationEvidence(
        provider=provider, model=model, succeeded=True,
        text="تمام، سامعك." if provider != "google_gemini" else "فهمت قصدك.",
        tool_name=None, tool_arguments=None, failure_category=None,
        latency_ms=100, prompt_tokens=1000, completion_tokens=20,
        cache_creation_input_tokens=900 if provider == "anthropic" else 0,
        cache_read_input_tokens=0, estimated_cost_usd="0.001000",
    )


def _fake_dispatcher_failing(provider: str, model: str, request: dict) -> GenerationEvidence:
    return GenerationEvidence(
        provider=provider, model=model, succeeded=False, text=None, tool_name=None, tool_arguments=None,
        failure_category="timeout", latency_ms=5000, prompt_tokens=None, completion_tokens=None,
        cache_creation_input_tokens=0, cache_read_input_tokens=0, estimated_cost_usd=None,
    )


# ---- planned attempts / budget cap --------------------------------------


def test_planned_attempts_is_exactly_24() -> None:
    assert len(planned_attempts()) == 24


def test_planned_attempts_uses_frozen_discriminator_subset_and_candidates() -> None:
    attempts = planned_attempts()
    case_ids = {a[0] for a in attempts}
    assert case_ids == set(DISCRIMINATOR_SUBSET_CASE_IDS)
    providers = {(a[1], a[2]) for a in attempts}
    assert providers == set(PROVIDER_MODEL_CANDIDATES)


def test_planned_attempts_has_identical_rep_count_per_case_per_provider() -> None:
    attempts = planned_attempts()
    reps = {a[3] for a in attempts}
    assert reps == {1}
    assert len(attempts) == len(DISCRIMINATOR_SUBSET_CASE_IDS) * len(PROVIDER_MODEL_CANDIDATES) * 1


def test_run_discriminator_benchmark_makes_exactly_24_attempts(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    outcomes = run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=24)
    assert len(outcomes) == 24
    assert ledger.total_reserved() == 24


def test_attempt_is_reserved_before_dispatch_is_called(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    call_log = []

    def _tracking_dispatcher(provider, model, request):
        attempt_id_prefix = f"{provider}:{model}"
        assert any(attempt_id_prefix in rid for rid in ledger._reserved_ids), (
            "dispatch was called before the attempt was reserved in the ledger"
        )
        call_log.append((provider, model))
        return _fake_dispatcher(provider, model, request)

    run_discriminator_benchmark(ledger, dispatch_fn=_tracking_dispatcher, max_attempts=24)
    assert len(call_log) == 24


def test_budget_cap_cannot_be_exceeded(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    with pytest.raises(ValueError):
        run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=10)


def test_ledger_refuses_to_reserve_beyond_its_own_cap(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=2)
    ledger.reserve("a:1", "a", "anthropic", "claude-sonnet-5", 1)
    ledger.reserve("a:2", "a", "anthropic", "claude-haiku-4-5", 1)
    with pytest.raises(BudgetExceededError):
        ledger.reserve("a:3", "a", "google_gemini", "gemini-3.1-flash-lite", 1)


def test_ledger_refuses_duplicate_reservation(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    ledger.reserve("a:1", "a", "anthropic", "claude-sonnet-5", 1)
    with pytest.raises(DuplicateAttemptError):
        ledger.reserve("a:1", "a", "anthropic", "claude-sonnet-5", 1)


# ---- interrupted-run protection / resumability --------------------------


def test_interrupted_run_is_not_repeated_on_resume(tmp_path) -> None:
    ledger_path = str(tmp_path / "ledger.jsonl")
    ledger1 = AttemptLedger(ledger_path, max_attempts=24)
    call_count = {"n": 0}

    def _counting_dispatcher(provider, model, request):
        call_count["n"] += 1
        return _fake_dispatcher(provider, model, request)

    # Simulate a crash after exactly 5 reservations by only letting 5
    # attempts dispatch, then abandon this ledger instance.
    attempts = planned_attempts()
    for case_id, provider, model, rep in attempts[:5]:
        attempt_id = f"{case_id}:{provider}:{model}:{rep}"
        ledger1.reserve(attempt_id, case_id, provider, model, rep)
        _counting_dispatcher(provider, model, {})
    assert call_count["n"] == 5

    # Resume: a FRESH ledger instance loaded from the SAME file must see
    # those 5 as already attempted and never re-dispatch them.
    ledger2 = AttemptLedger(ledger_path, max_attempts=24)
    assert ledger2.total_reserved() == 5
    outcomes = run_discriminator_benchmark(ledger2, dispatch_fn=_counting_dispatcher, max_attempts=24)
    assert len(outcomes) == 19  # only the remaining 19 were dispatched
    assert call_count["n"] == 5 + 19 == 24
    assert ledger2.total_reserved() == 24


# ---- no automatic retries -----------------------------------------------


def test_disabled_retry_context_manager_patches_and_restores_client_factories() -> None:
    from app.modules.model_router import gemini_service, service as model_router_service

    original_anthropic = model_router_service._get_client
    original_gemini = gemini_service._get_gemini_client

    with disabled_retry_real_dispatch():
        assert model_router_service._get_client is not original_anthropic
        assert gemini_service._get_gemini_client is not original_gemini

    assert model_router_service._get_client is original_anthropic
    assert gemini_service._get_gemini_client is original_gemini


def test_disabled_retry_restores_factories_even_on_exception() -> None:
    from app.modules.model_router import gemini_service, service as model_router_service

    original_anthropic = model_router_service._get_client
    original_gemini = gemini_service._get_gemini_client

    with pytest.raises(RuntimeError):
        with disabled_retry_real_dispatch():
            raise RuntimeError("boom")

    assert model_router_service._get_client is original_anthropic
    assert gemini_service._get_gemini_client is original_gemini


# ---- blind mapping correctness / no leakage ------------------------------


def test_blind_review_artifact_contains_no_provider_or_model_identity(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    outcomes = run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=24)
    path = str(tmp_path / "blind_review.txt")
    write_blind_review_artifact(outcomes, path)

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().lower()
    for forbidden in ("anthropic", "claude", "gemini", "google_gemini", "sonnet", "haiku", "flash-lite"):
        assert forbidden not in content, f"blind review artifact leaks identity term: {forbidden!r}"


def test_blind_review_artifact_contains_no_cost_or_token_or_latency_data(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    outcomes = run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=24)
    path = str(tmp_path / "blind_review.txt")
    write_blind_review_artifact(outcomes, path)

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().lower()
    for forbidden in ("latency_ms", "prompt_tokens", "completion_tokens", "estimated_cost", "0.001000"):
        assert forbidden not in content, f"blind review artifact leaks technical metadata: {forbidden!r}"


def test_identity_mapping_matches_blind_review_slot_count(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    outcomes = run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=24)
    mapping_path = str(tmp_path / "mapping.json")
    write_identity_mapping_artifact(outcomes, mapping_path)

    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping = json.load(f)

    assert set(mapping.keys()) == set(DISCRIMINATOR_SUBSET_CASE_IDS)
    for case_id, slots in mapping.items():
        assert len(slots) == len(PROVIDER_MODEL_CANDIDATES)
        assert set(slots.values()) == {f"{p}/{m}" for p, m in PROVIDER_MODEL_CANDIDATES}


def test_technical_evidence_artifact_contains_identity_and_cost(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    outcomes = run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=24)
    path = str(tmp_path / "technical.txt")
    write_technical_evidence_artifact(outcomes, path)

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "anthropic/claude-sonnet-5" in content
    assert "0.001000" in content
    assert "cache_creation_input_tokens" in content


def test_execution_summary_has_no_identity_breakdown(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    outcomes = run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=24)
    path = str(tmp_path / "summary.json")
    summary = write_execution_summary_artifact(outcomes, path)

    assert summary["attempted"] == 24
    assert summary["completed"] == 24
    assert summary["failed"] == 0
    assert "anthropic" not in json.dumps(summary).lower()
    assert "gemini" not in json.dumps(summary).lower()


def test_execution_summary_counts_failures_correctly(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=24)
    outcomes = run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher_failing, max_attempts=24)
    path = str(tmp_path / "summary.json")
    summary = write_execution_summary_artifact(outcomes, path)
    assert summary["attempted"] == 24
    assert summary["completed"] == 0
    assert summary["failed"] == 24


# ---- no production action execution / no domain DB writes ---------------


def test_execution_module_never_imports_domain_write_paths() -> None:
    from evals.benchmarks import provider_evidence_execution

    source = inspect.getsource(provider_evidence_execution)
    for forbidden in ("chat_service.send_message", "actions_service", "confirm_and_execute", "tasks_service.create_task", "calendar_service.create_event"):
        assert forbidden not in source, f"execution module references a domain write path: {forbidden!r}"


def test_fake_run_makes_zero_aitrace_rows(db_session) -> None:
    from sqlalchemy import func, select, text

    before = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()
    ledger = AttemptLedger(os.devnull if os.name != "nt" else "NUL", max_attempts=24)
    # os.devnull as a ledger path would fail real writes; use a real temp file instead.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ledger = AttemptLedger(os.path.join(tmp, "ledger.jsonl"), max_attempts=24)
        run_discriminator_benchmark(ledger, dispatch_fn=_fake_dispatcher, max_attempts=24)
    after = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()
    assert after == before


# ---- no personal data in the built request --------------------------------


def test_build_request_uses_only_frozen_case_fields() -> None:
    from evals.benchmarks.provider_evidence_cases import CASES_BY_ID
    from evals.benchmarks.provider_evidence_execution import _build_request

    case = CASES_BY_ID["a1_zahqan_restraint"]
    request = _build_request(case)
    assert case.user_message in str(request["messages"])
    assert "Ahmed" not in json.dumps(request["messages"], ensure_ascii=False)
