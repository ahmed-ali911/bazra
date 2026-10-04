"""Checkpoint 5.5A, section 18 — retrospective V1-vs-V2 re-evaluation of
the ALREADY-COMPLETED Checkpoint 5.5 benchmark artifact. ZERO provider
calls: candidate text is parsed from the stored report
(`evals/benchmarks/results/*.txt`), never regenerated.

PARSING SAFETY: the stored report's own `candidate: <repr>` lines were
produced by `evals.benchmarks.report.render_benchmark_report` via
Python's `{text!r}` formatting — a valid Python string literal. This
module parses them with `ast.literal_eval`, the exact structural
inverse of `repr()`, rather than any fragile ad-hoc string-scraping —
this is what makes the parse "safe and reliable" per the brief's own
explicit requirement (section 18: "If the existing artifact format
cannot be parsed safely/reliably: STOP and report"). The original
per-scenario FACTS (signal_type/title/priority) are never re-parsed
from the text at all — they are looked up directly, by case_id, from
`evals.benchmarks.scenarios.PROACTIVE_NARRATION_BENCHMARK_SCENARIOS`,
the same already-frozen source the original benchmark run itself used.

This module is read-only with respect to both the stored artifact and
`evals/grounding_v2.py`/`evals/grounding_v2_fixtures.py` — it is written
and run strictly AFTER both of those were frozen (Checkpoint 5.5A,
section 15's own mandatory ordering).
"""

import ast
import re
from dataclasses import dataclass

from evals.benchmarks.scenarios import PROACTIVE_NARRATION_BENCHMARK_SCENARIOS
from evals.grounding_v2 import check_on_topic_v2
from evals.proactive_narration import (
    check_action_confirmation_shaped,
    check_internal_architecture_terms,
    check_internal_id_leakage,
    check_invented_date,
    check_invented_priority,
    check_mutation_claim,
    check_on_topic_v1,
    check_unsupported_history,
    check_unsupported_mood,
)
from evals.schemas import CheckResult, EvaluationCase

_SCENARIOS_BY_ID = {s.case_id: s for s in PROACTIVE_NARRATION_BENCHMARK_SCENARIOS}

# The 8 checks V1/V2 share unchanged (everything except on_topic) — a
# literal subset of evals.proactive_narration.ALL_CHECKS, reused
# directly rather than re-listed as a separate, possibly-drifting copy.
_OTHER_CHECKS = (
    check_mutation_claim,
    check_action_confirmation_shaped,
    check_unsupported_history,
    check_unsupported_mood,
    check_internal_id_leakage,
    check_internal_architecture_terms,
    check_invented_priority,
    check_invented_date,
)

_CASE_RE = re.compile(r"^CASE: (.+)$")
_MODEL_RUN_RE = re.compile(r"^  \[(.+?)\] run (\d+)$")
_CANDIDATE_RE = re.compile(r"^    candidate: (.+)$")
_PROVIDER_FAILURE_RE = re.compile(r"^    PROVIDER FAILURE: (\S+)")


@dataclass(frozen=True)
class ParsedRow:
    case_id: str
    model: str
    run_index: int
    candidate: str | None
    failure_category: str | None


def parse_benchmark_report(report_text: str) -> tuple[ParsedRow, ...]:
    """Parses only the "PER-CASE RESULTS" section's own fixed structure.
    Raises ValueError (never silently drops or guesses) if a
    `[model] run N` block has neither a candidate line nor a provider-
    failure line before the next block starts — an unparsable/
    unexpected shape must surface loudly, not be approximated.
    """
    rows: list[ParsedRow] = []
    current_case_id: str | None = None
    current_model: str | None = None
    current_run: int | None = None
    pending_resolved = True  # True once the current block got a candidate or failure

    for line in report_text.splitlines():
        case_match = _CASE_RE.match(line)
        if case_match:
            if not pending_resolved:
                raise ValueError(f"unresolved block for case={current_case_id} model={current_model} run={current_run}")
            current_case_id = case_match.group(1)
            continue

        model_run_match = _MODEL_RUN_RE.match(line)
        if model_run_match:
            if not pending_resolved:
                raise ValueError(f"unresolved block for case={current_case_id} model={current_model} run={current_run}")
            current_model = model_run_match.group(1)
            current_run = int(model_run_match.group(2))
            pending_resolved = False
            continue

        failure_match = _PROVIDER_FAILURE_RE.match(line)
        if failure_match and not pending_resolved:
            rows.append(ParsedRow(current_case_id, current_model, current_run, None, failure_match.group(1)))
            pending_resolved = True
            continue

        candidate_match = _CANDIDATE_RE.match(line)
        if candidate_match and not pending_resolved:
            candidate_text = ast.literal_eval(candidate_match.group(1))
            if not isinstance(candidate_text, str):
                raise ValueError(f"parsed candidate is not a string: {candidate_text!r}")
            rows.append(ParsedRow(current_case_id, current_model, current_run, candidate_text, None))
            pending_resolved = True
            continue

    if not pending_resolved:
        raise ValueError(f"unresolved trailing block for case={current_case_id} model={current_model} run={current_run}")
    if not rows:
        raise ValueError("parsed zero rows — report format did not match the expected structure")
    return tuple(rows)


@dataclass(frozen=True)
class RetrospectiveCaseComparison:
    case_id: str
    model: str
    run_index: int
    candidate: str | None
    failure_category: str | None
    v1_on_topic: CheckResult | None
    v1_hard_passed: bool | None
    v2_on_topic: CheckResult | None
    v2_hard_passed: bool | None
    other_checks: tuple[CheckResult, ...]

    @property
    def verdict_change(self) -> str:
        if self.v1_hard_passed is None or self.v2_hard_passed is None:
            return "none"
        if self.v1_hard_passed == self.v2_hard_passed:
            return "none"
        return "FAIL -> PASS" if self.v2_hard_passed else "PASS -> FAIL"


def _evaluate_both_versions(scenario_case_id: str, candidate: str) -> tuple[CheckResult, bool, CheckResult, bool, tuple[CheckResult, ...]]:
    scenario = _SCENARIOS_BY_ID[scenario_case_id]
    facts = {"signal_type": scenario.signal_type, "title": scenario.title, "priority": scenario.priority}
    case = EvaluationCase(case_id=scenario_case_id, purpose="proactive_narration", facts=facts, candidate=candidate)

    other_results = tuple(check(case) for check in _OTHER_CHECKS)
    other_hard_passed = all(r.passed for r in other_results)

    v1_result = check_on_topic_v1(case)
    v2_result = check_on_topic_v2(case)

    return (
        v1_result, v1_result.passed and other_hard_passed,
        v2_result, v2_result.passed and other_hard_passed,
        other_results,
    )


def build_retrospective_comparison(report_text: str) -> tuple[RetrospectiveCaseComparison, ...]:
    """The single public entry point. Zero provider calls — operates
    entirely on the already-parsed, already-stored candidate text."""
    parsed_rows = parse_benchmark_report(report_text)
    comparisons = []
    for row in parsed_rows:
        if row.candidate is None:
            comparisons.append(
                RetrospectiveCaseComparison(
                    case_id=row.case_id, model=row.model, run_index=row.run_index,
                    candidate=None, failure_category=row.failure_category,
                    v1_on_topic=None, v1_hard_passed=None,
                    v2_on_topic=None, v2_hard_passed=None, other_checks=(),
                )
            )
            continue
        v1_result, v1_hard_passed, v2_result, v2_hard_passed, other_results = _evaluate_both_versions(
            row.case_id, row.candidate
        )
        comparisons.append(
            RetrospectiveCaseComparison(
                case_id=row.case_id, model=row.model, run_index=row.run_index,
                candidate=row.candidate, failure_category=None,
                v1_on_topic=v1_result, v1_hard_passed=v1_hard_passed,
                v2_on_topic=v2_result, v2_hard_passed=v2_hard_passed,
                other_checks=other_results,
            )
        )
    return tuple(comparisons)
