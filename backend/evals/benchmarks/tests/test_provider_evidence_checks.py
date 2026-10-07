"""Checkpoint 5.8A, section 27 — proves every NEW deterministic check
fires correctly and repeatably against hand-written, synthetic candidate
text, with zero model/network involvement (the same discipline every
existing evals/tests/test_*.py module already follows). Also proves
`build_evaluation_case` correctly assembles the EvaluationCase shape
every check expects.
"""

from evals.benchmarks.provider_evidence_checks import (
    CHECK_REGISTRY,
    build_evaluation_case,
    check_explicit_language_instruction_followed,
    check_forbidden_phrase_absent,
    check_line_count_constraint,
    check_no_action_attempted,
    check_no_plan_dump,
    check_required_tool_selected,
)
from evals.benchmarks.provider_evidence_schemas import CandidateGeneration
from evals.benchmarks.provider_evidence_cases import CASES_BY_ID
from evals.schemas import EvaluationCase


def test_check_required_tool_selected_passes_on_match() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"required_tool_name": "propose_create_task", "actual_tool_name": "propose_create_task"}, candidate="")
    result = check_required_tool_selected(case)
    assert result.passed is True


def test_check_required_tool_selected_fails_on_mismatch() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"required_tool_name": "propose_create_task", "actual_tool_name": "propose_create_event"}, candidate="")
    result = check_required_tool_selected(case)
    assert result.passed is False
    assert "propose_create_event" in result.reason


def test_check_required_tool_selected_passes_when_not_required() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"actual_tool_name": "anything"}, candidate="")
    assert check_required_tool_selected(case).passed is True


def test_check_no_action_attempted_fails_on_mutating_tool() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"no_action_expected": True, "actual_tool_name": "propose_create_task"}, candidate="")
    result = check_no_action_attempted(case)
    assert result.passed is False


def test_check_no_action_attempted_passes_on_respond_with_text() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"no_action_expected": True, "actual_tool_name": "respond_with_text"}, candidate="")
    assert check_no_action_attempted(case).passed is True


def test_check_no_action_attempted_passes_when_no_tool_called() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"no_action_expected": True, "actual_tool_name": None}, candidate="")
    assert check_no_action_attempted(case).passed is True


def test_check_forbidden_phrase_absent_fails_on_match_case_insensitive() -> None:
    case = EvaluationCase(
        case_id="x", purpose="p",
        facts={"forbidden_phrases": ("Tasks module",)},
        candidate="بقدر أساعدك من خلال tasks MODULE والتقويم",
    )
    assert check_forbidden_phrase_absent(case).passed is False


def test_check_forbidden_phrase_absent_passes_when_clean() -> None:
    case = EvaluationCase(
        case_id="x", purpose="p",
        facts={"forbidden_phrases": ("Tasks module",)},
        candidate="أشيل معاك زحمة دماغك وأفكرك باللي يهمك",
    )
    assert check_forbidden_phrase_absent(case).passed is True


def test_check_no_plan_dump_fails_on_numbered_list() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"no_plan_expected": True}, candidate="تمام:\n1. كلم حسين\n2. راجع العقد")
    assert check_no_plan_dump(case).passed is False


def test_check_no_plan_dump_fails_on_bullet_list() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"no_plan_expected": True}, candidate="- كلم حسين\n- راجع العقد")
    assert check_no_plan_dump(case).passed is False


def test_check_no_plan_dump_passes_on_plain_prose() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"no_plan_expected": True}, candidate="تمام، سامعك. خد وقتك.")
    assert check_no_plan_dump(case).passed is True


def test_check_line_count_constraint_fails_when_exceeded() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"max_lines": 2}, candidate="سطر واحد\nسطر اتنين\nسطر تلاتة")
    assert check_line_count_constraint(case).passed is False


def test_check_line_count_constraint_passes_within_limit() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"max_lines": 2}, candidate="سطر واحد\nسطر اتنين")
    assert check_line_count_constraint(case).passed is True


def test_check_explicit_language_instruction_followed_fails_on_wrong_script() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"requested_output_language": "latin"}, candidate="تمام هو ده المعنى باختصار")
    assert check_explicit_language_instruction_followed(case).passed is False


def test_check_explicit_language_instruction_followed_passes_on_requested_script() -> None:
    case = EvaluationCase(case_id="x", purpose="p", facts={"requested_output_language": "latin"}, candidate="Caching means storing a result so it can be reused without recomputation.")
    assert check_explicit_language_instruction_followed(case).passed is True


def test_check_registry_is_repeatable_across_calls() -> None:
    """Same input, same output, every time — no hidden state, no
    randomness, anywhere in CHECK_REGISTRY."""
    case = EvaluationCase(case_id="x", purpose="p", facts={"required_tool_name": "propose_create_task", "actual_tool_name": "propose_create_task"}, candidate="تمام، سجلتها.")
    check = CHECK_REGISTRY["required_tool_selected"]
    results = [check(case) for _ in range(5)]
    assert len({(r.passed, r.reason) for r in results}) == 1


def test_build_evaluation_case_defaults_empty_text_to_empty_string() -> None:
    case = CASES_BY_ID["a1_zahqan_restraint"]
    generation = CandidateGeneration(text=None, tool_name="propose_create_task", tool_arguments={})
    evaluation_case = build_evaluation_case(case, generation)
    assert evaluation_case.candidate == ""
    assert evaluation_case.facts["actual_tool_name"] == "propose_create_task"


def test_build_evaluation_case_merges_check_parameters_into_facts() -> None:
    case = CASES_BY_ID["c1_hussein_clear_action"]
    generation = CandidateGeneration(text="تمام", tool_name="propose_create_task", tool_arguments={"title": "اتصل بحسين"})
    evaluation_case = build_evaluation_case(case, generation)
    assert evaluation_case.facts["required_tool_name"] == "propose_create_task"
    result = CHECK_REGISTRY["required_tool_selected"](evaluation_case)
    assert result.passed is True
