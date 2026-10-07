from evals.benchmarks.provider_evidence_blind_review import blind_slot_order, slot_labels


def test_blind_slot_order_is_deterministic_across_calls() -> None:
    providers = ("anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5", "google_gemini/gemini-3.1-flash-lite")
    first = blind_slot_order("c1_hussein_clear_action", providers)
    second = blind_slot_order("c1_hussein_clear_action", providers)
    assert first == second


def test_blind_slot_order_is_a_permutation_of_the_input() -> None:
    providers = ("anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5", "google_gemini/gemini-3.1-flash-lite")
    order = blind_slot_order("c1_hussein_clear_action", providers)
    assert set(order) == set(providers)
    assert len(order) == len(providers)


def test_blind_slot_order_differs_across_case_ids() -> None:
    """Not a hard mathematical guarantee for every possible pair, but
    true for this repository's own real case ids — proves the ordering
    is actually case-dependent, not a constant permutation that would
    let a reviewer learn 'slot A is always Gemini' across many cases."""
    providers = ("anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5", "google_gemini/gemini-3.1-flash-lite")
    orders = {blind_slot_order(case_id, providers) for case_id in ("a1_zahqan_restraint", "b1_tired_gym_study", "c1_hussein_clear_action", "g1_job_decision_ar")}
    assert len(orders) > 1, "blind_slot_order produced the same permutation for every case_id tested"


def test_slot_labels_never_contain_provider_names() -> None:
    labels = slot_labels(3)
    assert labels == ("Response A", "Response B", "Response C")
    for label in labels:
        assert "claude" not in label.lower()
        assert "gemini" not in label.lower()
        assert "anthropic" not in label.lower()
        assert "google" not in label.lower()
