from evals.benchmarks.provider_evidence_cases import CASES_BY_ID
from evals.benchmarks.provider_evidence_discriminator_subset import DISCRIMINATOR_SUBSET_CASE_IDS


def test_every_discriminator_id_references_a_real_case() -> None:
    for case_id in DISCRIMINATOR_SUBSET_CASE_IDS:
        assert case_id in CASES_BY_ID, f"discriminator subset references unknown case_id {case_id!r}"


def test_discriminator_subset_excludes_zero_llm_controls() -> None:
    for case_id in DISCRIMINATOR_SUBSET_CASE_IDS:
        assert CASES_BY_ID[case_id].is_control is False, f"{case_id} is a ZERO_LLM control and must not be in the discriminator subset"


def test_discriminator_subset_has_no_duplicates() -> None:
    assert len(DISCRIMINATOR_SUBSET_CASE_IDS) == len(set(DISCRIMINATOR_SUBSET_CASE_IDS))


def test_discriminator_subset_covers_multiple_families() -> None:
    families = {CASES_BY_ID[case_id].family for case_id in DISCRIMINATOR_SUBSET_CASE_IDS}
    assert len(families) >= 6, f"discriminator subset only covers {len(families)} distinct families: {families}"
