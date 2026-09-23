from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _create_life_area(client: TestClient, name: str) -> dict:
    response = client.post("/api/v1/life-areas", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _create_task(client: TestClient, title: str, life_area_id: int | None = None, due_at: datetime | None = None) -> dict:
    body: dict = {"title": title}
    if life_area_id is not None:
        body["life_area_id"] = life_area_id
    if due_at is not None:
        body["due_at"] = due_at.isoformat()
    response = client.post("/api/v1/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _create_event(client: TestClient, title: str, starts_at: datetime, life_area_id: int | None = None) -> dict:
    body: dict = {"title": title, "starts_at": starts_at.isoformat()}
    if life_area_id is not None:
        body["life_area_id"] = life_area_id
    response = client.post("/api/v1/calendar/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---- list (existing) -----------------------------------------------------------


def test_list_life_areas_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/life-areas").status_code == 401


def test_list_life_areas_returns_the_migration_seeded_rows(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/v1/life-areas")
    assert response.status_code == 200
    slugs = {area["slug"] for area in response.json()}
    assert slugs == {"work", "personal", "learning", "career", "projects", "ideas", "teaching"}


# ---- create / rename ------------------------------------------------------------


def test_create_life_area_derives_slug(authenticated_client: TestClient) -> None:
    area = _create_life_area(authenticated_client, "Side Projects!")
    assert area["name"] == "Side Projects!"
    assert area["slug"] == "side-projects"


def test_create_life_area_duplicate_name_returns_409(authenticated_client: TestClient) -> None:
    _create_life_area(authenticated_client, "Duplicate Me")
    response = authenticated_client.post("/api/v1/life-areas", json={"name": "Duplicate Me"})
    assert response.status_code == 409


def test_rename_life_area_regenerates_slug(authenticated_client: TestClient) -> None:
    area = _create_life_area(authenticated_client, "Old Name")
    response = authenticated_client.patch(f"/api/v1/life-areas/{area['id']}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    assert response.json()["slug"] == "new-name"


def test_rename_life_area_duplicate_name_returns_409(authenticated_client: TestClient) -> None:
    _create_life_area(authenticated_client, "Existing Area")
    area = _create_life_area(authenticated_client, "Renamable Area")
    response = authenticated_client.patch(f"/api/v1/life-areas/{area['id']}", json={"name": "Existing Area"})
    assert response.status_code == 409


def test_rename_nonexistent_life_area_returns_404(authenticated_client: TestClient) -> None:
    response = authenticated_client.patch("/api/v1/life-areas/999999", json={"name": "Doesn't matter"})
    assert response.status_code == 404


def test_create_and_rename_require_authentication(client: TestClient) -> None:
    assert client.post("/api/v1/life-areas", json={"name": "x"}).status_code == 401
    assert client.patch("/api/v1/life-areas/1", json={"name": "x"}).status_code == 401


# ---- deletion: the empty case ----------------------------------------------------


def test_delete_empty_life_area_succeeds(authenticated_client: TestClient, db_session: Session) -> None:
    area = _create_life_area(authenticated_client, "Deletable Area")
    response = authenticated_client.delete(f"/api/v1/life-areas/{area['id']}")
    assert response.status_code == 204

    row = db_session.execute(
        text("SELECT id FROM life_areas WHERE id = :id"), {"id": area["id"]}
    ).one_or_none()
    assert row is None


def test_delete_nonexistent_life_area_returns_404(authenticated_client: TestClient) -> None:
    assert authenticated_client.delete("/api/v1/life-areas/999999").status_code == 404


def test_delete_requires_authentication(client: TestClient) -> None:
    assert client.delete("/api/v1/life-areas/1").status_code == 401


# ---- deletion: the blocked case — TWO SEPARATE tests, deliberately -----------------
#
# Test 1 proves the APPLICATION'S OWN GUARD works: the service layer counts
# linked rows and raises before ever attempting the database DELETE, so no
# FK violation occurs on this path at all — if the app is behaving
# correctly, testing for an IntegrityError here would be self-contradictory.
# Test 2 (further below) proves the opposite path: it bypasses the service
# layer entirely and shows the raw database constraint still exists,
# independently, as a second line of defense. Neither test can fail because
# of the other's bug.


def test_delete_blocked_by_linked_items_returns_structured_400_and_leaves_row_untouched(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """The API-guard test. Links an OPEN task, a DONE task, an ARCHIVED
    task, and a calendar event to the same area — proving the count
    includes every status/archived state, not just open ones (that's
    the whole point of count_tasks_by_life_area being unfiltered: it
    must match what the FK constraint would actually block on).
    """
    area = _create_life_area(authenticated_client, "Blocked Area")

    open_task = _create_task(authenticated_client, "Open task", life_area_id=area["id"])
    done_task = _create_task(authenticated_client, "Done task", life_area_id=area["id"])
    authenticated_client.patch(f"/api/v1/tasks/{done_task['id']}", json={"status": "done"})
    archived_task = _create_task(authenticated_client, "Archived task", life_area_id=area["id"])
    authenticated_client.delete(f"/api/v1/tasks/{archived_task['id']}")
    _create_event(authenticated_client, "Linked event", datetime.now(timezone.utc) + timedelta(days=1), life_area_id=area["id"])

    response = authenticated_client.delete(f"/api/v1/life-areas/{area['id']}")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "life_area_has_linked_items"
    assert detail["task_count"] == 3  # open + done + archived, all counted
    assert detail["calendar_event_count"] == 1
    assert detail["total_count"] == 4

    # The service layer never reached the database DELETE — confirm the
    # row is still there, via a session the failed request never touched.
    row = db_session.execute(
        text("SELECT id FROM life_areas WHERE id = :id"), {"id": area["id"]}
    ).one_or_none()
    assert row is not None
    assert open_task["life_area_id"] == area["id"]


def test_raw_db_delete_of_linked_life_area_raises_integrity_error(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """The DB-constraint test. Bypasses the service layer and the API
    entirely — issues a raw DELETE directly against the database for a
    life area with a linked task, proving Postgres's own FK constraint
    independently blocks it. This does not exercise
    life_areas_service.delete_life_area at all.
    """
    area = _create_life_area(authenticated_client, "Constraint Test Area")
    _create_task(authenticated_client, "Blocks raw delete", life_area_id=area["id"])

    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM life_areas WHERE id = :id"), {"id": area["id"]})
        db_session.commit()
    db_session.rollback()


# ---- My World summary ------------------------------------------------------------


def test_my_world_summary_counts_open_tasks_and_most_urgent_due_at(authenticated_client: TestClient) -> None:
    area = _create_life_area(authenticated_client, "Summary Area")

    _create_task(
        authenticated_client, "Overdue in area", life_area_id=area["id"], due_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    _create_task(
        authenticated_client,
        "Later due date in area",
        life_area_id=area["id"],
        due_at=datetime(2031, 1, 1, tzinfo=timezone.utc),
    )

    summary = authenticated_client.get("/api/v1/life-areas/summary").json()
    entry = next(a for a in summary["life_areas"] if a["id"] == area["id"])
    assert entry["open_task_count"] == 2
    # "Most urgent" includes the overdue task in the MIN, not just future ones.
    assert entry["most_urgent_due_at"].startswith("2020-01-01")


def test_my_world_summary_excludes_done_and_archived_from_open_count_but_deletion_count_still_includes_them(
    authenticated_client: TestClient,
) -> None:
    """The direct contrast the two counting functions are built for:
    summarize_open_tasks_by_life_area (displayed) vs.
    count_tasks_by_life_area (deletion-blocking) must disagree here, on
    purpose — proving they really are different functions serving
    different questions, not the same query reused twice.
    """
    area = _create_life_area(authenticated_client, "Contrast Area")
    done_task = _create_task(authenticated_client, "Will be done", life_area_id=area["id"])
    authenticated_client.patch(f"/api/v1/tasks/{done_task['id']}", json={"status": "done"})

    summary = authenticated_client.get("/api/v1/life-areas/summary").json()
    entry = next(a for a in summary["life_areas"] if a["id"] == area["id"])
    assert entry["open_task_count"] == 0  # the done task is NOT displayed as "open"

    # But deleting the area is still blocked by that same done task.
    delete_response = authenticated_client.delete(f"/api/v1/life-areas/{area['id']}")
    assert delete_response.status_code == 400
    assert delete_response.json()["detail"]["task_count"] == 1


def test_my_world_summary_includes_unassigned_bucket(authenticated_client: TestClient) -> None:
    _create_task(authenticated_client, "No life area at all")

    summary = authenticated_client.get("/api/v1/life-areas/summary").json()
    assert "unassigned" in summary
    assert summary["unassigned"]["open_task_count"] >= 1
    assert "id" not in summary["unassigned"]
    assert "slug" not in summary["unassigned"]


def test_my_world_summary_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/life-areas/summary").status_code == 401
