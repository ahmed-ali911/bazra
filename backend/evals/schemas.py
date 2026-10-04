"""Checkpoint 5.4 — the generic evaluation-case/result contract.

Deliberately NOT specific to proactive narration, or even to prose
generation at all — `candidate` is an untyped (`object`) field on
purpose (see EvaluationCase's own docstring) so a later checkpoint can
represent a tool-selection decision or a source-routing choice without
replacing this contract. Nothing here implements any of those future
evaluators — see evals/tests/test_future_extensibility.py for a
type-level proof that the contract itself doesn't block them.

`purpose`/`tier` are plain strings, not the real `ModelCallPurpose`/
`IntelligenceTier` Literal types from `app.modules.model_router.schemas`
— deliberately: today's real values happen to match that vocabulary,
but a future tool-selection or source-routing evaluation case may not
correspond to any model-call purpose at all, and this contract must
not block that by tying itself to a Literal built for a different
concern.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckResult:
    """One named, deterministic check's own verdict against a single
    candidate. `hard` (Checkpoint 5.4, section 9) marks whether failing
    THIS check fails the whole case — every check in the V1 proactive
    narration suite sets this True; the field exists so a future,
    explicitly-non-gating informational check has somewhere to say so,
    without needing a contract change to add it. `reason` is always a
    concise, concrete explanation — never a bare score."""

    name: str
    passed: bool
    reason: str
    hard: bool = True


@dataclass(frozen=True)
class EvaluationCase:
    """A single, frozen, version-controlled evaluation case.

    `candidate` (Checkpoint 5.4, section 7) is deliberately untyped
    (`object`, not `str`) — for proactive narration today it is always
    a plain string (the candidate narration text), but the field itself
    must not block a future case representing a tool-selection decision
    (e.g. a bare string enum like "WEB_SEARCH") or a structured
    source-routing result. Checks interpret `candidate`/`facts`
    themselves; this dataclass has no opinion about their shape.

    `expected_pass`/`expected_failed_checks` (Checkpoint 5.4, section
    34) are OPTIONAL, used only by this checkpoint's own frozen
    fixtures to self-test the harness's detection accuracy — a future
    real (non-fixture) evaluation case, e.g. comparing two live model
    outputs with no known-correct answer, legitimately has neither and
    leaves them at their default None/() — this is expected, not a
    missing field.

    No DB object, no secrets, no real user data — every field here must
    be safe to commit to version control verbatim (section 28).
    """

    case_id: str
    purpose: str
    facts: dict
    candidate: object
    tier: str | None = None
    tags: tuple[str, ...] = ()
    expected_pass: bool | None = None
    expected_failed_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    """`passed` is computed by the caller (see runner.py's
    evaluate_case) as "no hard check failed" — never averaged, never
    weighted (section 9/10: a safety failure is never offset by good
    style)."""

    case_id: str
    passed: bool
    checks: tuple[CheckResult, ...]

    @property
    def failed_checks(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if not c.passed)


@dataclass(frozen=True)
class EvaluationSuite:
    """A named, versioned collection of cases — `version` (section 30)
    is a plain code constant, not a DB row, just enough so a future
    report can state which check-set produced a result."""

    name: str
    version: str
    cases: tuple[EvaluationCase, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SuiteResult:
    """Simple, transparent aggregation only (section 33) — total/
    passed/failed counts and a per-check failure breakdown, never an
    opaque weighted score. `matches_expectations` is the suite's own
    explicit acceptance rule for a FROZEN fixture suite specifically
    (section 33's "all hard-invariant cases behave as expected"): True
    iff every case whose `expected_pass` is set actually evaluated to
    that same value — the harness-self-test property, distinct from
    "every case passed" (a FAIL fixture is SUPPOSED to fail; the suite
    still "matches expectations" when it does)."""

    suite_name: str
    suite_version: str
    results: tuple[EvaluationResult, ...]
    cases_by_id: dict

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return self.total - self.passed_count

    def failures_by_check_name(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            for check in result.failed_checks:
                counts[check.name] = counts.get(check.name, 0) + 1
        return counts

    @property
    def matches_expectations(self) -> bool:
        for result in self.results:
            case = self.cases_by_id[result.case_id]
            if case.expected_pass is not None and result.passed != case.expected_pass:
                return False
        return True
