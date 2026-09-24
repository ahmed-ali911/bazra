from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.spaces import service
from app.modules.spaces.models import Space


def test_get_default_space_for_user_resolves_the_migration_seeded_row(db_session: Session) -> None:
    user = auth_service.get_the_user(db_session)
    space = service.get_default_space_for_user(db_session, user.id)
    assert space.is_default is True
    assert space.name
    assert space.user_id == user.id


def test_get_current_space_id_resolves_to_the_authenticated_users_own_space(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """The existing single-user case, now going through the real
    ownership check rather than a global "the one space" lookup."""
    user = auth_service.get_the_user(db_session)
    expected_space = service.get_default_space_for_user(db_session, user.id)

    task = authenticated_client.post("/api/v1/tasks", json={"title": "Owned by the real default space"}).json()

    row = db_session.execute(text("SELECT space_id FROM tasks WHERE id = :id"), {"id": task["id"]}).one()
    assert row.space_id == expected_space.id


def test_cross_user_space_ownership_is_enforced(db_session: Session) -> None:
    """Service-level proof: two distinct real users, each with their own
    real default space, must each resolve to their OWN space and never
    the other's — proving the mechanism holds even though production
    only ever has one user today, the same testing philosophy already
    used for every existing space-isolation test (which creates a
    second Space to prove isolation despite production only using one).
    """
    first_user = auth_service.get_the_user(db_session)

    second_user = User(password_hash=auth_service.hash_password("second-user-password"))
    db_session.add(second_user)
    db_session.commit()
    db_session.refresh(second_user)

    second_space = Space(name="Second user's space", is_default=True, user_id=second_user.id)
    db_session.add(second_space)
    db_session.commit()
    db_session.refresh(second_space)

    first_resolved = service.get_default_space_for_user(db_session, first_user.id)
    second_resolved = service.get_default_space_for_user(db_session, second_user.id)

    assert first_resolved.id != second_resolved.id
    assert first_resolved.user_id == first_user.id
    assert second_resolved.user_id == second_user.id


def test_cross_user_isolation_through_real_authenticated_request(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """Goes through the REAL request path — a genuinely minted second
    session cookie, real get_current_user, real get_current_space_id —
    not a direct call to a service function in isolation. auth_service's
    get_session_user/create_session are already generic (they resolve
    whichever user a session token belongs to; only login-by-password
    assumes a single row), so a real second user's session is mintable
    and usable through the real dependency chain today.
    """
    first_user_task = authenticated_client.post(
        "/api/v1/tasks", json={"title": "First user's private task"}
    ).json()
    assert first_user_task["title"] == "First user's private task"

    second_user = User(password_hash=auth_service.hash_password("second-user-password"))
    db_session.add(second_user)
    db_session.commit()
    db_session.refresh(second_user)

    second_space = Space(name="Second user's space", is_default=True, user_id=second_user.id)
    db_session.add(second_space)
    db_session.commit()

    raw_token = auth_service.create_session(db_session, second_user)
    authenticated_client.cookies.set(settings.session_cookie_name, raw_token)

    response = authenticated_client.get("/api/v1/tasks")
    assert response.status_code == 200
    titles = [t["title"] for t in response.json()]
    assert "First user's private task" not in titles
