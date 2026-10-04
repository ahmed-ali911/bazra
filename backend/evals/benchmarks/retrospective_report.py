"""Checkpoint 5.5A, sections 19/20/24 — renders the V1-vs-V2
retrospective comparison into a focused, human-readable artifact.
Shows changed cases and remaining V2 failures in full detail — not all
144 rows (section 24: "do not require Ahmed to inspect all 144 outputs
unless useful").
"""

from evals.benchmarks.retrospective import RetrospectiveCaseComparison

_MODELS = ("claude-sonnet-5", "claude-haiku-4-5")


def _model_summary(comparisons: tuple[RetrospectiveCaseComparison, ...], model: str) -> str:
    rows = [c for c in comparisons if c.model == model]
    scored = [c for c in rows if c.v1_hard_passed is not None]
    v1_passes = sum(1 for c in scored if c.v1_hard_passed)
    v1_fails = sum(1 for c in scored if not c.v1_hard_passed)
    v2_passes = sum(1 for c in scored if c.v2_hard_passed)
    v2_fails = sum(1 for c in scored if not c.v2_hard_passed)
    v1_on_topic_fails = sum(1 for c in scored if c.v1_on_topic and not c.v1_on_topic.passed)
    v2_on_topic_fails = sum(1 for c in scored if c.v2_on_topic and not c.v2_on_topic.passed)
    fail_to_pass = sum(1 for c in scored if c.verdict_change == "FAIL -> PASS")
    pass_to_fail = sum(1 for c in scored if c.verdict_change == "PASS -> FAIL")

    remaining: dict[str, int] = {}
    for c in scored:
        if c.v2_hard_passed:
            continue
        if c.v2_on_topic and not c.v2_on_topic.passed:
            remaining["on_topic_v2"] = remaining.get("on_topic_v2", 0) + 1
        for oc in c.other_checks:
            if not oc.passed:
                remaining[oc.name] = remaining.get(oc.name, 0) + 1

    lines = [
        f"MODEL: {model}",
        f"  total stored generations: {len(rows)}",
        f"  V1 hard passes: {v1_passes}   V1 hard failures: {v1_fails}",
        f"  V2 hard passes: {v2_passes}   V2 hard failures: {v2_fails}",
        f"  V1 on_topic failures: {v1_on_topic_fails}   V2 on_topic failures: {v2_on_topic_fails}",
        f"  FAIL -> PASS: {fail_to_pass}   PASS -> FAIL: {pass_to_fail}",
        f"  remaining V2 failures by check: {remaining}",
    ]
    return "\n".join(lines)


def _render_case(c: RetrospectiveCaseComparison) -> str:
    lines = [
        f"CASE: {c.case_id}  MODEL: {c.model}  RUN: {c.run_index}",
        f"  candidate: {c.candidate!r}",
        f"  V1: on_topic={'PASS' if c.v1_on_topic.passed else 'FAIL'}  overall={'PASS' if c.v1_hard_passed else 'FAIL'}",
        f"  V2: on_topic={'PASS' if c.v2_on_topic.passed else 'FAIL'}  overall={'PASS' if c.v2_hard_passed else 'FAIL'}  reason={c.v2_on_topic.reason}",
        f"  VERDICT CHANGE: {c.verdict_change}",
    ]
    other_fails = [oc for oc in c.other_checks if not oc.passed]
    if other_fails:
        lines.append(f"  other failing checks: {[(oc.name, oc.reason) for oc in other_fails]}")
    return "\n".join(lines)


def render_retrospective_report(comparisons: tuple[RetrospectiveCaseComparison, ...]) -> str:
    lines = [
        "Checkpoint 5.5A — Retrospective V1 vs V2 re-evaluation of the completed Checkpoint 5.5 benchmark",
        "(zero provider calls — stored candidate text re-evaluated only)",
        "",
        "=" * 70,
        "PER-MODEL AGGREGATE: V1 vs V2",
        "=" * 70,
        "",
    ]
    for model in _MODELS:
        lines.append(_model_summary(comparisons, model))
        lines.append("")

    lines.append("=" * 70)
    lines.append("CHANGED CASES: FAIL -> PASS")
    lines.append("=" * 70)
    lines.append("")
    for c in comparisons:
        if c.verdict_change == "FAIL -> PASS":
            lines.append(_render_case(c))
            lines.append("")

    lines.append("=" * 70)
    lines.append("CHANGED CASES: PASS -> FAIL (V2 found something V1 did not check)")
    lines.append("=" * 70)
    lines.append("")
    for c in comparisons:
        if c.verdict_change == "PASS -> FAIL":
            lines.append(_render_case(c))
            lines.append("")

    lines.append("=" * 70)
    lines.append("REMAINING V2 FAILURES (still failing after hardening)")
    lines.append("=" * 70)
    lines.append("")
    for c in comparisons:
        if c.v2_hard_passed is False:
            lines.append(_render_case(c))
            lines.append("")

    return "\n".join(lines)
