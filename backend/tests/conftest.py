from collections.abc import Generator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.main import app

TEST_DB_NAME = "bazra_test"


def _psycopg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://")


def _test_database_url() -> str:
    base = settings.database_url.rsplit("/", 1)[0]
    return f"{base}/{TEST_DB_NAME}"


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database() -> Generator[None, None, None]:
    """Drop and recreate the test database once per test session, so every
    run starts from a clean database rather than accumulating state.
    """
    admin_dsn = _psycopg_dsn(settings.database_url)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        # A prior run's connection to bazra_test may not have closed cleanly
        # (crashed process, hung pool) — terminate any survivors first, or
        # DROP DATABASE fails with "database is being accessed by other users".
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (TEST_DB_NAME,),
        )
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
        conn.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    yield


@pytest.fixture(scope="session")
def test_engine() -> Generator[Engine, None, None]:
    engine = create_engine(_test_database_url())
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine: Engine) -> Generator[Session, None, None]:
    session = sessionmaker(bind=test_engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
