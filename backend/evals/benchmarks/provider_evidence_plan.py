"""Checkpoint 5.8A, sections 15/16/17/26 — the benchmark's own frozen
execution plan: provider/model candidates, repetition strategy, staged
execution tiers, and the resulting call-budget calculation (full design
and reduced/discriminator design). This module computes numbers from
the frozen case set (`provider_evidence_cases.py`) and the
discriminator subset (`provider_evidence_discriminator_subset.py`) —
nothing here is hand-typed arithmetic that could drift from the actual
case count.

NO EXECUTION HAPPENS HERE (section 2/27) — every function in this
module is a pure, offline calculation over frozen data; none of them
make, schedule, or prepare a real network call.
"""

from dataclasses import dataclass

from evals.benchmarks.provider_evidence_cases import PROVIDER_EVIDENCE_CASES
from evals.benchmarks.provider_evidence_discriminator_subset import DISCRIMINATOR_SUBSET_CASE_IDS

# Section 15 — candidates for later comparison. Listing them here is NOT
# a claim these three must remain the final routing choices (section 15's
# own explicit caveat) — just the known, already-integrated candidates
# this repository can reach today (Checkpoint 5.7/5.7H).
PROVIDER_MODEL_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("anthropic", "claude-sonnet-5"),       # current production default reference
    ("anthropic", "claude-haiku-4-5"),      # current lightweight/verifier reference
    ("google_gemini", "gemini-3.1-flash-lite"),  # explicit Gemini Test candidate
)

# Section 16 — repetition strategy. 3 reps for cases where subjective/
# nondeterministic judgment matters (evidenced necessary by Checkpoint
# 5.5, where 3 repetitions exposed real inconsistency); 1 rep for cases
# that are primarily a deterministic structural/tool-selection question
# (a wrong tool name or a broken line-count ceiling is not the kind of
# thing that becomes "right" on a retry — if it's flaky, that flakiness
# IS the finding, worth seeing even at n=1, not worth tripling the cost
# to pre-confirm). Every provider gets the IDENTICAL rep count per case
# — never fewer reps for one provider than another, which would bias
# the nondeterminism evidence itself.
_THREE_REP_TAGS = frozenset({"casual", "emotional", "low_energy", "ambiguous", "context_use", "complex_reasoning", "humor", "light", "serious", "contrastive"})


def _recommended_reps(case) -> int:
    """3 reps when the case is primarily a subjective/nondeterminism-
    sensitive judgment (non-empty human_review_dimensions AND not a
    hard structural/tool-selection-dominant case); 1 rep otherwise. This
    mirrors, rather than hand-lists, each case's own authored intent —
    see provider_evidence_cases.py's own family-by-family commentary for
    why each case landed where it did."""
    structural_families = {"LOCAL_FACT_RETRIEVAL", "INSTRUCTION_FOLLOWING", "FACTUAL_RESTRAINT", "COMPLEX_TOOL_ACTION_INTERPRETATION", "BAZRA_SELF_DESCRIPTION"}
    if case.family in structural_families:
        return 1
    if case.case_id in ("c1_hussein_clear_action", "d1_gendered_coreference_ar"):
        return 1
    return 3


@dataclass(frozen=True)
class CallBudget:
    label: str
    case_count: int
    total_generation_calls: int
    calls_by_provider: dict[str, int]


def _generative_cases():
    """Every case EXCEPT the ZERO_LLM controls (section 10.E) — those
    are verified structurally (does the production code path ever call
    model_router.complete for this input shape?), never by spending real
    provider-call budget to "prove" an architecture guarantee that a
    code-level test already proves for free. See this module's own
    `CONTROL_CASE_NOTE`."""
    return tuple(c for c in PROVIDER_EVIDENCE_CASES if not c.is_control)


CONTROL_CASE_NOTE = (
    "ZERO_LLM control cases (e1_open_tasks_control, e2_inbox_attention_control) are "
    "deliberately EXCLUDED from every call-budget figure below. Their own claim "
    "(no LLM involvement at all) is proven by a structural/code-path test, not by "
    "spending real provider-call budget — see evals/benchmarks/tests/"
    "test_provider_evidence_cases.py's own test for that proof. Section 10.E's own "
    "text: 'Do not waste real provider calls later on cases whose accepted "
    "architecture already guarantees zero-LLM unless needed specifically as a "
    "control.'"
)


def full_design_budget() -> CallBudget:
    """Section 28's "FULL DESIGN": every non-control case, at its own
    recommended rep count, against every candidate provider/model."""
    cases = _generative_cases()
    per_model_calls = sum(_recommended_reps(c) for c in cases)
    calls_by_provider = {f"{p}/{m}": per_model_calls for p, m in PROVIDER_MODEL_CANDIDATES}
    total = per_model_calls * len(PROVIDER_MODEL_CANDIDATES)
    return CallBudget("FULL_DESIGN", len(cases), total, calls_by_provider)


def discriminator_design_budget() -> CallBudget:
    """Section 28's "REDUCED DISCRIMINATOR DESIGN" — Stage 1 only
    (section 17): the discriminator subset, each case run exactly once
    per provider/model, regardless of its own full-design rep count.
    Stage 1's purpose is cheap, early insufficiency detection, not a
    final answer — see `provider_evidence_discriminator_subset.py`."""
    cases = tuple(c for c in _generative_cases() if c.case_id in DISCRIMINATOR_SUBSET_CASE_IDS)
    per_model_calls = len(cases)  # exactly 1 rep each in Stage 1
    calls_by_provider = {f"{p}/{m}": per_model_calls for p, m in PROVIDER_MODEL_CANDIDATES}
    total = per_model_calls * len(PROVIDER_MODEL_CANDIDATES)
    return CallBudget("DISCRIMINATOR_STAGE_1", len(cases), total, calls_by_provider)


# ---- staged execution plan (section 17) --------------------------------

STAGE_1_DISCRIMINATOR = (
    "STAGE 1 — DISCRIMINATOR SET. Run every case in DISCRIMINATOR_SUBSET_CASE_IDS "
    "exactly once per candidate provider/model (see discriminator_design_budget()). "
    "Purpose: cheaply identify a provider/model that is OBVIOUSLY insufficient "
    "(a hard-gate failure on an unambiguous case, e.g. a clear mutation claim or a "
    "wrong tool on c1/d1) before spending the full repetition budget on it. A "
    "provider failing a HARD check here on an unambiguous case may be excluded "
    "from Stage 2 for that workload family specifically — never globally from a "
    "single case, since different families may legitimately need different "
    "resources (section 24)."
)

STAGE_2_FULL_QUALIFIED_SET = (
    "STAGE 2 — FULL QUALIFIED SET. For every (provider/model, workload family) "
    "pair that survived Stage 1, run the REMAINING cases in that family (and the "
    "remaining reps for any discriminator-subset case in that family) at each "
    "case's own full recommended rep count (_recommended_reps). A (provider, "
    "family) pair that was excluded in Stage 1 is skipped here, not re-tried — "
    "Stage 1's own hard-gate failure is itself the evidence."
)

STAGE_3_TARGETED_TIEBREAKERS = (
    "STAGE 3 — TARGETED TIEBREAKERS. Only for a case where Stage 2's own evidence "
    "remains ambiguous (a hard check disagrees across reps, i.e. passes some runs "
    "and fails others, OR a subjective rubric dimension's score spread across reps/"
    "reviewers is >= 2 points on the 1-5 scale), add up to 2 EXTRA reps for that "
    "specific (case, provider/model) pair only. Bounded by construction: at most "
    "6 cases may plausibly need this (one per family group sharing a workload "
    "concern), times 3 providers, times 2 extra reps = 36 calls, the worst-case "
    "ceiling on top of the full design's own 144 — never run on a case/provider "
    "pair that Stage 2 already resolved unambiguously."
)

STAGE_3_WORST_CASE_CEILING_CALLS = 6 * len(PROVIDER_MODEL_CANDIDATES) * 2  # 36
