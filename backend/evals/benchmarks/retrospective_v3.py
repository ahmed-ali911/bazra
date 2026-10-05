"""Checkpoint 5.6 — retrospective V1-vs-V2-vs-V3 re-evaluation of the
ALREADY-COMPLETED Checkpoint 5.5 benchmark artifact. ZERO provider
calls: candidate text is parsed from the same stored report
(`evals/benchmarks/results/*.txt`) Checkpoint 5.5A's own
`evals.benchmarks.retrospective` module already parses — this module
reuses that module's own `parse_benchmark_report` directly rather than
re-implementing the parsing logic, and is strictly ADDITIVE: it never
imports from, modifies, or overwrites anything `evals.benchmarks.
retrospective`/`retrospective_report` produce. The original 5.5A
artifact (`retrospective_v1_vs_v2_2026-10-04.txt`) is never touched;
this module's own report is written to a NEW, separately-named file
(see `evals/benchmarks/retrospective_v3_compare.py`'s own CLI for the
exact output path).

SAME MODEL OUTPUTS, NEW EVALUATION INSTRUMENT: every candidate text
replayed here is the EXACT, UNMODIFIED text the real Sonnet/Haiku
benchmark generated in Checkpoint 5.5 (144 real Anthropic generations).
This module makes ZERO provider calls — it only re-scores stored text
through newer, more hardened deterministic checks. It is explicitly NOT
a new model benchmark and must never be reported or read as one.
"""

from dataclasses import dataclass

from evals.benchmarks.retrospective import _OTHER_CHECKS, _SCENARIOS_BY_ID, parse_benchmark_report
from evals.grounding_v2 import check_on_topic_v2
from evals.grounding_v3 import check_language_instruction_following, check_on_topic_v3
from evals.proactive_narration import (
    check_action_confirmation_shaped_v1,
    check_action_confirmation_shaped_v2,
    check_on_topic_v1,
)
from evals.schemas import CheckResult, EvaluationCase

# The 7 checks shared, unchanged, across every version compared here —
# _OTHER_CHECKS minus action_confirmation_shaped (v1), which THIS
# module compares separately (v1 vs v2) rather than treating as a fixed
# background check — reused directly from evals.benchmarks.retrospective
# rather than re-listed as a separate, possibly-drifting copy.
_SHARED_UNCHANGED_CHECKS = tuple(
    check for check in _OTHER_CHECKS if check is not check_action_confirmation_shaped_v1
)


@dataclass(frozen=True)
class RetrospectiveV3CaseComparison:
    case_id: str
    model: str
    run_index: int
    candidate: str | None
    failure_category: str | None

    v1_on_topic: CheckResult | None
    v1_hard_passed: bool | None
    v2_on_topic: CheckResult | None
    v2_hard_passed: bool | None
    v3_on_topic: CheckResult | None
    v3_hard_passed: bool | None

    action_confirmation_v1: CheckResult | None
    action_confirmation_v2: CheckResult | None
    language_instruction_following: CheckResult | None

    other_checks: tuple[CheckResult, ...]

    @property
    def v1_to_v3_verdict_change(self) -> str:
        if self.v1_hard_passed is None or self.v3_hard_passed is None:
            return "none"
        if self.v1_hard_passed == self.v3_hard_passed:
            return "none"
        return "FAIL -> PASS" if self.v3_hard_passed else "PASS -> FAIL"

    @property
    def v2_to_v3_verdict_change(self) -> str:
        if self.v2_hard_passed is None or self.v3_hard_passed is None:
            return "none"
        if self.v2_hard_passed == self.v3_hard_passed:
            return "none"
        return "FAIL -> PASS" if self.v3_hard_passed else "PASS -> FAIL"

    @property
    def action_confirmation_verdict_change(self) -> str:
        if self.action_confirmation_v1 is None or self.action_confirmation_v2 is None:
            return "none"
        if self.action_confirmation_v1.passed == self.action_confirmation_v2.passed:
            return "none"
        return "FAIL -> PASS" if self.action_confirmation_v2.passed else "PASS -> FAIL"


def _evaluate_all_versions(scenario_case_id: str, candidate: str):
    scenario = _SCENARIOS_BY_ID[scenario_case_id]
    facts = {"signal_type": scenario.signal_type, "title": scenario.title, "priority": scenario.priority}
    case = EvaluationCase(case_id=scenario_case_id, purpose="proactive_narration", facts=facts, candidate=candidate)

    other_results = tuple(check(case) for check in _SHARED_UNCHANGED_CHECKS)
    other_hard_passed = all(r.passed for r in other_results)

    v1_result = check_on_topic_v1(case)
    v2_result = check_on_topic_v2(case)
    v3_result = check_on_topic_v3(case)

    ac_v1 = check_action_confirmation_shaped_v1(case)
    ac_v2 = check_action_confirmation_shaped_v2(case)
    lang_result = check_language_instruction_following(case)

    # V1 and V2 hard-pass BOTH use the ORIGINAL action_confirmation_shaped
    # (v1) — this reproduces Checkpoint 5.5A's own "V2" definition
    # byte-for-byte (on_topic_v2 + the original 8 OTHER_CHECKS,
    # unchanged), so this module's V1/V2 columns are directly
    # cross-checkable against `retrospective_v1_vs_v2_2026-10-04.txt`'s
    # own numbers — no silent redefinition of what "V2" already meant.
    # ONLY the V3 column uses the newly-hardened action_confirmation_
    # shaped_v2 — V3 is the one column representing "every hardening
    # this checkpoint introduces, combined".
    v1_hard_passed = v1_result.passed and ac_v1.passed and other_hard_passed
    v2_hard_passed = v2_result.passed and ac_v1.passed and other_hard_passed
    v3_hard_passed = v3_result.passed and ac_v2.passed and other_hard_passed

    return (
        v1_result, v1_hard_passed,
        v2_result, v2_hard_passed,
        v3_result, v3_hard_passed,
        ac_v1, ac_v2, lang_result,
        other_results,
    )


def build_retrospective_v3_comparison(report_text: str) -> tuple[RetrospectiveV3CaseComparison, ...]:
    """The single public entry point. Zero provider calls — operates
    entirely on the already-parsed, already-stored candidate text,
    reusing `evals.benchmarks.retrospective`'s own parser."""
    parsed_rows = parse_benchmark_report(report_text)
    comparisons = []
    for row in parsed_rows:
        if row.candidate is None:
            comparisons.append(
                RetrospectiveV3CaseComparison(
                    case_id=row.case_id, model=row.model, run_index=row.run_index,
                    candidate=None, failure_category=row.failure_category,
                    v1_on_topic=None, v1_hard_passed=None,
                    v2_on_topic=None, v2_hard_passed=None,
                    v3_on_topic=None, v3_hard_passed=None,
                    action_confirmation_v1=None, action_confirmation_v2=None,
                    language_instruction_following=None, other_checks=(),
                )
            )
            continue
        (
            v1_result, v1_hard_passed, v2_result, v2_hard_passed, v3_result, v3_hard_passed,
            ac_v1, ac_v2, lang_result, other_results,
        ) = _evaluate_all_versions(row.case_id, row.candidate)
        comparisons.append(
            RetrospectiveV3CaseComparison(
                case_id=row.case_id, model=row.model, run_index=row.run_index,
                candidate=row.candidate, failure_category=None,
                v1_on_topic=v1_result, v1_hard_passed=v1_hard_passed,
                v2_on_topic=v2_result, v2_hard_passed=v2_hard_passed,
                v3_on_topic=v3_result, v3_hard_passed=v3_hard_passed,
                action_confirmation_v1=ac_v1, action_confirmation_v2=ac_v2,
                language_instruction_following=lang_result,
                other_checks=other_results,
            )
        )
    return tuple(comparisons)
