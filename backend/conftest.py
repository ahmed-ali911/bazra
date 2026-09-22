from collections.abc import Generator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database import get_db
from app.main import app

TEST_DB_NAME = "bazra_test"


def _psycopg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://")


def _test_database_url() -> str:
    base = settings.database_url.rsplit("/", 1)[0]
    return f"{base}/{TEST_DB_NAME}"


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database() -> Generator[None, None, None]:
    """Drop and recreate the test database once per test session, then apply
    every real migration to it, so every test — not just test_migrations.py —
    runs against actual tables produced by the actual migration files, rather
    than a schema improvised via Base.metadata.create_all().
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

    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", _test_database_url())
    command.upgrade(alembic_config, "head")

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
def client(test_engine: Engine) -> Generator[TestClient, None, None]:
    """TestClient wired to the test database via a get_db override, so any
    endpoint that touches the database — auth, and every future module —
    runs against bazra_test rather than the dev database.
    """

    def override_get_db() -> Generator[Session, None, None]:
        session = sessionmaker(bind=test_engine)()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def authenticated_client(client: TestClient, db_session: Session) -> TestClient:
    """A TestClient with a valid session cookie already set, for tests of
    modules that require auth but aren't testing auth itself (every module
    from Checkpoint 2.2 onward). Idempotently ensures the single test User
    row has a known password, regardless of what earlier tests in this
    session left behind — same pattern as auth's own test_user fixture,
    duplicated in this one small place rather than importing across
    modules' test internals.
    """
    from app.modules.auth import service as auth_service
    from app.modules.auth.models import User

    password = "test-password-for-authenticated-client"  # noqa: S105
    user = auth_service.get_the_user(db_session)
    password_hash = auth_service.hash_password(password)
    if user is None:
        user = User(password_hash=password_hash)
        db_session.add(user)
    else:
        user.password_hash = password_hash
    db_session.commit()

    response = client.post("/api/v1/auth/login", json={"password": password})
    assert response.status_code == 200
    return client
