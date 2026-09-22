from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


def test_create_and_list_task(authenticated_client: TestClient) -> None:
    response = authenticated_client.post("/api/v1/tasks", json={"title": "Write report"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Write report"
    assert body["status"] == "open"
    assert body["completed_at"] is None

    list_response = authenticated_client.get("/api/v1/tasks")
    assert list_response.status_code == 200
    titles = [task["title"] for task in list_response.json()]
    assert "Write report" in titles


def test_get_nonexistent_task_returns_404(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/v1/tasks/999999")
    assert response.status_code == 404


def test_update_status_to_done_sets_completed_at_and_reopening_clears_it(
    authenticated_client: TestClient,
) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Ship checkpoint"}).json()

    done_response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"status": "done"})
    assert done_response.status_code == 200
    assert done_response.json()["status"] == "done"
    assert done_response.json()["completed_at"] is not None

    reopened_response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"status": "open"})
    assert reopened_response.status_code == 200
    assert reopened_response.json()["status"] == "open"
    assert reopened_response.json()["completed_at"] is None


def test_delete_archives_rather_than_hard_deletes(
    authenticated_client: TestClient, db_session: Session
) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Temporary task"}).json()

    delete_response = authenticated_client.delete(f"/api/v1/tasks/{created['id']}")
    assert delete_response.status_code == 204

    # Invisible through the API — the whole point of "deleted" from the
    # user's perspective.
    assert authenticated_client.get(f"/api/v1/tasks/{created['id']}").status_code == 404
    assert created["id"] not in [t["id"] for t in authenticated_client.get("/api/v1/tasks").json()]

    # But the row itself, and its data, still exist — checked directly
    # against the real database, not assumed from the API's 404.
    row = db_session.execute(
        text("SELECT archived_at, title FROM tasks WHERE id = :id"), {"id": created["id"]}
    ).one()
    assert row.archived_at is not None
    assert row.title == "Temporary task"


def test_status_filter(authenticated_client: TestClient) -> None:
    open_task = authenticated_client.post("/api/v1/tasks", json={"title": "Still open"}).json()
    done_task = authenticated_client.post("/api/v1/tasks", json={"title": "Already done"}).json()
    authenticated_client.patch(f"/api/v1/tasks/{done_task['id']}", json={"status": "done"})

    open_only = authenticated_client.get("/api/v1/tasks?status=open").json()
    ids = [t["id"] for t in open_only]
    assert open_task["id"] in ids
    assert done_task["id"] not in ids


def test_create_with_invalid_life_area_returns_400(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Bad reference", "life_area_id": 999999}
    )
    assert response.status_code == 400


def test_create_with_valid_life_area(authenticated_client: TestClient) -> None:
    life_areas = authenticated_client.get("/api/v1/life-areas").json()
    work = next(area for area in life_areas if area["slug"] == "work")

    response = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Quarterly review", "life_area_id": work["id"]}
    )
    assert response.status_code == 201
    assert response.json()["life_area_id"] == work["id"]


def test_tasks_require_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/tasks").status_code == 401
    assert client.post("/api/v1/tasks", json={"title": "x"}).status_code == 401


def test_space_isolation_across_fetch_update_and_delete(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """A task created in the default space must be completely invisible —
    to GET, PATCH, and DELETE alike — when the request resolves to a
    different space. Covers all three write/read paths explicitly, not
    just the read path.
    """
    from app.core.deps import get_current_space_id
    from app.main import app
    from app.modules.spaces.models import Space

    task = authenticated_client.post("/api/v1/tasks", json={"title": "Space A's task"}).json()

    other_space = Space(name="Other Space", is_default=False)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)

    app.dependency_overrides[get_current_space_id] = lambda: other_space.id
    try:
        assert authenticated_client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
        assert task["id"] not in [t["id"] for t in authenticated_client.get("/api/v1/tasks").json()]

        update_response = authenticated_client.patch(
            f"/api/v1/tasks/{task['id']}", json={"title": "Hijacked"}
        )
        assert update_response.status_code == 404

        delete_response = authenticated_client.delete(f"/api/v1/tasks/{task['id']}")
        assert delete_response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_space_id, None)

    # And confirm the task in space A was genuinely untouched by the
    # attempted cross-space update/delete above.
    row = db_session.execute(
        text("SELECT title, archived_at FROM tasks WHERE id = :id"), {"id": task["id"]}
    ).one()
    assert row.title == "Space A's task"
    assert row.archived_at is None
