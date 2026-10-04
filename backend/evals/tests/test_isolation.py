"""Checkpoint 5.4, sections 4/5/36 — proves the harness runs fully
offline and is never imported by production code, as a structural
guarantee rather than a convention to remember.
"""

import pathlib

import pytest


def test_no_app_module_imports_the_evals_package() -> None:
    """The hard invariant: production code must never import this
    harness, so no production request can ever wait on, or be affected
    by, offline evaluation (section 5)."""
    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import evals" in text or "from evals" in text:
            offenders.append(str(path))
    assert offenders == [], f"production code must never import evals/: {offenders}"


def test_harness_modules_have_no_provider_network_or_db_import() -> None:
    import inspect

    from evals import proactive_narration, report, runner, schemas

    for module in (schemas, runner, proactive_narration, report):
        import_lines = [
            line for line in inspect.getsource(module).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for forbidden in ("anthropic", "httpx", "requests", "sqlalchemy", "psycopg", "socket"):
            assert not any(forbidden in line.lower() for line in import_lines), (
                f"{module.__name__} unexpectedly imports {forbidden!r}"
            )


def test_evaluating_the_full_frozen_suite_makes_zero_aitrace_rows(db_session) -> None:
    """Checkpoint 5.4, section 36 — 0 AiTrace rows from offline
    evaluation. Uses the real conftest.py db_session fixture only to
    OBSERVE the row count before/after; the harness itself never
    touches the database."""
    from sqlalchemy import func, select, text

    from evals.proactive_narration import ALL_CHECKS, PROACTIVE_NARRATION_SUITE
    from evals.runner import evaluate_suite

    before = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()
    evaluate_suite(PROACTIVE_NARRATION_SUITE, ALL_CHECKS)
    after = db_session.execute(select(func.count()).select_from(text("ai_traces"))).scalar_one()

    assert after == before
