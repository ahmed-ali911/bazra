"""Checkpoint 5.8B — the ONE module that executes the frozen 5.8A Stage 1
discriminator benchmark against real providers, plus the artifact
writers that turn raw evidence into the blind review / restricted
mapping / technical evidence / execution summary handoff.

Mirrors Checkpoint 5.5's own `evals.benchmarks.generation` discipline
exactly: bypasses `model_router_service.complete()` and
`complete_with_explicit_provider()` ENTIRELY (both call `_record_trace`)
in favor of the same lower-level, already-tested primitives
`generation.py` itself reuses (`_call_anthropic`/`_extract_response_parts`/
`_classify_failure` for Anthropic; the Gemini-adapter equivalents for
Gemini) — this is a structural guarantee, not a flag to remember, that
this module writes ZERO AiTrace rows and performs ZERO domain actions
(it never imports Chat's own message-sending entry point, the actions
module, or any other domain write path at all).

Importing `app/` here (chat_service's own `_TOOLS_OFFERED`, orchestrator's
own `_PRIMARY_CHAT_TOOL_CHOICE`/`_build_static_system_instructions`/
`_build_dynamic_system_context`, model_router/gemini_service's own
low-level primitives) is the SAME established exception
`evals/benchmarks/generation.py` and `evals/benchmarks/prompts.py`
already use — this module is the "real execution" module, not a
design-only one, so it is deliberately NOT covered by
test_provider_evidence_isolation.py's "no app/ import" rule.
"""

import contextlib
import json
import time
from dataclasses import dataclass

from app.modules.chat import service as chat_service
from app.modules.model_router import service as model_router_service
from app.modules.model_router import gemini_service
from app.modules.orchestrator import service as orchestrator_service

from evals.benchmarks.provider_evidence_cases import CASES_BY_ID
from evals.benchmarks.provider_evidence_checks import CHECK_REGISTRY, build_evaluation_case
from evals.benchmarks.provider_evidence_discriminator_subset import DISCRIMINATOR_SUBSET_CASE_IDS
from evals.benchmarks.provider_evidence_interpretation import classify as classify_interpretation
from evals.benchmarks.provider_evidence_ledger import AttemptLedger
from evals.benchmarks.provider_evidence_plan import PROVIDER_MODEL_CANDIDATES
from evals.benchmarks.provider_evidence_blind_review import blind_slot_order, slot_labels
from evals.benchmarks.provider_evidence_schemas import CandidateGeneration, ProviderEvidenceCase
from evals.runner import evaluate_case

# Section 3 — a frozen, synthetic "current datetime" shared by every
# case/provider/rep in this run: identical across all 24 attempts, so
# no candidate receives a different dynamic context than any other
# purely due to wall-clock timing. Not tied to any real date Ahmed
# actually cares about.
FROZEN_SYNTHETIC_DATETIME = "Current local datetime: Thursday, 2026-10-08 10:00\nTimezone: Africa/Cairo\nUTC offset: +02:00"

MAX_ATTEMPTS = 24


@dataclass(frozen=True)
class GenerationEvidence:
    provider: str
    model: str
    succeeded: bool
    text: str | None
    tool_name: str | None
    tool_arguments: dict | None
    failure_category: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    estimated_cost_usd: str | None  # Decimal serialized as str — JSON-safe, exact


@dataclass(frozen=True)
class AttemptOutcome:
    attempt_id: str
    case_id: str
    provider: str
    model: str
    rep: int
    evidence: GenerationEvidence
    hard_check_results: tuple  # tuple[evals.schemas.CheckResult, ...] — empty if generation failed
    interpretations: dict  # check_name -> interpretation bucket


def _build_request(case: ProviderEvidenceCase) -> dict:
    """The one, shared, provider-neutral request shape every candidate
    receives — built ONCE per case, reused identically for all three
    providers (section 4: 'All three candidates must receive
    semantically equivalent frozen test conditions'). Uses the REAL
    production system-prompt builders and the REAL, FULL production
    tool catalog (`chat_service._TOOLS_OFFERED` — all 9 real tools, not
    the restricted single-tool Gemini Test Mode catalog from 5.7H: that
    restriction was a live-Chat SAFETY scope decision, not a capability
    ceiling, and this benchmark's own dispatch never reaches a domain
    write path regardless of which tool a candidate selects, so
    offering the full catalog to every candidate is both safe and
    required for a fair tool-selection comparison)."""
    messages = [{"role": t.role, "content": t.content} for t in case.conversation_history]
    messages.append({"role": "user", "content": case.user_message})
    dynamic_system = orchestrator_service._build_dynamic_system_context(case.synthetic_context, FROZEN_SYNTHETIC_DATETIME)
    static_system = orchestrator_service._build_static_system_instructions()
    tools = list(chat_service._TOOLS_OFFERED)
    tool_choice = orchestrator_service._PRIMARY_CHAT_TOOL_CHOICE
    return {
        "messages": messages,
        "system": dynamic_system,
        "cacheable_system_prefix": static_system,
        "tools": tools,
        "tool_choice": tool_choice,
    }


def dispatch_real(provider: str, model: str, request: dict) -> GenerationEvidence:
    """The one real-provider call site. No AiTrace row is reachable
    from here (see module docstring) — this function exists only to
    answer 'what does THIS exact model produce for THIS exact frozen
    request,' nothing else."""
    start = time.monotonic()
    try:
        if provider == "anthropic":
            raw = model_router_service._call_anthropic(
                model, request["messages"], system=request["system"], tools=request["tools"],
                tool_choice=request["tool_choice"], cacheable_system_prefix=request["cacheable_system_prefix"],
            )
        else:
            raw = gemini_service._call_gemini(
                model, request["messages"], system=request["system"], tools=request["tools"],
                tool_choice=request["tool_choice"], max_output_tokens=None,
            )
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        category = (
            model_router_service._classify_failure(exc) if provider == "anthropic"
            else gemini_service._classify_gemini_failure(exc)
        )
        return GenerationEvidence(
            provider=provider, model=model, succeeded=False, text=None, tool_name=None, tool_arguments=None,
            failure_category=category, latency_ms=latency_ms, prompt_tokens=None, completion_tokens=None,
            cache_creation_input_tokens=0, cache_read_input_tokens=0, estimated_cost_usd=None,
        )

    latency_ms = int((time.monotonic() - start) * 1000)
    try:
        if provider == "anthropic":
            prompt_tokens = raw.usage.input_tokens
            completion_tokens = raw.usage.output_tokens
            cache_creation_input_tokens = getattr(raw.usage, "cache_creation_input_tokens", None) or 0
            cache_read_input_tokens = getattr(raw.usage, "cache_read_input_tokens", None) or 0
            text, tool_uses = model_router_service._extract_response_parts(raw)
            cost = model_router_service._safe_estimate_cost(
                model, prompt_tokens, completion_tokens, cache_creation_input_tokens, cache_read_input_tokens,
            )
        else:
            prompt_tokens = raw.usage_metadata.prompt_token_count if raw.usage_metadata else 0
            completion_tokens = raw.usage_metadata.candidates_token_count if raw.usage_metadata else 0
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0
            text, tool_uses = gemini_service._extract_response_parts_gemini(raw)
            cost = gemini_service.estimate_gemini_cost(model, prompt_tokens, completion_tokens)
    except Exception as exc:
        category = (
            model_router_service._classify_failure(exc) if provider == "anthropic"
            else gemini_service._classify_gemini_failure(exc)
        )
        return GenerationEvidence(
            provider=provider, model=model, succeeded=False, text=None, tool_name=None, tool_arguments=None,
            failure_category=category, latency_ms=latency_ms, prompt_tokens=None, completion_tokens=None,
            cache_creation_input_tokens=0, cache_read_input_tokens=0, estimated_cost_usd=None,
        )

    tool_name = tool_uses[0].name if tool_uses else None
    tool_arguments = tool_uses[0].input if tool_uses else None
    return GenerationEvidence(
        provider=provider, model=model, succeeded=True, text=text, tool_name=tool_name, tool_arguments=tool_arguments,
        failure_category=None, latency_ms=latency_ms, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens, cache_read_input_tokens=cache_read_input_tokens,
        estimated_cost_usd=str(cost) if cost is not None else None,
    )


@contextlib.contextmanager
def disabled_retry_real_dispatch():
    """Section 2: 'If a provider SDK performs automatic retries,
    explicitly disable them for this benchmark.' Monkeypatches ONLY the
    two plain, already-independently-mockable module-level client
    factories (`_get_client`/`_get_gemini_client` — the exact seam
    model_router/service.py's own docstring calls out as test-
    substitutable) for the DURATION of this context manager, restoring
    the real factories immediately after — never a permanent production
    change. Anthropic's SDK defaults to `max_retries=2`; Gemini's
    defaults to up to 5 attempts (confirmed via direct introspection of
    `google.genai.types.HttpRetryOptions`, 2026-10-08) — both explicitly
    reduced to exactly 1 attempt (no retry) here.
    """
    import anthropic as anthropic_sdk
    from app.config import settings
    from google import genai
    from google.genai import types as genai_types

    original_anthropic_factory = model_router_service._get_client
    original_gemini_factory = gemini_service._get_gemini_client

    def _no_retry_anthropic_client():
        return anthropic_sdk.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)

    def _no_retry_gemini_client():
        return genai.Client(
            api_key=settings.gemini_api_key,
            http_options=genai_types.HttpOptions(retry_options=genai_types.HttpRetryOptions(attempts=1)),
        )

    model_router_service._get_client = _no_retry_anthropic_client
    gemini_service._get_gemini_client = _no_retry_gemini_client
    try:
        yield
    finally:
        model_router_service._get_client = original_anthropic_factory
        gemini_service._get_gemini_client = original_gemini_factory


def planned_attempts(case_ids=DISCRIMINATOR_SUBSET_CASE_IDS, candidates=PROVIDER_MODEL_CANDIDATES):
    """The fixed, deterministic attempt order: case-major, provider-minor
    — 8 cases x 3 candidates x 1 rep = 24, in a stable, reproducible
    sequence. Never shuffled, never reordered based on anything
    observed during the run."""
    return [(case_id, provider, model, 1) for case_id in case_ids for provider, model in candidates]


def run_discriminator_benchmark(ledger: AttemptLedger, dispatch_fn=dispatch_real, max_attempts: int = MAX_ATTEMPTS) -> list[AttemptOutcome]:
    """The core orchestration loop. `dispatch_fn` is injectable so
    offline tests can pass a fake (deterministic, zero-network)
    dispatcher — see test_provider_evidence_execution.py — while real
    execution passes `dispatch_real` under `disabled_retry_real_dispatch()`.

    Enforces, by construction:
    - exactly the frozen 8x3x1=24 attempt plan, never more
    - an attempt is reserved in the ledger BEFORE dispatch_fn is called
    - an attempt_id already in the ledger (a resumed, previously-
      interrupted run) is skipped, never re-dispatched
    - the budget cap is the ledger's own enforced limit, not a separate
      counter that could drift from it
    """
    attempts = planned_attempts()
    if len(attempts) > max_attempts:
        raise ValueError(f"planned attempt count {len(attempts)} exceeds max_attempts {max_attempts}")

    outcomes: list[AttemptOutcome] = []
    for case_id, provider, model, rep in attempts:
        attempt_id = f"{case_id}:{provider}:{model}:{rep}"
        if ledger.already_attempted(attempt_id):
            continue
        ledger.reserve(attempt_id, case_id, provider, model, rep)

        case = CASES_BY_ID[case_id]
        request = _build_request(case)
        evidence = dispatch_fn(provider, model, request)

        if evidence.succeeded:
            generation = CandidateGeneration(text=evidence.text, tool_name=evidence.tool_name, tool_arguments=evidence.tool_arguments)
            evaluation_case = build_evaluation_case(case, generation)
            checks_to_run = [CHECK_REGISTRY[name] for name in case.hard_checks]
            result = evaluate_case(evaluation_case, checks_to_run)
            hard_check_results = result.checks
            interpretations = {c.name: classify_interpretation(c.name, c.passed) for c in hard_check_results}
        else:
            hard_check_results = ()
            interpretations = {}

        outcomes.append(AttemptOutcome(
            attempt_id=attempt_id, case_id=case_id, provider=provider, model=model, rep=rep,
            evidence=evidence, hard_check_results=hard_check_results, interpretations=interpretations,
        ))
    return outcomes


# ---- artifact writers ---------------------------------------------------


def write_blind_review_artifact(outcomes: list[AttemptOutcome], path: str) -> None:
    """Artifact A — safe for Ahmed to read and score BEFORE unblinding.
    No provider name, model identifier, pricing, token usage, latency,
    or any other identity-correlated metadata anywhere in this file."""
    from evals.benchmarks.provider_evidence_rubric import ALL_DIMENSIONS

    by_case: dict[str, list[AttemptOutcome]] = {}
    for outcome in outcomes:
        by_case.setdefault(outcome.case_id, []).append(outcome)

    lines = [
        "CHECKPOINT 5.8B — BLIND HUMAN REVIEW",
        "=" * 70,
        "",
        "Do not reveal or guess provider/model identity while scoring.",
        "Score ONLY the dimensions listed for each case, using the anchors below.",
        "",
        "RUBRIC (shared across every case — score 1-5 per listed dimension)",
        "-" * 70,
    ]
    for dimension in ALL_DIMENSIONS:
        lines.append(f"\n{dimension.name} — {dimension.question}")
        for anchor in dimension.anchors:
            lines.append(f"  {anchor.score}: {anchor.description}")
    lines.append("\n" + "=" * 70)

    for case_id, case_outcomes in by_case.items():
        case = CASES_BY_ID[case_id]
        provider_models = tuple(f"{o.provider}/{o.model}" for o in case_outcomes)
        order = blind_slot_order(case_id, provider_models)
        labels = slot_labels(len(order))
        by_provider_model = {f"{o.provider}/{o.model}": o for o in case_outcomes}

        lines.append(f"\nCASE: {case_id}")
        lines.append(f"  User message: {case.user_message}")
        lines.append(f"  Synthetic context: {case.synthetic_context}")
        if case.human_review_dimensions:
            lines.append(f"  Score these dimensions: {', '.join(case.human_review_dimensions)}")
        else:
            lines.append("  No frozen subjective dimensions apply to this case (deterministic-only).")
        for label, provider_model in zip(labels, order):
            outcome = by_provider_model[provider_model]
            lines.append(f"\n  {label}:")
            if not outcome.evidence.succeeded:
                lines.append("    [response unavailable — generation did not complete]")
                continue
            if outcome.evidence.text:
                lines.append(f"    Text: {outcome.evidence.text}")
            if outcome.evidence.tool_name:
                lines.append(f"    Called: {outcome.evidence.tool_name}({outcome.evidence.tool_arguments})")
        for label in labels:
            lines.append(f"  {label} scores: " + ", ".join(f"{d}=__" for d in case.human_review_dimensions) if case.human_review_dimensions else f"  {label}: (no scoring needed)")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_identity_mapping_artifact(outcomes: list[AttemptOutcome], path: str) -> None:
    """Artifact B — RESTRICTED. The blind-label -> real provider/model
    mapping, sealed until Ahmed confirms scoring is complete."""
    by_case: dict[str, list[AttemptOutcome]] = {}
    for outcome in outcomes:
        by_case.setdefault(outcome.case_id, []).append(outcome)

    mapping = {}
    for case_id, case_outcomes in by_case.items():
        provider_models = tuple(f"{o.provider}/{o.model}" for o in case_outcomes)
        order = blind_slot_order(case_id, provider_models)
        labels = slot_labels(len(order))
        mapping[case_id] = dict(zip(labels, order))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def write_technical_evidence_artifact(outcomes: list[AttemptOutcome], path: str) -> None:
    """Artifact C — RESTRICTED. Full per-attempt evidence including
    provider/model identity, tokens, cache usage, cost, latency, and
    hard-check/interpretation results. Kept isolated from the blind
    review material until Ahmed confirms scoring is complete."""
    lines = ["CHECKPOINT 5.8B — TECHNICAL EVIDENCE (RESTRICTED)", "=" * 70]
    for outcome in outcomes:
        e = outcome.evidence
        lines.append(f"\nattempt_id: {outcome.attempt_id}")
        lines.append(f"  case_id: {outcome.case_id}")
        lines.append(f"  provider/model: {e.provider}/{e.model}")
        lines.append(f"  succeeded: {e.succeeded}")
        if not e.succeeded:
            lines.append(f"  failure_category: {e.failure_category}")
        lines.append(f"  latency_ms: {e.latency_ms}")
        lines.append(f"  prompt_tokens: {e.prompt_tokens if e.prompt_tokens is not None else 'UNKNOWN'}")
        lines.append(f"  completion_tokens: {e.completion_tokens if e.completion_tokens is not None else 'UNKNOWN'}")
        lines.append(f"  cache_creation_input_tokens: {e.cache_creation_input_tokens}")
        lines.append(f"  cache_read_input_tokens: {e.cache_read_input_tokens}")
        lines.append(f"  estimated_cost_usd: {e.estimated_cost_usd if e.estimated_cost_usd is not None else 'UNKNOWN'}")
        if e.tool_name:
            lines.append(f"  tool_name: {e.tool_name}")
            lines.append(f"  tool_arguments: {e.tool_arguments}")
        for check in outcome.hard_check_results:
            interpretation = outcome.interpretations.get(check.name, "N/A")
            lines.append(f"  check[{check.name}]: passed={check.passed} reason={check.reason!r} interpretation={interpretation}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_execution_summary_artifact(outcomes: list[AttemptOutcome], path: str) -> dict:
    """Artifact D — non-identifying aggregate counts only, safe to show
    Ahmed in the visible handoff (section 12/13)."""
    attempted = len(outcomes)
    completed = sum(1 for o in outcomes if o.evidence.succeeded)
    failed = attempted - completed
    hard_pass = sum(1 for o in outcomes if o.evidence.succeeded and all(c.passed for c in o.hard_check_results if c.hard))
    hard_fail = sum(1 for o in outcomes if o.evidence.succeeded and not all(c.passed for c in o.hard_check_results if c.hard))
    summary = {
        "attempted": attempted,
        "completed": completed,
        "failed": failed,
        "hard_gate_pass": hard_pass,
        "hard_gate_fail": hard_fail,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return summary
