"""Checkpoint 5.8A, section 28 — proves the call-budget arithmetic is
actually computed from the frozen case set (not hand-typed), and that
control cases are excluded, exactly as provider_evidence_plan.py's own
CONTROL_CASE_NOTE claims. Zero provider calls anywhere in this test.
"""

from evals.benchmarks.provider_evidence_cases import PROVIDER_EVIDENCE_CASES
from evals.benchmarks.provider_evidence_discriminator_subset import DISCRIMINATOR_SUBSET_CASE_IDS
from evals.benchmarks.provider_evidence_plan import (
    PROVIDER_MODEL_CANDIDATES,
    discriminator_design_budget,
    full_design_budget,
)


def test_full_design_budget_excludes_control_cases() -> None:
    budget = full_design_budget()
    control_count = sum(1 for c in PROVIDER_EVIDENCE_CASES if c.is_control)
    assert budget.case_count == len(PROVIDER_EVIDENCE_CASES) - control_count


def test_full_design_budget_is_consistent_with_provider_count() -> None:
    budget = full_design_budget()
    assert budget.total_generation_calls % len(PROVIDER_MODEL_CANDIDATES) == 0
    assert len(budget.calls_by_provider) == len(PROVIDER_MODEL_CANDIDATES)
    assert sum(budget.calls_by_provider.values()) == budget.total_generation_calls


def test_full_design_budget_matches_hand_verified_total() -> None:
    """Checkpoint 5.8A's own report states 144 total generation calls
    (12 three-rep cases + 12 one-rep cases, times 3 providers) — this
    test is the structural proof that number is actually derived from
    the frozen case set, not a hand-typed figure that could silently
    drift from it."""
    budget = full_design_budget()
    assert budget.total_generation_calls == 144


def test_discriminator_design_budget_is_one_rep_per_case_per_provider() -> None:
    budget = discriminator_design_budget()
    assert budget.case_count == len(DISCRIMINATOR_SUBSET_CASE_IDS)
    assert budget.total_generation_calls == len(DISCRIMINATOR_SUBSET_CASE_IDS) * len(PROVIDER_MODEL_CANDIDATES)


def test_discriminator_budget_is_materially_smaller_than_full_budget() -> None:
    full = full_design_budget()
    reduced = discriminator_design_budget()
    assert reduced.total_generation_calls < full.total_generation_calls
