"""Checkpoint 5.8A, sections 24/25 — the decision rules, defined BEFORE
any execution. A pure, testable predicate (`is_proven_sufficient`) plus
the routing-change boundary (section 25), which this module can only
state, never cross — nothing here changes app/'s own routing, and
nothing in a future execution checkpoint may either, without a separate,
Ahmed-approved checkpoint of its own.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseProviderEvidence:
    """One (case, provider/model) pair's aggregated evidence across its
    own reps — the minimal shape `is_proven_sufficient` needs. A future
    execution checkpoint assembles this from real `EvaluationResult`s;
    nothing here depends on how that assembly happens."""

    case_id: str
    provider: str
    model: str
    reps_run: int
    hard_gate_failures: int  # count of reps where ANY hard check failed
    unresolved_ambiguous: bool  # Stage 3 still didn't settle it (section 17)


def is_proven_sufficient(evidence: CaseProviderEvidence) -> bool:
    """Section 24's own rule, literally: 'A model cannot be "sufficient"
    for a workload family if it has unresolved critical hard-gate
    failures.' Cost is never part of this predicate — a cheap model
    that fails a hard gate is NOT sufficient regardless of price (section
    7: 'Cost can NEVER compensate for a hard-gate failure'), and an
    expensive model that passes every hard gate IS sufficient regardless
    of price (price is a SEPARATE, later preference-ranking input, not a
    sufficiency gate)."""
    if evidence.hard_gate_failures > 0:
        return False
    if evidence.unresolved_ambiguous:
        return False
    return evidence.reps_run > 0


def cheapest_sufficient(evidences: list[CaseProviderEvidence], cost_rank: dict[str, int]) -> CaseProviderEvidence | None:
    """Section 24: 'A cheaper model should be preferred only when it is
    PROVEN SUFFICIENT.' `cost_rank` maps "provider/model" -> an integer
    rank (lower = cheaper); this function never computes cost itself
    (see provider_evidence_cost_methodology.py for that) — it only
    APPLIES an already-computed ranking to an already-computed
    sufficiency verdict. Returns None when no candidate is proven
    sufficient for this case — a legitimate, honest outcome (section 24:
    different families may legitimately require different resources, up
    to and including none of the tested candidates)."""
    sufficient = [e for e in evidences if is_proven_sufficient(e)]
    if not sufficient:
        return None
    return min(sufficient, key=lambda e: cost_rank.get(f"{e.provider}/{e.model}", float("inf")))


DECISION_RULES = (
    "A model cannot be 'sufficient' for a workload family if it has an unresolved "
    "critical hard-gate failure (section 7/24) — no cost figure, however small, "
    "changes this.",
    "A cheaper model is preferred over a more expensive one ONLY when both are "
    "proven sufficient for the same workload family (section 24).",
    "A more expensive model is retained where cheaper candidates show material "
    "quality/safety loss on that family — 'material' is judged from the full, "
    "per-dimension evidence (hard-gate pass rate + the relevant rubric scores), "
    "never from a single blended number (section 6/9/24).",
    "No single overall winner is required or expected — different workload "
    "families may legitimately resolve to different resources (section 24's own "
    "worked example: Gemini sufficient for casual, Sonnet required for complex "
    "reasoning, Haiku sufficient for narration, Sonnet required for action "
    "interpretation — a valid, desirable outcome, not a contradiction).",
)

ROUTING_CHANGE_BOUNDARY = (
    "Section 25 — even after a future benchmark execution produces every figure "
    "this module's own decision rules need, NOTHING in evals/ may modify "
    "app/modules/model_router's own routing, app/modules/chat's own tool/model "
    "selection, or any other production routing behavior. Benchmark evidence "
    "produces a RECOMMENDATION only; a separate, later, Ahmed-approved checkpoint "
    "performs any actual routing change, exactly as Checkpoint 5.7H's own Gemini "
    "Test Mode remained explicit/opt-in rather than silently becoming a default. "
    "evals/tests/test_isolation.py's own import-grep is the structural guarantee "
    "this boundary cannot be silently crossed by this package."
)
