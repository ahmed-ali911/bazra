from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.auth import service
from app.modules.auth.models import User, UserSession

TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture()
def test_user(db_session: Session) -> User:
    """Idempotently ensure the single User row exists with a known password,
    regardless of what earlier tests in this session left behind — auth
    tests must not depend on execution order.
    """
    user = service.get_the_user(db_session)
    password_hash = service.hash_password(TEST_PASSWORD)
    if user is None:
        user = User(password_hash=password_hash)
        db_session.add(user)
    else:
        user.password_hash = password_hash
    db_session.commit()
    db_session.refresh(user)
    return user


def test_login_success_sets_cookie_and_allows_me(client: TestClient, test_user: User) -> None:
    response = client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200
    assert settings.session_cookie_name in response.cookies

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json() == {"authenticated": True}


def test_login_wrong_password_returns_401_and_no_cookie(client: TestClient, test_user: User) -> None:
    response = client.post("/api/v1/auth/login", json={"password": "wrong password"})
    assert response.status_code == 401
    assert settings.session_cookie_name not in response.cookies


def test_me_without_cookie_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_logout_invalidates_session(client: TestClient, test_user: User) -> None:
    login_response = client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
    assert login_response.status_code == 200

    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 200

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 401


def test_expired_session_is_rejected(client: TestClient, db_session: Session, test_user: User) -> None:
    raw_token = service.create_session(db_session, test_user)

    session_row = (
        db_session.query(UserSession).filter_by(user_id=test_user.id).order_by(UserSession.created_at.desc()).first()
    )
    assert session_row is not None
    session_row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    client.cookies.set(settings.session_cookie_name, raw_token)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
