"""Checkpoint 5.8A, section 27 — fixture schema validity, uniqueness,
frozen version, valid family/resource-hypothesis, hard-check
registration, rubric-anchor coverage, no provider identity leakage, no
private data, and the ZERO_LLM control structural proof.
"""

import re

from evals.benchmarks.provider_evidence_cases import PROVIDER_EVIDENCE_CASES, VERSION
from evals.benchmarks.provider_evidence_checks import CHECK_REGISTRY
from evals.benchmarks.provider_evidence_rubric import DIMENSIONS_BY_NAME
from evals.benchmarks.provider_evidence_schemas import (
    ALL_PRODUCTION_TOOL_NAMES,
    LANGUAGES,
    RESOURCE_CLASS_HYPOTHESES,
    WORKLOAD_FAMILIES,
)


def test_case_ids_are_unique() -> None:
    ids = [c.case_id for c in PROVIDER_EVIDENCE_CASES]
    assert len(ids) == len(set(ids)), f"duplicate case_id(s): {[i for i in ids if ids.count(i) > 1]}"


def test_every_case_is_frozen_at_the_module_version() -> None:
    for case in PROVIDER_EVIDENCE_CASES:
        assert case.version == VERSION, f"{case.case_id} has version {case.version!r}, expected {VERSION!r}"


def test_every_case_has_a_valid_workload_family() -> None:
    for case in PROVIDER_EVIDENCE_CASES:
        assert case.family in WORKLOAD_FAMILIES, f"{case.case_id} has unknown family {case.family!r}"


def test_every_workload_family_is_represented() -> None:
    represented = {c.family for c in PROVIDER_EVIDENCE_CASES}
    missing = set(WORKLOAD_FAMILIES) - represented
    assert not missing, f"no case exists for families: {missing}"


def test_every_case_has_a_valid_language_tag() -> None:
    for case in PROVIDER_EVIDENCE_CASES:
        assert case.language in LANGUAGES, f"{case.case_id} has unknown language {case.language!r}"


def test_every_case_has_a_valid_expected_resource_class_hypothesis() -> None:
    for case in PROVIDER_EVIDENCE_CASES:
        assert case.expected_resource_class in RESOURCE_CLASS_HYPOTHESES, (
            f"{case.case_id} has unknown expected_resource_class {case.expected_resource_class!r}"
        )


def test_every_hard_check_name_is_registered() -> None:
    for case in PROVIDER_EVIDENCE_CASES:
        for check_name in case.hard_checks:
            assert check_name in CHECK_REGISTRY, f"{case.case_id} references unregistered check {check_name!r}"


def test_every_human_review_dimension_has_rubric_anchors() -> None:
    for case in PROVIDER_EVIDENCE_CASES:
        for dimension_name in case.human_review_dimensions:
            assert dimension_name in DIMENSIONS_BY_NAME, (
                f"{case.case_id} references dimension {dimension_name!r} with no rubric entry"
            )


def test_every_rubric_dimension_has_a_five_point_anchor_set() -> None:
    from evals.benchmarks.provider_evidence_rubric import ALL_DIMENSIONS

    for dimension in ALL_DIMENSIONS:
        scores = sorted(a.score for a in dimension.anchors)
        assert scores == [1, 2, 3, 4, 5], f"{dimension.name} does not have anchors for every score 1-5: {scores}"
        for anchor in dimension.anchors:
            assert anchor.description.strip(), f"{dimension.name} anchor {anchor.score} has an empty description"


def test_available_tools_is_always_a_subset_of_the_real_production_catalog() -> None:
    for case in PROVIDER_EVIDENCE_CASES:
        assert set(case.available_tools).issubset(set(ALL_PRODUCTION_TOOL_NAMES)), (
            f"{case.case_id} offers a tool not in production's own catalog"
        )


def test_zero_llm_control_cases_offer_no_tools_and_are_marked_control() -> None:
    """Section 10.E's own structural proof: a control case's
    `available_tools` is empty (nothing to call, because no LLM is ever
    reached in the accepted production architecture) and `is_control` is
    True — the only two properties the FULL_DESIGN budget calculation
    relies on to exclude these cases from real-call spend (see
    provider_evidence_plan.py's own CONTROL_CASE_NOTE)."""
    controls = [c for c in PROVIDER_EVIDENCE_CASES if c.family == "LOCAL_FACT_RETRIEVAL"]
    assert controls, "expected at least one LOCAL_FACT_RETRIEVAL case"
    for case in controls:
        assert case.is_control is True, f"{case.case_id} is a LOCAL_FACT_RETRIEVAL case but is_control is False"
        assert case.available_tools == (), f"{case.case_id} is a ZERO_LLM control but offers tools: {case.available_tools}"
        assert case.expected_resource_class == "ZERO_LLM"


def test_pairs_with_references_are_mutual() -> None:
    """Section 11 — a contrastive pair names each other symmetrically."""
    by_id = {c.case_id: c for c in PROVIDER_EVIDENCE_CASES}
    for case in PROVIDER_EVIDENCE_CASES:
        if case.pairs_with is None:
            continue
        assert case.pairs_with in by_id, f"{case.case_id}.pairs_with references unknown case {case.pairs_with!r}"
        partner = by_id[case.pairs_with]
        assert partner.pairs_with == case.case_id, (
            f"{case.case_id} pairs_with {case.pairs_with!r}, but that case's own pairs_with is {partner.pairs_with!r}"
        )


_SENSITIVE_REAL_NAMES = ("Ahmed Aboeldeb", "ahmedaboeldeb", "icloud.com", "ahmed-ali911")


def test_no_real_personal_data_in_any_fixture_text() -> None:
    """Section 12/23/28 — every name/context/phrase here must be
    synthetic. A real name/email appearing in a committed fixture would
    be a direct violation of 'use synthetic names/context where
    necessary.'"""
    for case in PROVIDER_EVIDENCE_CASES:
        haystack = " ".join([
            case.user_message, case.synthetic_context, case.expected_behavior,
            *(t.content for t in case.conversation_history),
        ])
        for forbidden in _SENSITIVE_REAL_NAMES:
            assert forbidden.lower() not in haystack.lower(), f"{case.case_id} contains real personal data: {forbidden!r}"


def test_no_provider_or_model_identity_in_any_candidate_facing_text() -> None:
    """Section 27 — no case's own user_message/synthetic_context/
    conversation_history (the part a real candidate generation would
    actually see) may embed a provider/model name; that would let a
    model's own response trivially reveal or react to which provider is
    being tested, contaminating the comparison itself."""
    forbidden_terms = re.compile(r"\b(anthropic|claude|gemini|google_gemini|gpt|openai)\b", re.IGNORECASE)
    for case in PROVIDER_EVIDENCE_CASES:
        candidate_facing = " ".join([
            case.user_message, case.synthetic_context, *(t.content for t in case.conversation_history),
        ])
        matched = forbidden_terms.search(candidate_facing)
        assert matched is None, f"{case.case_id} leaks provider/model identity in candidate-facing text: {matched.group(0)!r}"
