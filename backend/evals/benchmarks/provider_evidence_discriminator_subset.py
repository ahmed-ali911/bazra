"""Checkpoint 5.8A, sections 17/26/28 — the frozen Stage 1 discriminator
subset: a small, deliberately high-information set of cases chosen to
surface an OBVIOUSLY insufficient provider/model cheaply, before the
full repetition budget is spent. Selection criteria (all eight cases
satisfy at least one): covers a distinct workload family, includes at
least one hard-gate authority/safety case (c1, d1), at least one
subjective-quality case per major axis (restraint: a1; emotional
read-the-room: b1; selective grounding: f1; reasoning: g1), the known
regression class (i1, from the 5.5 holdout's own evidenced failure
mode), and the identity-fit case most likely to reveal generic/
corporate phrasing (j1).

Every id below MUST exist in provider_evidence_cases.CASES_BY_ID and
MUST NOT be a ZERO_LLM control case — see
evals/benchmarks/tests/test_provider_evidence_discriminator_subset.py
for both proofs.
"""

DISCRIMINATOR_SUBSET_CASE_IDS: tuple[str, ...] = (
    "a1_zahqan_restraint",
    "b1_tired_gym_study",
    "c1_hussein_clear_action",
    "d1_gendered_coreference_ar",
    "f1_selective_priority_ar",
    "g1_job_decision_ar",
    "i1_event_no_date_supplied",
    "j1_self_description_ar",
)
