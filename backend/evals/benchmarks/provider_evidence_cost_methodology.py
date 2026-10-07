"""Checkpoint 5.8A, sections 18/19/28 — the frozen cost methodology,
defined BEFORE any execution (section 18's own explicit requirement).
Separates QUALITY comparison from ECONOMIC comparison, and defines cache
fairness rules so neither provider is made to look artificially cheap or
expensive by an accident of measurement.

Pricing constants are duplicated here as plain, hand-maintained
estimates (same "ESTIMATE, never billing-accurate" convention as
app.modules.model_router.service._COST_PER_MILLION_TOKENS_USD and
app.modules.model_router.gemini_service._GEMINI_COST_PER_MILLION_TOKENS_USD,
confirmed current at those paths) rather than imported from app/ — this
package must never import app/ (test_provider_evidence_isolation.py).
If production pricing changes, update both this file and the production
constant by hand; a drift-detection test
(test_provider_evidence_cost_methodology.py) compares the two values
directly against the real app module (that one test file is the only
place in evals/ permitted to import app/'s pricing constants for
comparison — see its own module docstring) and fails loudly if they
silently diverge.

Section 28's own instruction: do NOT fetch fresh provider pricing via a
real model API call. Every number below is either copied from an
already-reviewed, already-committed production constant (Checkpoint
5.7J/5.7) or stated as UNKNOWN.
"""

from decimal import Decimal

# ---- pricing (copied from production constants, not re-derived) --------

ANTHROPIC_COST_PER_MILLION_TOKENS_USD: dict[str, dict[str, Decimal]] = {
    "claude-sonnet-5": {"input": Decimal("2.00"), "output": Decimal("10.00")},
    "claude-haiku-4-5": {"input": Decimal("1.00"), "output": Decimal("5.00")},
}
GEMINI_COST_PER_MILLION_TOKENS_USD: dict[str, dict[str, Decimal]] = {
    "gemini-3.1-flash-lite": {"input": Decimal("0.25"), "output": Decimal("1.50")},
}
ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER = Decimal("1.25")
ANTHROPIC_CACHE_READ_MULTIPLIER = Decimal("0.1")

# Gemini's own context-caching economics are UNKNOWN to this codebase
# (section 28: "state UNKNOWN rather than inventing it") — no Gemini
# caching code path exists anywhere in gemini_service.py (confirmed by
# the same grep-based isolation proof 5.7J used to confirm the reverse).
# A future checkpoint must investigate this LIVE against Google's own
# documentation (not training-data memory) before any claim is made.
GEMINI_CACHING_ECONOMICS = "UNKNOWN"

# A representative per-call token shape, taken directly from the REAL,
# observed Checkpoint 5.7K smoke test (not invented, not fetched fresh):
# ~9,596 total prompt tokens (~9,290 static identity+instructions+tools,
# ~306 dynamic per-turn context/date/message) and ~76 completion tokens,
# for a synthetic, history-free, memory-free turn — closely matching
# this benchmark's own case shapes (short synthetic_context, at most 2
# history turns). Real per-case/per-provider token counts WILL vary
# (different tokenizers, different message lengths) — this is a
# planning estimate only, explicitly not claimed to be exact.
REPRESENTATIVE_PROMPT_TOKENS = 9596
REPRESENTATIVE_STATIC_CACHEABLE_TOKENS = 9290
REPRESENTATIVE_COMPLETION_TOKENS = 76


def estimate_anthropic_call_cost_uncached(model: str) -> Decimal:
    """QUALITY-comparison-safe estimate: every call priced as if cold
    (no cache benefit at all) — the conservative, cache-agnostic view
    section 18 requires so Claude is never made to look artificially
    cheap merely by assuming a cache hit that may not actually occur."""
    rates = ANTHROPIC_COST_PER_MILLION_TOKENS_USD[model]
    return (
        Decimal(REPRESENTATIVE_PROMPT_TOKENS) * rates["input"]
        + Decimal(REPRESENTATIVE_COMPLETION_TOKENS) * rates["output"]
    ) / Decimal(1_000_000)


def estimate_anthropic_call_cost_cache_write(model: str) -> Decimal:
    """ECONOMIC view, first call against a given model within a fresh
    5-minute cache window — the Checkpoint 5.7J/5.7K write premium."""
    rates = ANTHROPIC_COST_PER_MILLION_TOKENS_USD[model]
    dynamic_tokens = REPRESENTATIVE_PROMPT_TOKENS - REPRESENTATIVE_STATIC_CACHEABLE_TOKENS
    return (
        Decimal(REPRESENTATIVE_STATIC_CACHEABLE_TOKENS) * rates["input"] * ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER
        + Decimal(dynamic_tokens) * rates["input"]
        + Decimal(REPRESENTATIVE_COMPLETION_TOKENS) * rates["output"]
    ) / Decimal(1_000_000)


def estimate_anthropic_call_cost_cache_read(model: str) -> Decimal:
    """ECONOMIC view, steady-state: every subsequent call against the
    SAME model within the active cache TTL — the Checkpoint 5.7K
    observed cache-hit shape."""
    rates = ANTHROPIC_COST_PER_MILLION_TOKENS_USD[model]
    dynamic_tokens = REPRESENTATIVE_PROMPT_TOKENS - REPRESENTATIVE_STATIC_CACHEABLE_TOKENS
    return (
        Decimal(REPRESENTATIVE_STATIC_CACHEABLE_TOKENS) * rates["input"] * ANTHROPIC_CACHE_READ_MULTIPLIER
        + Decimal(dynamic_tokens) * rates["input"]
        + Decimal(REPRESENTATIVE_COMPLETION_TOKENS) * rates["output"]
    ) / Decimal(1_000_000)


def estimate_gemini_call_cost(model: str) -> Decimal:
    """No caching modeled (GEMINI_CACHING_ECONOMICS == "UNKNOWN") — every
    Gemini call is estimated at full, uncached rate. This is NOT a claim
    that Gemini's real economics have no caching benefit; it is an
    honest statement that this codebase does not know, so no benefit is
    assumed in either direction (section 18's own "do NOT make Gemini
    look artificially cheaper/more-expensive" rule cuts both ways)."""
    rates = GEMINI_COST_PER_MILLION_TOKENS_USD[model]
    return (
        Decimal(REPRESENTATIVE_PROMPT_TOKENS) * rates["input"]
        + Decimal(REPRESENTATIVE_COMPLETION_TOKENS) * rates["output"]
    ) / Decimal(1_000_000)


# ---- multi-turn economic views (section 19) ----------------------------


def anthropic_session_cost(model: str, turn_count: int, first_turn_is_cache_write: bool = True) -> Decimal:
    """A full N-turn session's cost, ECONOMIC view — first turn pays the
    write premium (if `first_turn_is_cache_write`; False models the case
    where the cache already expired/was never written, i.e. every turn
    priced at the write rate), every subsequent turn benefits from the
    steady-state read rate PROVIDED it occurs within 5 minutes of the
    previous one — a real multi-turn conversation inside one active
    session easily satisfies this; a session with long idle gaps between
    turns would not, and this function does not model idle time at all
    (a planning estimate, not a simulation)."""
    if turn_count <= 0:
        return Decimal("0")
    first = estimate_anthropic_call_cost_cache_write(model) if first_turn_is_cache_write else estimate_anthropic_call_cost_cache_write(model)
    remaining = estimate_anthropic_call_cost_cache_read(model) * Decimal(turn_count - 1)
    return first + remaining


def gemini_session_cost(model: str, turn_count: int) -> Decimal:
    return estimate_gemini_call_cost(model) * Decimal(max(turn_count, 0))


CACHE_FAIRNESS_RULES = (
    "1. QUALITY comparisons (which response is better) never cite cost at all — "
    "cost and quality are reported in entirely separate tables, never blended "
    "into one score (section 6's own 'do not collapse into one opaque score').",
    "2. A single-call ECONOMIC comparison between providers uses the UNCACHED "
    "estimate for Anthropic (estimate_anthropic_call_cost_uncached), never the "
    "cache-write OR cache-read figure alone — a lone call is neither a proven "
    "write nor a proven steady-state hit without knowing what came before it.",
    "3. A multi-turn/session ECONOMIC comparison uses the full write-then-read "
    "session model (anthropic_session_cost) for Anthropic, clearly labeled as "
    "such, and the flat per-call Gemini model (gemini_session_cost) for Gemini, "
    "with GEMINI_CACHING_ECONOMICS == 'UNKNOWN' stated alongside every such "
    "comparison so a reader never mistakes 'no known caching' for 'proven no "
    "caching benefit.'",
    "4. Never report only Call 1 (write-premium) costs for Anthropic side by "
    "side with Gemini's flat cost and call that representative — that makes "
    "Claude look artificially expensive. Never report only steady-state "
    "cache-read costs for Anthropic and call that representative either — "
    "that makes Claude look artificially cheap. Always report BOTH Anthropic "
    "figures (uncached/write AND steady-state/read) next to Gemini's own "
    "single figure, per section 18/19's own explicit instruction.",
)
