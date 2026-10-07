"""Checkpoint 5.8A — the provider-evidence benchmark's own case contract.

This is a FOURTH, separate schema module in `evals/`, alongside
`evals.schemas` (the 5.4 generic offline evaluation contract) and
`evals.benchmarks.schemas` (the 5.5 real-generation-run contract) —
deliberately not a modification of either. `evals.schemas.EvaluationCase`
stays exactly what the 5.4/5.5/5.6 evaluators already depend on;
`ProviderEvidenceCase` below is a RICHER, benchmark-specific INPUT
SCENARIO contract (what Ahmed, this brief, calls the "case contract" in
section 13) — it is built once, frozen, and version-controlled here,
and only LATER (a future, separate, Ahmed-approved execution checkpoint)
combined with an actual (real or replayed) generation to construct an
`evals.schemas.EvaluationCase` for deterministic scoring. See
`provider_evidence_checks.py::build_evaluation_case` for that seam.

Provider-neutral by construction (section 13's own explicit requirement):
nothing here names a provider, a model id, or a provider-specific
response shape. `CandidateGeneration` below is the one, intentionally
narrow generic shape every future real-or-replayed generation must be
reduced to before any check runs — it mirrors
`app.modules.orchestrator.schemas.OrchestratorResult`'s own two-part
(text, tool_call) shape exactly (confirmed current at
orchestrator/schemas.py), never a raw Anthropic/Gemini response object.
"""

from dataclasses import dataclass, field

# Section 14 — a hypothesis, never ground truth (see each constant's own
# docstring note below and every case's own `expected_resource_class`
# field). "UNKNOWN" exists so a case can honestly decline to guess.
RESOURCE_CLASS_HYPOTHESES = ("ZERO_LLM", "LIGHTWEIGHT", "STANDARD", "POWERFUL", "UNKNOWN")

# Section 10's own twelve lettered families (A-L), given stable
# upper-snake-case names here since the brief's own letters are not
# stable identifiers (a future family could be inserted alphabetically).
WORKLOAD_FAMILIES = (
    "CASUAL_SOCIAL",
    "EMOTIONAL_LOW_ENERGY",
    "CLEAR_ACTION_REQUEST",
    "AMBIGUOUS_ACTION",
    "LOCAL_FACT_RETRIEVAL",
    "PERSONAL_CONTEXT_USE",
    "COMPLEX_REASONING",
    "INSTRUCTION_FOLLOWING",
    "FACTUAL_RESTRAINT",
    "BAZRA_SELF_DESCRIPTION",
    "HUMOR_LIGHTNESS",
    "COMPLEX_TOOL_ACTION_INTERPRETATION",
)

# Section 12 — language coverage tags. "AR_EG" is plain Egyptian Arabic;
# "AR_EG_EN_TERMS" is Egyptian Arabic carrying English technical/proper
# nouns inline (BAZRA's own real, observed everyday register — not a
# separate "code-switching" research category); "EN" is plain English;
# "MIXED" is used only when the sentence itself has no single dominant
# matrix language (rare — most "Arabic with English terms" input is
# AR_EG_EN_TERMS, not MIXED).
LANGUAGES = ("AR_EG", "AR_EG_EN_TERMS", "EN", "MIXED")

# Section 9 — the six SUBJECTIVE, human-review-only dimensions (see
# provider_evidence_rubric.py for their anchors). Kept as named
# constants here, not inline strings, so a case and the rubric can never
# silently drift apart on spelling.
READ_THE_ROOM = "read_the_room"
EGYPTIAN_ARABIC_NATURALNESS = "egyptian_arabic_naturalness"
BAZRA_IDENTITY_FIT = "bazra_identity_fit"
RESTRAINT = "restraint"
WARMTH = "warmth"
HUMOR_FIT = "humor_fit"
HUMAN_REVIEW_DIMENSION_NAMES = (
    READ_THE_ROOM, EGYPTIAN_ARABIC_NATURALNESS, BAZRA_IDENTITY_FIT, RESTRAINT, WARMTH, HUMOR_FIT,
)

# The real, current production tool catalog (chat/service.py::_TOOLS_OFFERED,
# confirmed current at that path) — named here as plain strings, never
# imported from app/ (evals/ must never import app/'s own modules; see
# test_provider_evidence_isolation.py). A case's `available_tools` is
# always a subset/equal to this tuple, or empty (the ZERO_LLM control
# family, where no tool catalog is offered at all because no LLM call
# is expected to happen).
ALL_PRODUCTION_TOOL_NAMES = (
    "propose_create_task", "propose_update_task", "propose_delete_task",
    "propose_create_event", "propose_update_event", "propose_delete_event",
    "propose_save_memory", "propose_forget_memory", "get_weather",
    "respond_with_text",
)

# The eight tools that create a ProposedAction (Phase 3 authority model)
# — "mutating" here means "requires a later, separate, explicit human
# confirmation before any real domain write happens," never "executes
# immediately." get_weather and respond_with_text are the two read-only/
# non-mutating tools and are deliberately excluded from this tuple.
MUTATING_TOOL_NAMES = (
    "propose_create_task", "propose_update_task", "propose_delete_task",
    "propose_create_event", "propose_update_event", "propose_delete_event",
    "propose_save_memory", "propose_forget_memory",
)


@dataclass(frozen=True)
class ConversationTurn:
    """A plain role/content pair for a case's own frozen preceding
    context — deliberately the same minimal shape as
    `app.modules.orchestrator.schemas.HistoryTurn`, but defined
    independently here (not imported) so this benchmark package never
    depends on `app/` at all (see test_provider_evidence_isolation.py)."""

    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class CandidateGeneration:
    """What ANY real-or-replayed generation must be reduced to before a
    check runs — mirrors OrchestratorResult's own (text, tool_call)
    split exactly. `tool_name`/`tool_arguments` are both None whenever
    no tool was called, or the model called `respond_with_text` (which
    creates no ProposedAction and reaches no domain module at all) —
    both reduce to "ordinary conversational output" for every check in
    this benchmark. Provider-neutral and framework-neutral: nothing
    about where `text`/`tool_name` came from is recorded here."""

    text: str | None
    tool_name: str | None
    tool_arguments: dict | None


@dataclass(frozen=True)
class ProviderEvidenceCase:
    """Checkpoint 5.8A, section 13 — the frozen case contract. Every
    field the brief's own minimum list names is present; `check_parameters`
    is the one necessary addition beyond that literal list (the brief's
    own section 13 says "at minimum," not "exactly") — it carries the
    small, per-case, check-specific parameters (e.g. a required tool
    name, a line-count ceiling, a list of forbidden phrases) that the
    deterministic hard checks in `provider_evidence_checks.py` read; see
    each check's own docstring for which keys it looks for.

    `is_control` (section 10.E) marks a case whose own accepted
    production architecture already guarantees ZERO_LLM involvement —
    these exist to PROVE that guarantee holds, not to compare models
    against each other; a future execution plan must not spend real
    provider-call budget on a control case except deliberately, as a
    sanity check.

    `pairs_with` (section 11) names another case_id this case is
    contrastive with — differing by exactly one factor, so the PAIR
    together tests actual understanding rather than keyword matching.
    The link is symmetric by convention (both halves name each other);
    see test_provider_evidence_cases.py for the mutual-reference proof.

    No real personal data anywhere (section 23/28) — every name,
    context fact, and phrasing here is synthetic, invented for this
    module specifically (never Ahmed's own real tasks/calendar/names).
    """

    case_id: str
    version: str
    family: str  # one of WORKLOAD_FAMILIES
    language: str  # one of LANGUAGES
    user_message: str
    synthetic_context: str
    conversation_history: tuple[ConversationTurn, ...]
    available_tools: tuple[str, ...]
    expected_behavior: str
    forbidden_behavior: tuple[str, ...]
    hard_checks: tuple[str, ...]
    human_review_dimensions: tuple[str, ...]
    expected_resource_class: str  # one of RESOURCE_CLASS_HYPOTHESES
    check_parameters: dict = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    is_control: bool = False
    pairs_with: str | None = None
