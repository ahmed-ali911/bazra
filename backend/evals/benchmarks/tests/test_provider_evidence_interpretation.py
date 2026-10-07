from evals.benchmarks.provider_evidence_checks import CHECK_REGISTRY
from evals.benchmarks.provider_evidence_interpretation import (
    CLEAR_SAFETY_OR_AUTHORITY_VIOLATION,
    NOT_APPLICABLE_PASSED,
    POSSIBLE_EVALUATOR_SENSITIVITY,
    UNRESOLVED_REQUIRES_HUMAN_REVIEW,
    classify,
)


def test_passing_check_is_always_not_applicable() -> None:
    assert classify("mutation_claim", True) == NOT_APPLICABLE_PASSED
    assert classify("anything_unregistered", True) == NOT_APPLICABLE_PASSED


def test_every_registered_check_has_an_explicit_bucket_when_failed() -> None:
    for check_name in CHECK_REGISTRY:
        bucket = classify(check_name, False)
        assert bucket in (CLEAR_SAFETY_OR_AUTHORITY_VIOLATION, POSSIBLE_EVALUATOR_SENSITIVITY, UNRESOLVED_REQUIRES_HUMAN_REVIEW)


def test_unregistered_check_defaults_to_unresolved() -> None:
    assert classify("some_future_check", False) == UNRESOLVED_REQUIRES_HUMAN_REVIEW


def test_classification_never_depends_on_anything_but_name_and_passed() -> None:
    first = classify("mutation_claim", False)
    second = classify("mutation_claim", False)
    assert first == second
