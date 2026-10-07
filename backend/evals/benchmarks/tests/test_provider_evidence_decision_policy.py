from evals.benchmarks.provider_evidence_decision_policy import (
    CaseProviderEvidence,
    cheapest_sufficient,
    is_proven_sufficient,
)


def test_hard_gate_failure_is_never_sufficient_regardless_of_reps() -> None:
    evidence = CaseProviderEvidence("c1", "anthropic", "claude-sonnet-5", reps_run=3, hard_gate_failures=1, unresolved_ambiguous=False)
    assert is_proven_sufficient(evidence) is False


def test_unresolved_ambiguous_is_never_sufficient() -> None:
    evidence = CaseProviderEvidence("c1", "anthropic", "claude-sonnet-5", reps_run=3, hard_gate_failures=0, unresolved_ambiguous=True)
    assert is_proven_sufficient(evidence) is False


def test_clean_evidence_is_sufficient() -> None:
    evidence = CaseProviderEvidence("c1", "anthropic", "claude-sonnet-5", reps_run=3, hard_gate_failures=0, unresolved_ambiguous=False)
    assert is_proven_sufficient(evidence) is True


def test_zero_reps_is_never_sufficient() -> None:
    evidence = CaseProviderEvidence("c1", "anthropic", "claude-sonnet-5", reps_run=0, hard_gate_failures=0, unresolved_ambiguous=False)
    assert is_proven_sufficient(evidence) is False


def test_cheapest_sufficient_never_picks_a_hard_gate_failure_even_if_cheapest() -> None:
    cheap_but_failing = CaseProviderEvidence("c1", "google_gemini", "gemini-3.1-flash-lite", reps_run=3, hard_gate_failures=1, unresolved_ambiguous=False)
    expensive_but_clean = CaseProviderEvidence("c1", "anthropic", "claude-sonnet-5", reps_run=3, hard_gate_failures=0, unresolved_ambiguous=False)
    cost_rank = {"google_gemini/gemini-3.1-flash-lite": 0, "anthropic/claude-sonnet-5": 2}
    result = cheapest_sufficient([cheap_but_failing, expensive_but_clean], cost_rank)
    assert result is expensive_but_clean


def test_cheapest_sufficient_returns_none_when_nothing_qualifies() -> None:
    only_failing = CaseProviderEvidence("c1", "anthropic", "claude-sonnet-5", reps_run=3, hard_gate_failures=2, unresolved_ambiguous=False)
    assert cheapest_sufficient([only_failing], {}) is None


def test_cheapest_sufficient_picks_the_cheaper_of_two_proven_candidates() -> None:
    a = CaseProviderEvidence("c1", "google_gemini", "gemini-3.1-flash-lite", reps_run=1, hard_gate_failures=0, unresolved_ambiguous=False)
    b = CaseProviderEvidence("c1", "anthropic", "claude-sonnet-5", reps_run=3, hard_gate_failures=0, unresolved_ambiguous=False)
    cost_rank = {"google_gemini/gemini-3.1-flash-lite": 0, "anthropic/claude-sonnet-5": 2}
    assert cheapest_sufficient([a, b], cost_rank) is a
