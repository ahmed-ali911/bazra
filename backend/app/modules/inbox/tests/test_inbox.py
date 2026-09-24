import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _create_task(client: TestClient, title: str) -> dict:
    response = client.post("/api/v1/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


def _complete(client: TestClient, task_id: int) -> dict:
    response = client.patch(f"/api/v1/tasks/{task_id}", json={"status": "done"})
    assert response.status_code == 200, response.text
    return response.json()


def _reopen(client: TestClient, task_id: int) -> dict:
    response = client.patch(f"/api/v1/tasks/{task_id}", json={"status": "open"})
    assert response.status_code == 200, response.text
    return response.json()


def _inbox_items_for(client: TestClient, title_prefix: str) -> list[dict]:
    items = client.get("/api/v1/inbox").json()
    return [item for item in items if item["title"].startswith(title_prefix)]


# ---- the generation trigger --------------------------------------------------


def test_completing_a_task_creates_exactly_one_inbox_item(authenticated_client: TestClient) -> None:
    task = _create_task(authenticated_client, "Ship checkpoint 2.4")
    _complete(authenticated_client, task["id"])

    matches = _inbox_items_for(authenticated_client, "Completed: Ship checkpoint 2.4")
    assert len(matches) == 1
    assert matches[0]["task_id"] == task["id"]
    assert matches[0]["read_at"] is None


def test_updating_an_already_done_task_creates_no_additional_inbox_item(
    authenticated_client: TestClient,
) -> None:
    """Idempotency: the trigger fires on the open->done EDGE, not on every
    write to a task that happens to already be done — e.g. renaming a
    completed task, or re-sending status="done" for an already-done task,
    must not accumulate a second InboxItem.
    """
    task = _create_task(authenticated_client, "Renamable task")
    _complete(authenticated_client, task["id"])
    assert len(_inbox_items_for(authenticated_client, "Completed: Renamable task")) == 1

    # Re-affirming status="done" on an already-done task is a no-op edge.
    authenticated_client.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"})
    # Editing an unrelated field on an already-done task is also a no-op edge.
    authenticated_client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "Renamed task"})

    all_items = authenticated_client.get("/api/v1/inbox").json()
    completed_items = [item for item in all_items if item["task_id"] == task["id"]]
    assert len(completed_items) == 1


def test_done_open_done_creates_a_second_inbox_item(authenticated_client: TestClient) -> None:
    """Explicit intended behavior for the second transition: the trigger is
    edge-based (open -> done), so crossing that edge twice — done -> open,
    then open -> done again — is treated as two distinct completion events
    and produces two InboxItems, not one. This is a deliberate consequence
    of the MVP "every open->done edge is worth surfacing" trigger, not an
    accident — see inbox/service.py's create_item docstring on this not
    being a frozen product rule.
    """
    task = _create_task(authenticated_client, "Flappy task")
    _complete(authenticated_client, task["id"])
    _reopen(authenticated_client, task["id"])
    _complete(authenticated_client, task["id"])

    matches = _inbox_items_for(authenticated_client, "Completed: Flappy task")
    assert len(matches) == 2


# ---- atomicity of the generation trigger ---------------------------------------


def test_task_update_and_inbox_item_creation_are_atomic(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If InboxItem creation fails as part of the open -> done transition,
    the ENTIRE write must roll back — the Task must never end up durably
    "done" with no corresponding InboxItem, which is what a real 500 on
    this endpoint would otherwise silently leave behind. Injects a real
    Postgres-level failure (an invalid task_id, violating inbox_items'
    own FK constraint) rather than a mocked Python exception, so this
    proves actual commit/rollback behavior in the database, not just
    that an exception happens to propagate. Checked via db_session — a
    separate session/connection from the one the failed request used —
    so this reflects what's actually durable, not leftover in-memory
    state on the failed session.
    """
    from app.modules.inbox import service as inbox_service_module

    task = _create_task(authenticated_client, "Should not complete")

    real_create_item = inbox_service_module.create_item

    def _create_item_with_invalid_task_id(db, space_id, *, task_id, title, commit=True):
        return real_create_item(db, space_id, task_id=999999999, title=title, commit=commit)

    monkeypatch.setattr(inbox_service_module, "create_item", _create_item_with_invalid_task_id)

    with pytest.raises(IntegrityError):
        authenticated_client.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"})

    monkeypatch.undo()

    task_row = db_session.execute(
        text("SELECT status, completed_at FROM tasks WHERE id = :id"), {"id": task["id"]}
    ).one()
    assert task_row.status == "open"
    assert task_row.completed_at is None

    inbox_count = db_session.execute(
        text("SELECT count(*) FROM inbox_items WHERE task_id = :id"), {"id": task["id"]}
    ).scalar_one()
    assert inbox_count == 0


# ---- the snapshot vs live-reference decision -----------------------------------


def test_inbox_item_title_is_a_snapshot_not_a_live_reference(authenticated_client: TestClient) -> None:
    task = _create_task(authenticated_client, "Original title")
    _complete(authenticated_client, task["id"])

    matches = _inbox_items_for(authenticated_client, "Completed: Original title")
    assert len(matches) == 1
    item_id = matches[0]["id"]

    # Rename the source task AFTER the InboxItem was generated.
    authenticated_client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "Renamed later"})

    item = authenticated_client.get("/api/v1/inbox").json()
    refetched = next(i for i in item if i["id"] == item_id)
    assert refetched["title"] == "Completed: Original title"


# ---- state transitions / CRUD --------------------------------------------------


def test_mark_read_and_unread(authenticated_client: TestClient) -> None:
    task = _create_task(authenticated_client, "Mark me read")
    _complete(authenticated_client, task["id"])
    item = _inbox_items_for(authenticated_client, "Completed: Mark me read")[0]

    read_response = authenticated_client.patch(f"/api/v1/inbox/{item['id']}", json={"read": True})
    assert read_response.status_code == 200
    assert read_response.json()["read_at"] is not None

    unread_response = authenticated_client.patch(f"/api/v1/inbox/{item['id']}", json={"read": False})
    assert unread_response.status_code == 200
    assert unread_response.json()["read_at"] is None


def test_unread_filter(authenticated_client: TestClient) -> None:
    task_a = _create_task(authenticated_client, "Stays unread")
    task_b = _create_task(authenticated_client, "Gets read")
    _complete(authenticated_client, task_a["id"])
    _complete(authenticated_client, task_b["id"])

    item_b = _inbox_items_for(authenticated_client, "Completed: Gets read")[0]
    authenticated_client.patch(f"/api/v1/inbox/{item_b['id']}", json={"read": True})

    unread_only = authenticated_client.get("/api/v1/inbox", params={"unread": True}).json()
    titles = [item["title"] for item in unread_only]
    assert "Completed: Stays unread" in titles
    assert "Completed: Gets read" not in titles


def test_dismiss_archives_rather_than_hard_deletes(
    authenticated_client: TestClient, db_session: Session
) -> None:
    task = _create_task(authenticated_client, "Dismiss me")
    _complete(authenticated_client, task["id"])
    item = _inbox_items_for(authenticated_client, "Completed: Dismiss me")[0]

    dismiss_response = authenticated_client.delete(f"/api/v1/inbox/{item['id']}")
    assert dismiss_response.status_code == 204

    remaining_titles = [i["title"] for i in authenticated_client.get("/api/v1/inbox").json()]
    assert "Completed: Dismiss me" not in remaining_titles

    row = db_session.execute(
        text("SELECT archived_at, title FROM inbox_items WHERE id = :id"), {"id": item["id"]}
    ).one()
    assert row.archived_at is not None
    assert row.title == "Completed: Dismiss me"


def test_mark_read_on_nonexistent_item_returns_404(authenticated_client: TestClient) -> None:
    response = authenticated_client.patch("/api/v1/inbox/999999", json={"read": True})
    assert response.status_code == 404


def test_dismiss_nonexistent_item_returns_404(authenticated_client: TestClient) -> None:
    response = authenticated_client.delete("/api/v1/inbox/999999")
    assert response.status_code == 404


def test_inbox_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/inbox").status_code == 401
    assert client.patch("/api/v1/inbox/1", json={"read": True}).status_code == 401
    assert client.delete("/api/v1/inbox/1").status_code == 401


# ---- space isolation ------------------------------------------------------------


def test_space_isolation_across_list_update_and_dismiss(
    authenticated_client: TestClient, db_session: Session
) -> None:
    from app.core.deps import get_current_space_id
    from app.main import app
    from app.modules.auth import service as auth_service
    from app.modules.spaces.models import Space

    task = _create_task(authenticated_client, "Space A's completed task")
    _complete(authenticated_client, task["id"])
    item = _inbox_items_for(authenticated_client, "Completed: Space A's completed task")[0]

    owner = auth_service.get_the_user(db_session)
    other_space = Space(name="Other Space (Inbox test)", is_default=False, user_id=owner.id)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)

    app.dependency_overrides[get_current_space_id] = lambda: other_space.id
    try:
        titles = [i["title"] for i in authenticated_client.get("/api/v1/inbox").json()]
        assert "Completed: Space A's completed task" not in titles

        update_response = authenticated_client.patch(f"/api/v1/inbox/{item['id']}", json={"read": True})
        assert update_response.status_code == 404

        dismiss_response = authenticated_client.delete(f"/api/v1/inbox/{item['id']}")
        assert dismiss_response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_space_id, None)

    row = db_session.execute(
        text("SELECT read_at, archived_at FROM inbox_items WHERE id = :id"), {"id": item["id"]}
    ).one()
    assert row.read_at is None
    assert row.archived_at is None
