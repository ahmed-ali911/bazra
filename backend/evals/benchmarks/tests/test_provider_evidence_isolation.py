"""Checkpoint 5.8A, section 27 — mirrors evals/tests/test_isolation.py's
own discipline for this checkpoint's new design-only modules: no
provider/network/db import anywhere in them. `provider_evidence_checks`
and `provider_evidence_cases`/`provider_evidence_schemas` are pure
dataclasses/functions; `provider_evidence_cost_methodology` duplicates
pricing as plain Decimal constants (only ITS OWN dedicated test file,
test_provider_evidence_cost_methodology.py, is allowed to cross-reference
app/'s real constants — the same precedent test_prompt_fidelity.py and
test_generation.py already set).
"""

import inspect

from evals.benchmarks import (
    provider_evidence_blind_review,
    provider_evidence_cases,
    provider_evidence_checks,
    provider_evidence_cost_methodology,
    provider_evidence_decision_policy,
    provider_evidence_discriminator_subset,
    provider_evidence_plan,
    provider_evidence_rubric,
    provider_evidence_schemas,
)

_DESIGN_ONLY_MODULES = (
    provider_evidence_schemas,
    provider_evidence_checks,
    provider_evidence_rubric,
    provider_evidence_cases,
    provider_evidence_plan,
    provider_evidence_cost_methodology,
    provider_evidence_decision_policy,
    provider_evidence_blind_review,
    provider_evidence_discriminator_subset,
)

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "anthropic", "httpx", "requests", "sqlalchemy", "psycopg", "socket", "embedding", "google.genai", "google_genai",
)


def test_no_provider_network_db_or_embedding_import_in_any_design_module() -> None:
    for module in _DESIGN_ONLY_MODULES:
        import_lines = [
            line for line in inspect.getsource(module).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for forbidden in _FORBIDDEN_IMPORT_SUBSTRINGS:
            assert not any(forbidden in line.lower() for line in import_lines), (
                f"{module.__name__} unexpectedly imports {forbidden!r}: {import_lines}"
            )


def test_no_design_module_imports_app() -> None:
    """Stricter than the production-side `test_no_app_module_imports_the_evals_package`
    guarantee (evals/tests/test_isolation.py) — THIS checkpoint's own new
    design modules must not even READ from app/ (unlike generation.py,
    which legitimately does for real-call execution). Only this
    checkpoint's own cost-methodology TEST file is permitted to cross-
    reference app/'s pricing constants for a drift check."""
    for module in _DESIGN_ONLY_MODULES:
        import_lines = [
            line for line in inspect.getsource(module).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        offenders = [line for line in import_lines if "app." in line or line.strip().startswith(("import app", "from app"))]
        assert not offenders, f"{module.__name__} imports app/: {offenders}"


def test_evaluating_every_registered_check_makes_zero_aitrace_rows(db_session) -> None:
    """Same proof as evals/tests/test_isolation.py's own
    test_evaluating_the_full_frozen_suite_makes_zero_aitrace_rows, for
    this checkpoint's own new checks — running every registered check
    against a hand-built EvaluationCase must never touch the database."""
    from sqlalchemy import func, select, text

    from evals.benchmarks.provider_evidence_checks import CHECK_REGISTRY
    from evals.runner import evaluate_case
    from evals.schemas import EvaluationCase

    before = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()
    case = EvaluationCase(
        case_id="isolation_probe", purpose="p",
        facts={
            "required_tool_name": "propose_create_task", "actual_tool_name": "propose_create_task",
            "no_action_expected": False, "forbidden_phrases": (), "max_lines": 10,
            "no_plan_expected": False, "requested_output_language": None,
        },
        candidate="تمام، سجلتها.",
    )
    evaluate_case(case, tuple(CHECK_REGISTRY.values()))
    after = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()
    assert after == before
