"""Proves the spaces.user_id migration (a1b0fe5e4ba2) refuses to guess
an owner when its precondition doesn't hold — real safety, not just
asserted safety. Uses its own throwaway database, entirely separate
from the shared bazra_test database every other test relies on being at
`head` throughout the session — downgrading/re-upgrading that shared
database mid-session would disrupt every other test.
"""

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import settings

THROWAWAY_DB_NAME = "bazra_test_migration_guard"

# The revision immediately before the one under test, per
# migrations/versions/a1b0fe5e4ba2_add_spaces_user_id.py's own
# down_revision.
_PREVIOUS_REVISION = "25a290c68158"
_MIGRATION_UNDER_TEST = "a1b0fe5e4ba2"


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _throwaway_db_url() -> str:
    base = settings.database_url.rsplit("/", 1)[0]
    return f"{base}/{THROWAWAY_DB_NAME}"


@pytest.fixture()
def throwaway_db_url():
    admin_dsn = _psycopg_dsn(settings.database_url)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (THROWAWAY_DB_NAME,),
        )
        conn.execute(f"DROP DATABASE IF EXISTS {THROWAWAY_DB_NAME}")
        conn.execute(f"CREATE DATABASE {THROWAWAY_DB_NAME}")

    yield _throwaway_db_url()

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (THROWAWAY_DB_NAME,),
        )
        conn.execute(f"DROP DATABASE IF EXISTS {THROWAWAY_DB_NAME}")


def _alembic_config(db_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def test_migration_fails_loudly_on_multiple_users_rather_than_guessing_an_owner(throwaway_db_url: str) -> None:
    config = _alembic_config(throwaway_db_url)
    command.upgrade(config, _PREVIOUS_REVISION)

    engine = create_engine(throwaway_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (password_hash, created_at, updated_at) VALUES ('x', now(), now())")
            )
            conn.execute(
                text("INSERT INTO users (password_hash, created_at, updated_at) VALUES ('y', now(), now())")
            )
    finally:
        engine.dispose()

    with pytest.raises(Exception, match="Refusing to backfill"):
        command.upgrade(config, _MIGRATION_UNDER_TEST)


def test_migration_fails_loudly_on_multiple_spaces_for_one_user_rather_than_guessing(throwaway_db_url: str) -> None:
    config = _alembic_config(throwaway_db_url)
    command.upgrade(config, _PREVIOUS_REVISION)

    engine = create_engine(throwaway_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (password_hash, created_at, updated_at) VALUES ('x', now(), now())")
            )
            # A second space, alongside the one already bulk-seeded by an
            # earlier migration — exactly one user, but now ambiguous
            # which space(s) actually belong to them.
            conn.execute(
                text(
                    "INSERT INTO spaces (name, is_default, created_at, updated_at) "
                    "VALUES ('Second Space', false, now(), now())"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(Exception, match="Refusing to backfill"):
        command.upgrade(config, _MIGRATION_UNDER_TEST)


def test_migration_succeeds_and_deletes_orphaned_space_when_zero_users_exist(throwaway_db_url: str) -> None:
    """The normal, expected case for every fresh install/fresh test run —
    not an error case. Confirms the earlier-seeded, unowned space is
    removed safely (nothing could reference it yet) rather than the
    migration raising.
    """
    config = _alembic_config(throwaway_db_url)
    command.upgrade(config, _PREVIOUS_REVISION)

    engine = create_engine(throwaway_db_url)
    try:
        with engine.begin() as conn:
            space_count_before = conn.execute(text("SELECT count(*) FROM spaces")).scalar_one()
        assert space_count_before == 1  # the earlier migration's unconditional bulk-seed
    finally:
        engine.dispose()

    command.upgrade(config, _MIGRATION_UNDER_TEST)  # must not raise

    engine = create_engine(throwaway_db_url)
    try:
        with engine.begin() as conn:
            space_count_after = conn.execute(text("SELECT count(*) FROM spaces")).scalar_one()
        assert space_count_after == 0
    finally:
        engine.dispose()
