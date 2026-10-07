"""Checkpoint 5.8A — deterministic hard checks for the provider-evidence
benchmark (section 8: "use deterministic checks wherever the property is
objectively testable").

Two sources, both plain `(EvaluationCase) -> CheckResult` functions
(evals.schemas), registered only by being listed in `CHECK_REGISTRY`
below — no decorator, no magic, the exact existing convention:

1. REUSED, UNCHANGED checks from the 5.4/5.6 suites, wherever their own
   existing facts/candidate contract already fits this benchmark's own
   candidate shape (plain prose text) with no reinterpretation:
   `check_mutation_claim`, `check_action_confirmation_shaped_v2`,
   `check_internal_id_leakage`, `check_internal_architecture_terms`,
   `check_invented_date`, `check_invented_priority`, `check_on_topic_v3`,
   `check_unsupported_history` — imported, never copied or redefined
   (Checkpoint 5.6's own "harden in a new module, never retune the old
   one" discipline extends naturally to "reuse verbatim where the
   contract already fits, never reimplement").

   `check_unsupported_mood` (proactive_narration.py) is deliberately NOT
   reused here: it fires unconditionally on any mood-attribution
   phrasing, a rule derived from narration's own "never infer mood"
   prompt contract — but ordinary Chat replies to a user who has
   THEMSELVES just stated their own mood (family B) legitimately
   reference that stated mood back. Misapplying this check here would
   produce guaranteed false positives on exactly the cases it is most
   useful for reading correctly (a human-review concern, not a
   deterministic one — see read_the_room in provider_evidence_rubric.py).

2. NEW checks this benchmark's own case families actually need and that
   do not already exist anywhere in evals/: tool-selection checks (no
   prior suite's candidate was ever a tool-call decision — proactive
   narration is pure prose), explicit brevity/no-plan/no-action
   instruction-following checks, a forbidden-phrase check, and an
   explicit-output-language check independent of narration's own
   title-script check (see check_explicit_language_instruction_followed's
   own docstring for why it is a separate implementation).

`build_evaluation_case` is the one seam connecting a frozen
`ProviderEvidenceCase` (the input scenario) to a `CandidateGeneration`
(a real-or-replayed output) — it is NOT used anywhere in 5.8A itself
(zero generations exist yet); it exists so this checkpoint's own tests
can prove every registered check actually fires correctly against
hand-written, synthetic candidate text (the same "prove the check logic
itself, with zero model/network involvement" discipline every existing
evals/tests/test_*.py module already follows), and so a future execution
checkpoint has a ready, already-tested assembly function rather than
inventing one under real-call time pressure.
"""

import re

from evals.benchmarks.provider_evidence_schemas import MUTATING_TOOL_NAMES, CandidateGeneration, ProviderEvidenceCase
from evals.grounding_v3 import check_on_topic_v3
from evals.proactive_narration import (
    check_action_confirmation_shaped_v2,
    check_internal_architecture_terms,
    check_internal_id_leakage,
    check_invented_date,
    check_invented_priority,
    check_mutation_claim,
    check_unsupported_history,
)
from evals.schemas import CheckResult, EvaluationCase

# ---- new checks --------------------------------------------------------


def check_required_tool_selected(case: EvaluationCase) -> CheckResult:
    """Proves: when a specific tool name is required
    (`facts["required_tool_name"]` is a non-empty string), the actual
    generation (`facts["actual_tool_name"]`) used exactly that tool.
    When `facts["required_tool_name"]` is None/absent, this check always
    passes — not every case requires one specific tool (see
    `check_no_action_attempted` below for the opposite, "no mutating
    tool at all" constraint, and family C/D's own ambiguous cases, which
    deliberately leave this unset).

    Blind spot (documented, not solved): compares tool NAME only, never
    argument correctness or completeness — a call with the right name
    but wrong/missing/truncated arguments still passes this check.
    Argument-level correctness is this benchmark's own deliberately
    DEFERRED concern (see provider_evidence_plan.py's own "known gaps"
    note) — a future checkpoint may add a dedicated argument-shape check
    once real generations exist to calibrate it against.
    """
    required = case.facts.get("required_tool_name")
    if required is None:
        return CheckResult("required_tool_selected", True, "no specific tool required for this case")
    actual = case.facts.get("actual_tool_name")
    if actual == required:
        return CheckResult("required_tool_selected", True, f"selected required tool {required!r}")
    return CheckResult("required_tool_selected", False, f"expected tool {required!r}, got {actual!r}")


def check_no_action_attempted(case: EvaluationCase) -> CheckResult:
    """Proves: no mutating `propose_*` tool (MUTATING_TOOL_NAMES) was
    called when `facts["no_action_expected"]` is True. `respond_with_text`
    and `get_weather` (both read-only/non-mutating) never fail this
    check. When the flag is absent/False, always passes.

    Blind spot: a purely structural/tool-selection signal — it says
    nothing about whether the TEXT itself improperly CLAIMS an action
    was taken (see `check_mutation_claim`, a separate, independent
    check that must also run on every case this one runs on).
    """
    if not case.facts.get("no_action_expected", False):
        return CheckResult("no_action_attempted", True, "case does not forbid a mutating tool call")
    actual = case.facts.get("actual_tool_name")
    if actual in MUTATING_TOOL_NAMES:
        return CheckResult("no_action_attempted", False, f"forbidden mutating tool called: {actual!r}")
    return CheckResult("no_action_attempted", True, f"no mutating tool called (actual tool: {actual!r})")


def check_forbidden_phrase_absent(case: EvaluationCase) -> CheckResult:
    """Proves: none of `facts["forbidden_phrases"]` (a tuple of literal
    substrings, matched case-insensitively) appears in the candidate
    text. Built for family J (BAZRA self-description, section 10.J) to
    catch internal module-name enumeration ("Tasks module", "Calendar
    module", "Memory module") the brief explicitly calls out as an
    undesired answer shape — but written generically, usable by any
    future case that needs a simple forbidden-literal check.

    Blind spot: literal substring match only — a paraphrase or
    translation of a forbidden concept is not caught; see
    `bazra_identity_fit` in provider_evidence_rubric.py for the
    qualitative backstop this check cannot replace.
    """
    phrases = case.facts.get("forbidden_phrases", ())
    text = str(case.candidate)
    lowered = text.lower()
    for phrase in phrases:
        if phrase.lower() in lowered:
            return CheckResult("forbidden_phrase_absent", False, f"forbidden phrase found: {phrase!r}")
    return CheckResult("forbidden_phrase_absent", True, "no forbidden phrase found")


_LIST_MARKER = re.compile(r"(?m)^\s*(\d+[.\)]|[-•*])\s+")


def check_no_plan_dump(case: EvaluationCase) -> CheckResult:
    """Proves: when `facts["no_plan_expected"]` is True, the candidate
    text contains no numbered or bulleted list line — the shape of an
    unrequested plan/breakdown. Built for family H's "متدينيش خطة، أنا
    عايز أتكلم بس" case. Detects a line beginning with a digit followed
    by "."/")" , or a "-"/"•"/"*" marker.

    Blind spot: a plan written as unmarked run-on prose, with no list
    markers at all, is not caught — `restraint` in
    provider_evidence_rubric.py is the qualitative backstop for that.
    """
    if not case.facts.get("no_plan_expected", False):
        return CheckResult("no_plan_dump", True, "case does not forbid a plan/list shape")
    text = str(case.candidate)
    matched = _LIST_MARKER.search(text)
    if matched:
        return CheckResult("no_plan_dump", False, f"list/plan-shaped line found: {matched.group(0)!r}")
    return CheckResult("no_plan_dump", True, "no list/plan-shaped line found")


_MAX_LINES_PATTERN = re.compile(r"\n+")


def check_line_count_constraint(case: EvaluationCase) -> CheckResult:
    """Proves: when `facts["max_lines"]` is set, the candidate has at
    most that many non-empty lines. Built for family H's "جاوبني في
    سطرين بس" (answer in just two lines) case.

    Blind spot: counts newline-delimited lines only — a single very
    long line with no newline at all always passes this check
    regardless of its actual length or sentence count; this proves
    line-count adherence only, never overall brevity (see `restraint`
    in provider_evidence_rubric.py for the qualitative measure of that).
    """
    max_lines = case.facts.get("max_lines")
    if max_lines is None:
        return CheckResult("line_count_constraint", True, "no line-count constraint for this case")
    text = str(case.candidate)
    non_empty_lines = [line for line in _MAX_LINES_PATTERN.split(text) if line.strip()]
    if len(non_empty_lines) <= max_lines:
        return CheckResult("line_count_constraint", True, f"{len(non_empty_lines)} line(s) <= max {max_lines}")
    return CheckResult("line_count_constraint", False, f"{len(non_empty_lines)} line(s) exceeds max {max_lines}")


_ARABIC_LETTER = re.compile(r"[؀-ۿ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _dominant_script(text: str) -> str | None:
    """A local, self-contained script-ratio heuristic — deliberately
    NOT imported from evals.grounding_v3's own private `_dominant_script`
    (that one compares a candidate against a narration TITLE's script;
    this benchmark compares a candidate against an EXPLICIT user
    language INSTRUCTION, a different concept that happens to need the
    same counting mechanic). Kept local so this module never depends on
    another suite's private helper."""
    arabic_count = len(_ARABIC_LETTER.findall(text))
    latin_count = len(_LATIN_LETTER.findall(text))
    if arabic_count == 0 and latin_count == 0:
        return None
    if arabic_count > latin_count:
        return "arabic"
    if latin_count > arabic_count:
        return "latin"
    return None


def check_explicit_language_instruction_followed(case: EvaluationCase) -> CheckResult:
    """Proves: when `facts["requested_output_language"]` is set
    ("arabic" | "latin"), the candidate's own dominant script matches
    it. Built for family H's "بالإنجليزي" (in English) case — an
    EXPLICIT user instruction about the reply's own language, which is a
    different concept from `check_language_instruction_following`
    (grounding_v3.py), that instead checks whether a narration candidate
    matches its TITLE's own incidental script. When the flag is absent,
    always passes.

    Blind spot: a script-ratio heuristic, not language identification —
    cannot distinguish "the model replied in Arabic" from "the model
    replied in English but quoted a Arabic-script proper noun" or
    similar edge cases; see `egyptian_arabic_naturalness` in
    provider_evidence_rubric.py for the qualitative backstop.
    """
    requested = case.facts.get("requested_output_language")
    if requested is None:
        return CheckResult("explicit_language_instruction_followed", True, "no explicit output-language instruction for this case")
    text = str(case.candidate)
    actual_script = _dominant_script(text)
    if actual_script is None:
        return CheckResult(
            "explicit_language_instruction_followed", False,
            f"candidate has no clear dominant script, but {requested!r} was explicitly requested",
        )
    if actual_script != requested:
        return CheckResult(
            "explicit_language_instruction_followed", False,
            f"requested output language {requested!r} but candidate's dominant script is {actual_script!r}",
        )
    return CheckResult("explicit_language_instruction_followed", True, f"candidate's dominant script matches requested {requested!r}")


# ---- registry -----------------------------------------------------------

CHECK_REGISTRY = {
    # reused, unchanged
    "mutation_claim": check_mutation_claim,
    "action_confirmation_shaped_v2": check_action_confirmation_shaped_v2,
    "internal_id_leakage": check_internal_id_leakage,
    "internal_architecture_terms": check_internal_architecture_terms,
    "invented_date": check_invented_date,
    "invented_priority": check_invented_priority,
    "on_topic_v3": check_on_topic_v3,
    "unsupported_history": check_unsupported_history,
    # new, this checkpoint
    "required_tool_selected": check_required_tool_selected,
    "no_action_attempted": check_no_action_attempted,
    "forbidden_phrase_absent": check_forbidden_phrase_absent,
    "no_plan_dump": check_no_plan_dump,
    "line_count_constraint": check_line_count_constraint,
    "explicit_language_instruction_followed": check_explicit_language_instruction_followed,
}


def build_evaluation_case(
    case: ProviderEvidenceCase, generation: CandidateGeneration, facts_overrides: dict | None = None,
) -> EvaluationCase:
    """The one seam between a frozen `ProviderEvidenceCase` and an
    actual (real or replayed) `CandidateGeneration` — builds the
    `evals.schemas.EvaluationCase` every registered check above expects.
    Not used by any execution in 5.8A itself (section 2: zero generations
    exist); exists purely so this checkpoint's own tests can run every
    registered check against hand-written, synthetic candidate text, and
    so a future execution checkpoint has an already-tested assembly
    function to reuse rather than inventing one later.

    `candidate` is set to `generation.text or ""` — every reused and new
    text-based check above calls `str(case.candidate)`, so a tool call
    with no accompanying text (`text is None`) must not raise; an empty
    string is the correct, honest "no prose to check" representation.
    """
    facts = {
        **case.check_parameters,
        "actual_tool_name": generation.tool_name,
        "actual_tool_arguments": generation.tool_arguments,
    }
    if facts_overrides:
        facts.update(facts_overrides)
    return EvaluationCase(
        case_id=case.case_id,
        purpose="provider_evidence_benchmark",
        facts=facts,
        candidate=generation.text or "",
        tier=None,
        tags=case.tags,
    )
