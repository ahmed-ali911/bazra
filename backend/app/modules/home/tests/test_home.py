from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

# Fixed local-day boundaries used for every test that doesn't need to be
# relative to the real clock — TOMORROW_START is what the client would
# compute as "start of tomorrow", WINDOW_END is TOMORROW_START + 7 days
# (the Coming Up window's upper bound). Chosen safely in the future so it
# never collides with whatever "now" actually is when this suite runs —
# Coming Up's event half compares against the server's real now(), so a
# fixed date that isn't safely ahead of the real clock would silently
# exclude every event these tests create (caught during implementation,
# not guessed at).
TOMORROW_START = datetime(2030, 6, 15, 0, 0, tzinfo=timezone.utc)
WINDOW_END = TOMORROW_START + timedelta(days=7)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _summary(client: TestClient, tomorrow_start: datetime = TOMORROW_START, window_end: datetime = WINDOW_END):
    response = client.get(
        "/api/v1/home/summary",
        params={"tomorrow_start": _iso(tomorrow_start), "window_end": _iso(window_end)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_task(client: TestClient, title: str, due_at: datetime | None = None) -> dict:
    body = {"title": title}
    if due_at is not None:
        body["due_at"] = _iso(due_at)
    response = client.post("/api/v1/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _create_event(client: TestClient, title: str, starts_at: datetime) -> dict:
    response = client.post("/api/v1/calendar/events", json={"title": title, "starts_at": _iso(starts_at)})
    assert response.status_code == 201, response.text
    return response.json()


def _complete(client: TestClient, task_id: int) -> None:
    response = client.patch(f"/api/v1/tasks/{task_id}", json={"status": "done"})
    assert response.status_code == 200, response.text


def _titles(items: list[dict]) -> set[str]:
    return {item["title"] for item in items}


# ---- Focus Today ----------------------------------------------------------


def test_focus_today_includes_far_overdue_and_due_later_today(authenticated_client: TestClient) -> None:
    _create_task(authenticated_client, "Way overdue", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    _create_task(authenticated_client, "Due just before tomorrow", due_at=TOMORROW_START - timedelta(minutes=1))

    summary = _summary(authenticated_client)
    titles = _titles(summary["focus_today"])
    assert "Way overdue" in titles
    assert "Due just before tomorrow" in titles


def test_focus_today_excludes_task_due_exactly_at_tomorrow_start(authenticated_client: TestClient) -> None:
    _create_task(authenticated_client, "Due exactly at boundary", due_at=TOMORROW_START)

    summary = _summary(authenticated_client)
    assert "Due exactly at boundary" not in _titles(summary["focus_today"])
    assert "Due exactly at boundary" in _titles(summary["coming_up"])


def test_focus_today_excludes_tasks_without_due_date(authenticated_client: TestClient) -> None:
    _create_task(authenticated_client, "No due date at all")

    summary = _summary(authenticated_client)
    assert "No due date at all" not in _titles(summary["focus_today"])


def test_focus_today_excludes_done_and_archived_tasks(authenticated_client: TestClient) -> None:
    done_task = _create_task(authenticated_client, "Overdue but done", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    _complete(authenticated_client, done_task["id"])

    archived_task = _create_task(
        authenticated_client, "Overdue but archived", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    authenticated_client.delete(f"/api/v1/tasks/{archived_task['id']}")

    summary = _summary(authenticated_client)
    titles = _titles(summary["focus_today"])
    assert "Overdue but done" not in titles
    assert "Overdue but archived" not in titles


# ---- Anytime ----------------------------------------------------------------


def test_anytime_includes_only_open_tasks_without_due_date(authenticated_client: TestClient) -> None:
    _create_task(authenticated_client, "No due date, open")
    _create_task(authenticated_client, "Has a due date", due_at=TOMORROW_START + timedelta(days=2))

    done_no_due = _create_task(authenticated_client, "No due date, done")
    _complete(authenticated_client, done_no_due["id"])

    summary = _summary(authenticated_client)
    titles = _titles(summary["anytime"])
    assert "No due date, open" in titles
    assert "Has a due date" not in titles
    assert "No due date, done" not in titles


# ---- Coming Up: task half ----------------------------------------------------


def test_coming_up_task_boundaries(authenticated_client: TestClient) -> None:
    _create_task(authenticated_client, "Due at window_end (excluded)", due_at=WINDOW_END)
    _create_task(authenticated_client, "Due one day before window_end", due_at=WINDOW_END - timedelta(days=1))

    summary = _summary(authenticated_client)
    titles = _titles(summary["coming_up"])
    assert "Due at window_end (excluded)" not in titles
    assert "Due one day before window_end" in titles


def test_coming_up_excludes_done_tasks(authenticated_client: TestClient) -> None:
    """Calendar's own agenda (list_tasks_due_between) deliberately does
    NOT exclude done tasks — a completed task that was due today still
    belongs on a calendar view. Home's Coming Up is a different question
    ("what's still ahead"), so a done task must NOT appear here even
    though it would appear in Calendar's agenda for the same range.
    """
    task = _create_task(authenticated_client, "Done but in range", due_at=TOMORROW_START + timedelta(days=2))
    _complete(authenticated_client, task["id"])

    summary = _summary(authenticated_client)
    assert "Done but in range" not in _titles(summary["coming_up"])


# ---- Coming Up: event half (relative to the real clock) ----------------------


def test_coming_up_events_relative_to_now(authenticated_client: TestClient) -> None:
    now = datetime.now(timezone.utc)
    _create_event(authenticated_client, "Started a while ago", now - timedelta(minutes=30))
    _create_event(authenticated_client, "Starting soon", now + timedelta(minutes=30))

    # A window bracketing the REAL clock, not the fixed far-future
    # TOMORROW_START/WINDOW_END used elsewhere in this file — those exist
    # to test task boundaries deterministically and are irrelevant to
    # these two events, which are created relative to actual now().
    summary = _summary(
        authenticated_client, tomorrow_start=now - timedelta(days=365), window_end=now + timedelta(hours=2)
    )
    titles = _titles(summary["coming_up"])
    assert "Started a while ago" not in titles
    assert "Starting soon" in titles


def test_coming_up_event_boundary_at_window_end_excluded(authenticated_client: TestClient) -> None:
    _create_event(authenticated_client, "Starts exactly at window_end", WINDOW_END)
    _create_event(authenticated_client, "Starts one day before window_end", WINDOW_END - timedelta(days=1))

    summary = _summary(authenticated_client)
    titles = _titles(summary["coming_up"])
    assert "Starts exactly at window_end" not in titles
    assert "Starts one day before window_end" in titles


def test_coming_up_excludes_archived_events(authenticated_client: TestClient) -> None:
    event = _create_event(authenticated_client, "Archived event in range", TOMORROW_START + timedelta(days=1))
    authenticated_client.delete(f"/api/v1/calendar/events/{event['id']}")

    summary = _summary(authenticated_client)
    assert "Archived event in range" not in _titles(summary["coming_up"])


def test_coming_up_merges_and_sorts_tasks_and_events(authenticated_client: TestClient) -> None:
    _create_task(authenticated_client, "Later task", due_at=TOMORROW_START + timedelta(days=3))
    _create_event(authenticated_client, "Earlier event", TOMORROW_START + timedelta(days=1))

    summary = _summary(authenticated_client)
    coming_up = summary["coming_up"]
    relevant = [item for item in coming_up if item["title"] in {"Later task", "Earlier event"}]
    assert len(relevant) == 2
    assert relevant[0]["title"] == "Earlier event"
    assert relevant[1]["title"] == "Later task"
    assert relevant[0]["source"] == "event"
    assert relevant[1]["source"] == "task"


# ---- Needs Attention ----------------------------------------------------------


def test_needs_attention_shows_only_unread_inbox_items(authenticated_client: TestClient) -> None:
    unread_task = _create_task(authenticated_client, "Stays unread")
    read_task = _create_task(authenticated_client, "Gets read")
    _complete(authenticated_client, unread_task["id"])
    _complete(authenticated_client, read_task["id"])

    inbox_items = authenticated_client.get("/api/v1/inbox").json()
    read_item = next(i for i in inbox_items if i["title"] == "Completed: Gets read")
    authenticated_client.patch(f"/api/v1/inbox/{read_item['id']}", json={"read": True})

    summary = _summary(authenticated_client)
    titles = _titles(summary["needs_attention"])
    assert "Completed: Stays unread" in titles
    assert "Completed: Gets read" not in titles


# ---- validation, auth, space isolation -----------------------------------------


def test_window_end_before_or_equal_to_tomorrow_start_rejected(authenticated_client: TestClient) -> None:
    reversed_response = authenticated_client.get(
        "/api/v1/home/summary",
        params={"tomorrow_start": _iso(WINDOW_END), "window_end": _iso(TOMORROW_START)},
    )
    assert reversed_response.status_code == 400

    equal_response = authenticated_client.get(
        "/api/v1/home/summary",
        params={"tomorrow_start": _iso(TOMORROW_START), "window_end": _iso(TOMORROW_START)},
    )
    assert equal_response.status_code == 400


def test_home_requires_authentication(client: TestClient) -> None:
    response = client.get(
        "/api/v1/home/summary",
        params={"tomorrow_start": _iso(TOMORROW_START), "window_end": _iso(WINDOW_END)},
    )
    assert response.status_code == 401


def test_home_space_isolation(authenticated_client: TestClient, db_session) -> None:
    from app.core.deps import get_current_space_id
    from app.main import app
    from app.modules.auth import service as auth_service
    from app.modules.spaces.models import Space

    _create_task(authenticated_client, "Space A overdue task", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))

    owner = auth_service.get_the_user(db_session)
    other_space = Space(name="Other Space (Home test)", is_default=False, user_id=owner.id)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)

    app.dependency_overrides[get_current_space_id] = lambda: other_space.id
    try:
        summary = _summary(authenticated_client)
        assert summary["focus_today"] == []
        assert summary["coming_up"] == []
        assert summary["needs_attention"] == []
        assert summary["anytime"] == []
    finally:
        app.dependency_overrides.pop(get_current_space_id, None)
