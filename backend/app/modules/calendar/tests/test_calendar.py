from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

# A fixed 24-hour agenda window used across most tests: [FROM, TO).
FROM = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
TO = datetime(2026, 6, 16, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _agenda(client: TestClient, from_: datetime = FROM, to: datetime = TO):
    response = client.get("/api/v1/calendar/agenda", params={"from": _iso(from_), "to": _iso(to)})
    assert response.status_code == 200, response.text
    return response.json()


def _create_event(client: TestClient, title: str, starts_at: datetime, ends_at: datetime | None = None):
    body = {"title": title, "starts_at": _iso(starts_at)}
    if ends_at is not None:
        body["ends_at"] = _iso(ends_at)
    response = client.post("/api/v1/calendar/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _create_task_due(client: TestClient, title: str, due_at: datetime):
    response = client.post("/api/v1/tasks", json={"title": title, "due_at": _iso(due_at)})
    assert response.status_code == 201, response.text
    return response.json()


# ---- CRUD, validation, auth, space isolation --------------------------------


def test_create_and_get_calendar_event(authenticated_client: TestClient) -> None:
    created = _create_event(authenticated_client, "Team sync", FROM, FROM)
    response = authenticated_client.get(f"/api/v1/calendar/events/{created['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Team sync"


def test_create_with_ends_before_starts_rejected(authenticated_client: TestClient) -> None:
    bad_ends = FROM.replace(day=14, hour=23)  # one hour before FROM
    response = authenticated_client.post(
        "/api/v1/calendar/events", json={"title": "Bad range", "starts_at": _iso(FROM), "ends_at": _iso(bad_ends)}
    )
    assert response.status_code == 422  # Pydantic model_validator rejects it at the schema boundary


def test_update_range_validated_against_merged_state(authenticated_client: TestClient) -> None:
    created = _create_event(authenticated_client, "Movable", FROM)
    # Only ends_at is sent — must be validated against the EXISTING starts_at,
    # not just the fields present in this partial payload.
    earlier_than_starts = FROM.replace(day=14)
    response = authenticated_client.patch(
        f"/api/v1/calendar/events/{created['id']}", json={"ends_at": _iso(earlier_than_starts)}
    )
    assert response.status_code == 400


def test_delete_archives_rather_than_hard_deletes(authenticated_client: TestClient, db_session: Session) -> None:
    created = _create_event(authenticated_client, "Temporary event", FROM)
    delete_response = authenticated_client.delete(f"/api/v1/calendar/events/{created['id']}")
    assert delete_response.status_code == 204

    assert authenticated_client.get(f"/api/v1/calendar/events/{created['id']}").status_code == 404

    row = db_session.execute(
        text("SELECT archived_at, title FROM calendar_events WHERE id = :id"), {"id": created["id"]}
    ).one()
    assert row.archived_at is not None
    assert row.title == "Temporary event"


def test_create_with_invalid_life_area_returns_400(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/v1/calendar/events", json={"title": "x", "starts_at": _iso(FROM), "life_area_id": 999999}
    )
    assert response.status_code == 400


def test_calendar_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/calendar/agenda", params={"from": _iso(FROM), "to": _iso(TO)}).status_code == 401
    assert client.post("/api/v1/calendar/events", json={"title": "x", "starts_at": _iso(FROM)}).status_code == 401


def test_space_isolation_across_fetch_update_and_delete(
    authenticated_client: TestClient, db_session: Session
) -> None:
    from app.core.deps import get_current_space_id
    from app.main import app
    from app.modules.auth import service as auth_service
    from app.modules.spaces.models import Space

    event = _create_event(authenticated_client, "Space A's event", FROM)

    owner = auth_service.get_the_user(db_session)
    other_space = Space(name="Other Space (calendar test)", is_default=False, user_id=owner.id)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)

    app.dependency_overrides[get_current_space_id] = lambda: other_space.id
    try:
        assert authenticated_client.get(f"/api/v1/calendar/events/{event['id']}").status_code == 404
        assert authenticated_client.patch(
            f"/api/v1/calendar/events/{event['id']}", json={"title": "Hijacked"}
        ).status_code == 404
        assert authenticated_client.delete(f"/api/v1/calendar/events/{event['id']}").status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_space_id, None)

    row = db_session.execute(
        text("SELECT title, archived_at FROM calendar_events WHERE id = :id"), {"id": event["id"]}
    ).one()
    assert row.title == "Space A's event"
    assert row.archived_at is None


# ---- Agenda: source tagging, ordering, archived exclusion --------------------


def test_agenda_includes_task_and_event_with_correct_source_tagging(authenticated_client: TestClient) -> None:
    event = _create_event(authenticated_client, "Standup", FROM.replace(hour=9))
    task = _create_task_due(authenticated_client, "File taxes", FROM.replace(hour=14))

    items = _agenda(authenticated_client)
    by_title = {item["title"]: item for item in items}

    assert by_title["Standup"]["source"] == "event"
    assert by_title["Standup"]["id"] == event["id"]
    assert by_title["File taxes"]["source"] == "task"
    assert by_title["File taxes"]["id"] == task["id"]
    assert by_title["File taxes"]["ends_at"] is None


def test_agenda_deterministic_ordering(authenticated_client: TestClient) -> None:
    _create_task_due(authenticated_client, "Third", FROM.replace(hour=15))
    _create_event(authenticated_client, "First", FROM.replace(hour=8))
    _create_task_due(authenticated_client, "Second", FROM.replace(hour=10))

    items = _agenda(authenticated_client)
    titles = [item["title"] for item in items]
    assert titles.index("First") < titles.index("Second") < titles.index("Third")


def test_agenda_excludes_archived_task_and_archived_event(authenticated_client: TestClient) -> None:
    task = _create_task_due(authenticated_client, "Will be archived", FROM.replace(hour=9))
    event = _create_event(authenticated_client, "Will be archived too", FROM.replace(hour=10))

    authenticated_client.delete(f"/api/v1/tasks/{task['id']}")
    authenticated_client.delete(f"/api/v1/calendar/events/{event['id']}")

    titles = [item["title"] for item in _agenda(authenticated_client)]
    assert "Will be archived" not in titles
    assert "Will be archived too" not in titles


def test_agenda_reflects_task_due_at_change(authenticated_client: TestClient) -> None:
    task = _create_task_due(authenticated_client, "Movable due date", FROM.replace(hour=9))
    assert "Movable due date" in [item["title"] for item in _agenda(authenticated_client)]

    # Move it a week later — out of this window entirely.
    new_due = FROM.replace(day=22, hour=9)
    authenticated_client.patch(f"/api/v1/tasks/{task['id']}", json={"due_at": _iso(new_due)})

    assert "Movable due date" not in [item["title"] for item in _agenda(authenticated_client)]
    later_window_items = _agenda(authenticated_client, from_=new_due.replace(hour=0), to=new_due.replace(hour=0).replace(day=23))
    assert "Movable due date" in [item["title"] for item in later_window_items]


# ---- Agenda range contract: half-open [from, to), boundaries, overlap -------


def test_agenda_task_due_exactly_at_from_included(authenticated_client: TestClient) -> None:
    _create_task_due(authenticated_client, "On the from boundary", FROM)
    assert "On the from boundary" in [item["title"] for item in _agenda(authenticated_client)]


def test_agenda_task_due_exactly_at_to_excluded(authenticated_client: TestClient) -> None:
    _create_task_due(authenticated_client, "On the to boundary", TO)
    assert "On the to boundary" not in [item["title"] for item in _agenda(authenticated_client)]


def test_agenda_event_starting_before_from_ending_inside_range_included(authenticated_client: TestClient) -> None:
    starts = FROM.replace(day=14, hour=23)
    ends = FROM.replace(hour=1)
    _create_event(authenticated_client, "Straddles the start", starts, ends)
    assert "Straddles the start" in [item["title"] for item in _agenda(authenticated_client)]


def test_agenda_event_starting_before_from_ending_after_to_included(authenticated_client: TestClient) -> None:
    starts = FROM.replace(day=14, hour=22)
    ends = TO.replace(hour=2)
    _create_event(authenticated_client, "Spans the whole range", starts, ends)
    assert "Spans the whole range" in [item["title"] for item in _agenda(authenticated_client)]


def test_agenda_event_entirely_inside_range_included(authenticated_client: TestClient) -> None:
    _create_event(authenticated_client, "Fully inside", FROM.replace(hour=10), FROM.replace(hour=11))
    assert "Fully inside" in [item["title"] for item in _agenda(authenticated_client)]


def test_agenda_event_entirely_outside_range_excluded(authenticated_client: TestClient) -> None:
    before = FROM.replace(day=13, hour=10)
    before_end = FROM.replace(day=13, hour=11)
    _create_event(authenticated_client, "Entirely before", before, before_end)

    after = TO.replace(day=17, hour=10)
    after_end = TO.replace(day=17, hour=11)
    _create_event(authenticated_client, "Entirely after", after, after_end)

    titles = [item["title"] for item in _agenda(authenticated_client)]
    assert "Entirely before" not in titles
    assert "Entirely after" not in titles


def test_agenda_boundary_touching_events_per_half_open_contract(authenticated_client: TestClient) -> None:
    # Ends exactly at `from` — the event's own end (inclusive) coincides
    # with the agenda's start (inclusive) -> they share that instant ->
    # included.
    ends_exactly_at_from = FROM
    starts_before = FROM.replace(day=14, hour=23)
    _create_event(authenticated_client, "Ends exactly at from", starts_before, ends_exactly_at_from)

    # Starts exactly at `to` — the agenda excludes `to` itself -> excluded.
    starts_exactly_at_to = TO
    ends_after = TO.replace(hour=1)
    _create_event(authenticated_client, "Starts exactly at to", starts_exactly_at_to, ends_after)

    titles = [item["title"] for item in _agenda(authenticated_client)]
    assert "Ends exactly at from" in titles
    assert "Starts exactly at to" not in titles


def test_agenda_point_event_inside_and_outside_range(authenticated_client: TestClient) -> None:
    _create_event(authenticated_client, "Point inside", FROM.replace(hour=12))
    _create_event(authenticated_client, "Point outside", FROM.replace(day=14, hour=12))

    titles = [item["title"] for item in _agenda(authenticated_client)]
    assert "Point inside" in titles
    assert "Point outside" not in titles


def test_agenda_rejects_to_less_equal_from(authenticated_client: TestClient) -> None:
    response = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": _iso(TO), "to": _iso(FROM)}
    )
    assert response.status_code == 400

    equal_response = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": _iso(FROM), "to": _iso(FROM)}
    )
    assert equal_response.status_code == 400
