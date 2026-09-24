from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.chat import context as context_module

TOMORROW_START = datetime(2030, 6, 15, 0, 0, tzinfo=timezone.utc)
WINDOW_END = TOMORROW_START + timedelta(days=7)


def _create_task(client: TestClient, title: str, due_at: datetime | None = None) -> dict:
    body: dict = {"title": title}
    if due_at is not None:
        body["due_at"] = due_at.isoformat()
    response = client.post("/api/v1/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_gather_context_includes_real_seeded_data(authenticated_client: TestClient, db_session: Session) -> None:
    _create_task(authenticated_client, "Buy anniversary gift", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))

    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)

    context = context_module.gather_context(db_session, space.id, TOMORROW_START, WINDOW_END)
    assert "Buy anniversary gift" in context
    assert "Focus Today" in context


def test_context_truncates_anytime_tasks_over_cap_with_explicit_note(
    authenticated_client: TestClient, db_session: Session
) -> None:
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    for i in range(20):
        _create_task(authenticated_client, f"Someday task {i}")

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)

    context = context_module.gather_context(db_session, space.id, TOMORROW_START, WINDOW_END)
    assert "more not shown" in context
    shown_count = context.count("Someday task")
    assert shown_count == context_module._MAX_ITEMS_PER_SECTION


def test_context_assembly_excludes_other_spaces_data(authenticated_client: TestClient, db_session: Session) -> None:
    from app.modules.auth import service as auth_service
    from app.modules.auth.models import User
    from app.modules.spaces import service as spaces_service
    from app.modules.spaces.models import Space

    _create_task(authenticated_client, "Space A only task")

    other_user = User(password_hash=auth_service.hash_password("other"))
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    other_space = Space(name="Other space", is_default=True, user_id=other_user.id)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)

    context = context_module.gather_context(db_session, other_space.id, TOMORROW_START, WINDOW_END)
    assert "Space A only task" not in context
