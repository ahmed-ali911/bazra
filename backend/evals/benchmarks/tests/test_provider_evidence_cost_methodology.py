"""Checkpoint 5.8A, section 18/19 — proves the cost methodology's own
arithmetic is internally consistent, and that its duplicated pricing
constants have not silently drifted from the real production constants.

This file (not the cost_methodology MODULE itself) is deliberately the
one place in evals/benchmarks/tests/ permitted to import app/'s pricing
constants for comparison — the exact same precedent already established
by test_prompt_fidelity.py (cross-references
app.modules.orchestrator.service for alignment) and test_generation.py
(imports app.modules.model_router.service directly). Zero real provider
calls anywhere in this file.
"""

from decimal import Decimal

from app.modules.model_router.gemini_service import _GEMINI_COST_PER_MILLION_TOKENS_USD
from app.modules.model_router.service import _COST_PER_MILLION_TOKENS_USD

from evals.benchmarks.provider_evidence_cost_methodology import (
    ANTHROPIC_COST_PER_MILLION_TOKENS_USD,
    GEMINI_COST_PER_MILLION_TOKENS_USD,
    anthropic_session_cost,
    estimate_anthropic_call_cost_cache_read,
    estimate_anthropic_call_cost_cache_write,
    estimate_anthropic_call_cost_uncached,
    estimate_gemini_call_cost,
    gemini_session_cost,
)


def test_duplicated_anthropic_pricing_matches_production() -> None:
    for model, rates in ANTHROPIC_COST_PER_MILLION_TOKENS_USD.items():
        assert model in _COST_PER_MILLION_TOKENS_USD, f"{model} not found in production pricing table"
        assert rates == _COST_PER_MILLION_TOKENS_USD[model], (
            f"{model} pricing drifted: benchmark has {rates}, production has {_COST_PER_MILLION_TOKENS_USD[model]}"
        )


def test_duplicated_gemini_pricing_matches_production() -> None:
    for model, rates in GEMINI_COST_PER_MILLION_TOKENS_USD.items():
        assert model in _GEMINI_COST_PER_MILLION_TOKENS_USD, f"{model} not found in production Gemini pricing table"
        assert rates == _GEMINI_COST_PER_MILLION_TOKENS_USD[model], (
            f"{model} pricing drifted: benchmark has {rates}, production has {_GEMINI_COST_PER_MILLION_TOKENS_USD[model]}"
        )


def test_cache_write_cost_exceeds_uncached_cost() -> None:
    """The documented 1.25x write premium must make the write estimate
    strictly more expensive than a flat uncached call — if this were
    ever false, the cache-fairness section 18 rules would be violated
    (Claude's first call would look artificially cheap)."""
    model = "claude-sonnet-5"
    assert estimate_anthropic_call_cost_cache_write(model) > estimate_anthropic_call_cost_uncached(model)


def test_cache_read_cost_is_cheaper_than_uncached_cost() -> None:
    model = "claude-sonnet-5"
    assert estimate_anthropic_call_cost_cache_read(model) < estimate_anthropic_call_cost_uncached(model)


def test_session_cost_grows_monotonically_with_turn_count() -> None:
    model = "claude-sonnet-5"
    costs = [anthropic_session_cost(model, n) for n in range(1, 6)]
    assert costs == sorted(costs)
    assert all(c2 > c1 for c1, c2 in zip(costs, costs[1:]))


def test_gemini_session_cost_is_flat_per_turn() -> None:
    model = "gemini-3.1-flash-lite"
    one = gemini_session_cost(model, 1)
    five = gemini_session_cost(model, 5)
    assert five == one * 5


def test_gemini_session_cost_zero_turns_is_zero() -> None:
    assert gemini_session_cost("gemini-3.1-flash-lite", 0) == Decimal("0")
