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


def test_life_area_id_filter(authenticated_client: TestClient) -> None:
    life_areas = authenticated_client.get("/api/v1/life-areas").json()
    work = next(area for area in life_areas if area["slug"] == "work")

    in_area = authenticated_client.post(
        "/api/v1/tasks", json={"title": "In work area", "life_area_id": work["id"]}
    ).json()
    unassigned = authenticated_client.post("/api/v1/tasks", json={"title": "No area"}).json()

    filtered = authenticated_client.get(f"/api/v1/tasks?life_area_id={work['id']}").json()
    ids = [t["id"] for t in filtered]
    assert in_area["id"] in ids
    assert unassigned["id"] not in ids


def test_unassigned_filter(authenticated_client: TestClient) -> None:
    life_areas = authenticated_client.get("/api/v1/life-areas").json()
    work = next(area for area in life_areas if area["slug"] == "work")

    in_area = authenticated_client.post(
        "/api/v1/tasks", json={"title": "In work area for unassigned test", "life_area_id": work["id"]}
    ).json()
    unassigned = authenticated_client.post(
        "/api/v1/tasks", json={"title": "No area for unassigned test"}
    ).json()

    filtered = authenticated_client.get("/api/v1/tasks?unassigned=true").json()
    ids = [t["id"] for t in filtered]
    assert unassigned["id"] in ids
    assert in_area["id"] not in ids


def test_life_area_id_and_unassigned_together_returns_422(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/v1/tasks?life_area_id=1&unassigned=true")
    assert response.status_code == 422


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
    from app.modules.auth import service as auth_service
    from app.modules.spaces.models import Space

    task = authenticated_client.post("/api/v1/tasks", json={"title": "Space A's task"}).json()

    owner = auth_service.get_the_user(db_session)
    other_space = Space(name="Other Space", is_default=False, user_id=owner.id)
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


def test_create_task_with_commit_false_defers_to_the_callers_own_commit(db_session: Session) -> None:
    """The Checkpoint 3.3 escape hatch, mirroring inbox_service.create_item's
    own commit=False parameter — the row must not be durably visible
    until the CALLER commits, but task.id must already be populated
    (via flush) so the caller can reference it (e.g. as a FK) before
    that commit happens.
    """
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)

    task = tasks_service.create_task(
        db_session, space.id, TaskCreate(title="Uncommitted task"), commit=False
    )
    assert task.id is not None  # flushed, not just added

    # A FRESH session must not see it yet — nothing was committed.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    fresh_session = sessionmaker(bind=db_session.get_bind())()
    try:
        row = fresh_session.execute(
            text("SELECT count(*) FROM tasks WHERE id = :id"), {"id": task.id}
        ).scalar_one()
        assert row == 0
    finally:
        fresh_session.close()

    db_session.commit()  # the caller's own covering commit

    fresh_session = sessionmaker(bind=db_session.get_bind())()
    try:
        row = fresh_session.execute(
            text("SELECT count(*) FROM tasks WHERE id = :id"), {"id": task.id}
        ).scalar_one()
        assert row == 1
    finally:
        fresh_session.close()


# ---- Checkpoint 4.1: Task priority --------------------------------------------


def test_create_without_priority_defaults_to_normal(authenticated_client: TestClient) -> None:
    response = authenticated_client.post("/api/v1/tasks", json={"title": "No priority given"})
    assert response.status_code == 201
    assert response.json()["priority"] == "normal"


def test_create_with_low_priority(authenticated_client: TestClient) -> None:
    response = authenticated_client.post("/api/v1/tasks", json={"title": "Low one", "priority": "low"})
    assert response.status_code == 201
    assert response.json()["priority"] == "low"


def test_create_with_high_priority(authenticated_client: TestClient) -> None:
    response = authenticated_client.post("/api/v1/tasks", json={"title": "High one", "priority": "high"})
    assert response.status_code == 201
    assert response.json()["priority"] == "high"


def test_create_with_invalid_priority_rejected(authenticated_client: TestClient) -> None:
    response = authenticated_client.post("/api/v1/tasks", json={"title": "Bad one", "priority": "urgent"})
    assert response.status_code == 422


def test_update_priority_normal_to_high(authenticated_client: TestClient) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Promote me"}).json()
    assert created["priority"] == "normal"

    response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"priority": "high"})
    assert response.status_code == 200
    assert response.json()["priority"] == "high"


def test_update_priority_high_to_low(authenticated_client: TestClient) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Demote me", "priority": "high"}).json()

    response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"priority": "low"})
    assert response.status_code == 200
    assert response.json()["priority"] == "low"


def test_update_priority_high_to_normal_explicit(authenticated_client: TestClient) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Back to normal", "priority": "high"}).json()

    response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"priority": "normal"})
    assert response.status_code == 200
    assert response.json()["priority"] == "normal"


def test_update_omitting_priority_leaves_it_unchanged(authenticated_client: TestClient) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Untouched priority", "priority": "high"}).json()

    response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"title": "Renamed only"})
    assert response.status_code == 200
    assert response.json()["priority"] == "high"
    assert response.json()["title"] == "Renamed only"


def test_update_with_invalid_priority_rejected(authenticated_client: TestClient) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Reject me"}).json()
    response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"priority": "urgent"})
    assert response.status_code == 422


def test_pre_migration_row_reads_as_normal(db_session: Session) -> None:
    """Confirms the migration's own server_default, not merely the
    Pydantic schema default — a row inserted with no priority column
    value at all (simulating a pre-4.1 row) must read back as 'normal'
    via the real Postgres column default, independent of any Python-side
    default."""
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)

    db_session.execute(
        text("INSERT INTO tasks (space_id, title, status) VALUES (:space_id, :title, 'open')"),
        {"space_id": space.id, "title": "Inserted without priority - 4.1"},
    )
    db_session.commit()

    row = db_session.execute(
        text("SELECT priority FROM tasks WHERE title = 'Inserted without priority - 4.1'")
    ).one()
    assert row.priority == "normal"


def test_task_response_exposes_priority(authenticated_client: TestClient) -> None:
    created = authenticated_client.post("/api/v1/tasks", json={"title": "Exposed", "priority": "low"}).json()
    assert "priority" in created

    fetched = authenticated_client.get(f"/api/v1/tasks/{created['id']}").json()
    assert fetched["priority"] == "low"


# ---- Checkpoint 4.4c-3: ACTED_ON wiring ----


def _space_and_user(db_session: Session):
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)
    return space.id, user.id


def _overdue_exposure_for_task(db_session: Session, space_id: int, user_id: int, task_id: int, due_at) -> int:
    from app.modules.attention import history as attention_history
    from app.modules.attention import scoring as attention_scoring
    from app.modules.attention.schemas import Signal

    signal = Signal(
        signal_type="TASK_OVERDUE", source_type="task", source_id=task_id, title="t",
        relevant_timestamp=due_at, priority="normal", measurement_seconds=3600,
        snapshot={"due_at": "x", "status": "open"},
    )
    candidate = attention_scoring.score_signal(signal)
    from datetime import datetime, timezone

    exposure = attention_history.record_exposure(
        db_session, space_id, user_id, candidate, "app_opened", datetime.now(timezone.utc), "Africa/Cairo"
    )
    return exposure.id


def test_status_open_to_done_qualifies_for_attribution(authenticated_client: TestClient, db_session: Session) -> None:
    from datetime import datetime, timedelta, timezone

    space_id, user_id = _space_and_user(db_session)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Done qualifies", "due_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"status": "done"})

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure_id}
    ).scalar_one()
    assert row is not None


def test_due_at_actual_change_qualifies_for_attribution(authenticated_client: TestClient, db_session: Session) -> None:
    from datetime import datetime, timedelta, timezone

    space_id, user_id = _space_and_user(db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Due change qualifies", "due_at": past_due.isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    future_due = datetime.now(timezone.utc) + timedelta(days=1)
    authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"due_at": future_due.isoformat()})

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure_id}
    ).scalar_one()
    assert row is not None


def test_same_due_at_value_does_not_invoke_attribution(authenticated_client: TestClient, db_session: Session) -> None:
    from datetime import datetime, timedelta, timezone

    space_id, user_id = _space_and_user(db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Same due_at no-op", "due_at": past_due.isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    # Re-send the EXACT same due_at value — a semantic no-op.
    authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"due_at": created["due_at"]})

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure_id}
    ).scalar_one()
    assert row is None


def test_title_only_change_does_not_invoke_attribution(authenticated_client: TestClient, db_session: Session) -> None:
    from datetime import datetime, timedelta, timezone

    space_id, user_id = _space_and_user(db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Title-only no-op", "due_at": past_due.isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"title": "Renamed, nothing else"})

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure_id}
    ).scalar_one()
    assert row is None


def test_life_area_only_change_does_not_invoke_attribution(authenticated_client: TestClient, db_session: Session) -> None:
    from datetime import datetime, timedelta, timezone

    space_id, user_id = _space_and_user(db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Life-area-only no-op", "due_at": past_due.isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    area = authenticated_client.post("/api/v1/life-areas", json={"name": "Acted-on wiring test area"}).json()
    authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"life_area_id": area["id"]})

    row = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure_id}
    ).scalar_one()
    assert row is None


def test_task_archive_qualifies_for_attribution(authenticated_client: TestClient, db_session: Session) -> None:
    from datetime import datetime, timedelta, timezone

    space_id, user_id = _space_and_user(db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Archive qualifies", "due_at": past_due.isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    authenticated_client.delete(f"/api/v1/tasks/{created['id']}")

    row = db_session.execute(
        text("SELECT acted_on_at, archived_at FROM attention_exposures ae "
             "JOIN tasks t ON t.id = ae.task_id WHERE ae.id = :id"),
        {"id": exposure_id},
    ).mappings().one()
    assert row["acted_on_at"] is not None


def test_task_archive_acted_on_uses_same_now_as_archived_at(authenticated_client: TestClient, db_session: Session) -> None:
    from datetime import datetime, timedelta, timezone

    space_id, user_id = _space_and_user(db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Same now proof", "due_at": past_due.isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    authenticated_client.delete(f"/api/v1/tasks/{created['id']}")

    row = db_session.execute(
        text(
            "SELECT ae.acted_on_at, t.archived_at FROM attention_exposures ae "
            "JOIN tasks t ON t.id = ae.task_id WHERE ae.id = :id"
        ),
        {"id": exposure_id},
    ).mappings().one()
    assert row["acted_on_at"] == row["archived_at"]


def test_forced_attribution_exception_during_task_update_still_commits_mutation(
    authenticated_client: TestClient, db_session: Session, monkeypatch
) -> None:
    from datetime import datetime, timedelta, timezone

    from app.modules.attention import resolution as attention_resolution

    space_id, user_id = _space_and_user(db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    created = authenticated_client.post(
        "/api/v1/tasks", json={"title": "Forced failure still commits", "due_at": past_due.isoformat()}
    ).json()
    exposure_id = _overdue_exposure_for_task(db_session, space_id, user_id, created["id"], created["due_at"])

    monkeypatch.setattr(
        attention_resolution, "evaluate_acted_on", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    response = authenticated_client.patch(f"/api/v1/tasks/{created['id']}", json={"status": "done"})
    monkeypatch.undo()

    assert response.status_code == 200
    assert response.json()["status"] == "done"

    acted_on = db_session.execute(
        text("SELECT acted_on_at FROM attention_exposures WHERE id = :id"), {"id": exposure_id}
    ).scalar_one()
    assert acted_on is None
