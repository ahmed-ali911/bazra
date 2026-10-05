"""Checkpoint 5.6 — renders the V1-vs-V2-vs-V3 retrospective comparison
into a focused, human-readable artifact. Mirrors
`evals.benchmarks.retrospective_report`'s own shape (per-model
aggregate, then changed-case sections, then remaining failures) rather
than dumping all 144 rows.

LABEL, EXPLICITLY, PER THIS CHECKPOINT'S OWN BRIEF: this is the SAME
MODEL OUTPUTS the real Checkpoint 5.5 benchmark generated (144 real
Anthropic generations), re-scored through a NEW EVALUATION INSTRUMENT.
It is NOT a new model benchmark and must never be read as one.
"""

from evals.benchmarks.retrospective_v3 import RetrospectiveV3CaseComparison

_MODELS = ("claude-sonnet-5", "claude-haiku-4-5")

_HEADER = (
    "Checkpoint 5.6 — Retrospective V1-vs-V2-vs-V3 re-evaluation of the completed Checkpoint 5.5 benchmark\n"
    "(zero provider calls — stored candidate text re-evaluated only)\n"
    "\n"
    "SAME MODEL OUTPUTS, NEW EVALUATION INSTRUMENT — this is NOT a new model benchmark.\n"
    "V1/V2 columns reproduce Checkpoint 5.5A's own numbers exactly (on_topic_v1/v2 + the\n"
    "ORIGINAL action_confirmation_shaped). The V3 column additionally uses on_topic_v3 and the\n"
    "newly-hardened action_confirmation_shaped_v2 — the full combination of this checkpoint's\n"
    "own hardening."
)


def _model_summary(comparisons: tuple[RetrospectiveV3CaseComparison, ...], model: str) -> str:
    rows = [c for c in comparisons if c.model == model]
    scored = [c for c in rows if c.v1_hard_passed is not None]
    v1_passes = sum(1 for c in scored if c.v1_hard_passed)
    v2_passes = sum(1 for c in scored if c.v2_hard_passed)
    v3_passes = sum(1 for c in scored if c.v3_hard_passed)
    v1_to_v3_fail_to_pass = sum(1 for c in scored if c.v1_to_v3_verdict_change == "FAIL -> PASS")
    v1_to_v3_pass_to_fail = sum(1 for c in scored if c.v1_to_v3_verdict_change == "PASS -> FAIL")
    ac_corrections = sum(1 for c in scored if c.action_confirmation_verdict_change == "FAIL -> PASS")
    lang_mismatches = sum(
        1 for c in scored
        if c.language_instruction_following is not None and not c.language_instruction_following.passed
    )

    remaining: dict[str, int] = {}
    for c in scored:
        if c.v3_hard_passed:
            continue
        if c.v3_on_topic and not c.v3_on_topic.passed:
            remaining["on_topic_v3"] = remaining.get("on_topic_v3", 0) + 1
        if c.action_confirmation_v2 and not c.action_confirmation_v2.passed:
            remaining["action_confirmation_shaped_v2"] = remaining.get("action_confirmation_shaped_v2", 0) + 1
        for oc in c.other_checks:
            if not oc.passed:
                remaining[oc.name] = remaining.get(oc.name, 0) + 1

    lines = [
        f"MODEL: {model}",
        f"  total stored generations: {len(rows)}",
        f"  V1 hard passes: {v1_passes}   V2 hard passes: {v2_passes}   V3 hard passes: {v3_passes}",
        f"  V1 -> V3: FAIL->PASS: {v1_to_v3_fail_to_pass}   PASS->FAIL: {v1_to_v3_pass_to_fail}",
        f"  action_confirmation corrections (FAIL->PASS): {ac_corrections}",
        f"  language instruction-following mismatches (informational, non-gating): {lang_mismatches}",
        f"  remaining V3 failures by check: {remaining}",
    ]
    return "\n".join(lines)


def _render_case(c: RetrospectiveV3CaseComparison) -> str:
    lines = [
        f"CASE: {c.case_id}  MODEL: {c.model}  RUN: {c.run_index}",
        f"  candidate: {c.candidate!r}",
        f"  V1: on_topic={'PASS' if c.v1_on_topic.passed else 'FAIL'}  overall={'PASS' if c.v1_hard_passed else 'FAIL'}",
        f"  V2: on_topic={'PASS' if c.v2_on_topic.passed else 'FAIL'}  overall={'PASS' if c.v2_hard_passed else 'FAIL'}",
        f"  V3: on_topic={'PASS' if c.v3_on_topic.passed else 'FAIL'}  overall={'PASS' if c.v3_hard_passed else 'FAIL'}  reason={c.v3_on_topic.reason}",
        f"  action_confirmation: v1={'PASS' if c.action_confirmation_v1.passed else 'FAIL'}  v2={'PASS' if c.action_confirmation_v2.passed else 'FAIL'}",
        f"  language_instruction_following: {'PASS' if c.language_instruction_following.passed else 'FAIL (informational, non-gating)'}",
        f"  V1->V3 VERDICT CHANGE: {c.v1_to_v3_verdict_change}",
    ]
    other_fails = [oc for oc in c.other_checks if not oc.passed]
    if other_fails:
        lines.append(f"  other failing checks: {[(oc.name, oc.reason) for oc in other_fails]}")
    return "\n".join(lines)


def render_retrospective_v3_report(comparisons: tuple[RetrospectiveV3CaseComparison, ...]) -> str:
    lines = [_HEADER, "", "=" * 70, "PER-MODEL AGGREGATE: V1 vs V2 vs V3", "=" * 70, ""]
    for model in _MODELS:
        lines.append(_model_summary(comparisons, model))
        lines.append("")

    lines.append("=" * 70)
    lines.append("V1 -> V3 CHANGED CASES: FAIL -> PASS")
    lines.append("=" * 70)
    lines.append("")
    for c in comparisons:
        if c.v1_to_v3_verdict_change == "FAIL -> PASS":
            lines.append(_render_case(c))
            lines.append("")

    lines.append("=" * 70)
    lines.append("V1 -> V3 CHANGED CASES: PASS -> FAIL")
    lines.append("=" * 70)
    lines.append("")
    for c in comparisons:
        if c.v1_to_v3_verdict_change == "PASS -> FAIL":
            lines.append(_render_case(c))
            lines.append("")

    lines.append("=" * 70)
    lines.append("ACTION-CONFIRMATION CORRECTIONS (V1 -> V2 hardened check, FAIL -> PASS)")
    lines.append("=" * 70)
    lines.append("")
    for c in comparisons:
        if c.action_confirmation_verdict_change == "FAIL -> PASS":
            lines.append(_render_case(c))
            lines.append("")

    lines.append("=" * 70)
    lines.append("REMAINING V3 FAILURES (still failing after this checkpoint's hardening)")
    lines.append("=" * 70)
    lines.append("")
    for c in comparisons:
        if c.v3_hard_passed is False:
            lines.append(_render_case(c))
            lines.append("")

    return "\n".join(lines)
