"""Checkpoint 5.5 — the benchmark's own result contract. Separate from
`evals.schemas` (the 5.4 offline contract) because a benchmark run
carries real-provider-specific facts (model identifier, run index,
token usage, latency, provider failure) that a pure offline fixture
case never has — see each dataclass's own docstring.
"""

from dataclasses import dataclass

# Checkpoint 5.5, section 22A — three interpretation buckets, assigned
# by a static, deterministic lookup table (interpretation.py), NEVER by
# an LLM. "N/A_PASSED" is not one of the three the brief names; it
# exists only so a passing check has an explicit, non-null value here
# too, rather than a magic None a future reader might misread.
FailureInterpretation = str  # "CLEAR_SAFETY_VIOLATION" | "POSSIBLE_EVALUATOR_SENSITIVITY" | "UNRESOLVED_REQUIRES_HUMAN_REVIEW" | "N/A_PASSED"


@dataclass(frozen=True)
class BenchmarkScenario:
    """A production-realistic proactive-narration input — mirrors the
    REAL production boundary exactly (signal_type/title/priority, see
    orchestrator_service.generate_app_opened_narration_text's own
    docstring) and nothing more. Synthetic titles only (Checkpoint 5.5
    section 27) — no real personal data."""

    case_id: str
    signal_type: str
    title: str
    priority: str | None


@dataclass(frozen=True)
class GenerationOutcome:
    """Either a successful generation (text + token usage) or a
    provider failure (category from the 5.1 taxonomy) — never both.
    `text is None` iff `failure_category is not None`."""

    model: str
    text: str | None
    failure_category: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass(frozen=True)
class CheckInterpretation:
    """One deterministic check's verdict PLUS its separately-assigned
    failure interpretation (section 22A) — the interpretation is
    metadata for human analysis; it never overrides `passed`."""

    check_name: str
    passed: bool
    reason: str
    interpretation: FailureInterpretation


@dataclass(frozen=True)
class BenchmarkCaseResult:
    """One (scenario, model, run_index) generation's full evidence.
    `hard_passed` is None when generation itself failed (a provider
    failure is not a candidate safety failure — section 24 — so there
    is no candidate text to evaluate at all, and this must not be
    conflated with a hard-check FAIL)."""

    scenario_case_id: str
    model: str
    run_index: int
    generation: GenerationOutcome
    hard_passed: bool | None
    checks: tuple[CheckInterpretation, ...]


@dataclass(frozen=True)
class ModelSummary:
    """Simple, transparent per-model aggregation — every field is a
    plain count or a list of named scenarios, never an opaque weighted
    score (section 10/19)."""

    model: str
    generations_attempted: int
    generations_completed: int
    provider_failures_by_category: dict[str, int]
    hard_passes: int
    hard_failures: int
    pass_rate: float | None
    failures_by_check: dict[str, int]
    failures_by_scenario: dict[str, int]
    inconsistent_scenarios: tuple[str, ...]
    clear_safety_violations: int
    possible_evaluator_sensitivity: int
    unresolved_requires_human_review: int
    total_input_tokens: int
    total_output_tokens: int
    average_latency_ms: float | None


@dataclass(frozen=True)
class BenchmarkReport:
    """Reproducibility metadata (section 33) + every raw result + the
    per-model summaries — the human-review report (report.py) renders
    this, it never discards the underlying per-case evidence."""

    suite_name: str
    suite_version: str
    timestamp: str
    git_sha: str
    scenario_count: int
    repetitions: int
    models: tuple[str, ...]
    results: tuple[BenchmarkCaseResult, ...]
    summaries: dict[str, ModelSummary]
