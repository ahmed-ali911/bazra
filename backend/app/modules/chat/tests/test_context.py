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


def _create_event(client: TestClient, title: str, starts_at: datetime, ends_at: datetime | None = None) -> dict:
    body: dict = {"title": title, "starts_at": starts_at.isoformat()}
    if ends_at is not None:
        body["ends_at"] = ends_at.isoformat()
    response = client.post("/api/v1/calendar/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---- Checkpoint 3.18: _format_agenda_line, tested directly ----------------------
#
# Direct, DB-free unit tests of the formatting function itself, rather than
# integration tests going through gather_context's real aggregation: the
# "Coming Up" section is capped at _MAX_ITEMS_PER_SECTION and this shared test
# database is never rolled back within a single pytest session (see
# conftest.py), so a full-suite run can leave enough OTHER real events/tasks
# in the same test window that an integration-level assertion on a specific
# line's presence becomes flaky depending on run order — a risk the exact
# behavior under test (what _format_agenda_line prints for a given AgendaItem)
# doesn't actually need to take on.


def test_format_agenda_line_exposes_event_id_and_ends_at_for_calendar_events() -> None:
    """Checkpoint 3.18: propose_update_event needs a stable event_id and
    the CURRENT ends_at (for duration-preserving moves) — both already
    carried by AgendaItem, just not previously printed."""
    from app.modules.calendar.schemas import AgendaItem

    item = AgendaItem(
        source="event", id=42, title="Meeting with Hussein - ctx318a",
        starts_at=datetime(2030, 6, 16, 11, 0, tzinfo=timezone.utc),
        ends_at=datetime(2030, 6, 16, 12, 0, tzinfo=timezone.utc),
        life_area_id=None,
    )
    line = context_module._format_agenda_line(item)
    assert "(event_id=42)" in line
    assert "Meeting with Hussein - ctx318a" in line
    assert item.starts_at.isoformat() in line
    assert item.ends_at.isoformat() in line


def test_format_agenda_line_point_event_omits_ends_at_dash() -> None:
    from app.modules.calendar.schemas import AgendaItem

    item = AgendaItem(
        source="event", id=43, title="Point event - ctx318b",
        starts_at=datetime(2030, 6, 16, 9, 0, tzinfo=timezone.utc), ends_at=None, life_area_id=None,
    )
    line = context_module._format_agenda_line(item)
    assert "(event_id=43)" in line
    assert "–" not in line


def test_format_agenda_line_exposes_life_area_id_when_present() -> None:
    from app.modules.calendar.schemas import AgendaItem

    item = AgendaItem(
        source="event", id=44, title="Event with life area - ctx318h",
        starts_at=datetime(2030, 6, 16, 9, 0, tzinfo=timezone.utc), ends_at=None, life_area_id=7,
    )
    line = context_module._format_agenda_line(item)
    assert "life_area_id=7" in line


def test_format_agenda_line_task_rendering_is_unaffected_by_event_id_exposure() -> None:
    """Checkpoint 3.18 explicit requirement: Task rendering must remain
    unchanged — task-sourced agenda lines never gain event_id/ends_at."""
    from app.modules.calendar.schemas import AgendaItem

    item = AgendaItem(
        source="task", id=99, title="Coming up task - ctx318c",
        starts_at=datetime(2030, 6, 16, 9, 0, tzinfo=timezone.utc), ends_at=None, life_area_id=None,
    )
    line = context_module._format_agenda_line(item)
    assert line == f"- [task] Coming up task - ctx318c at {item.starts_at.isoformat()}"
    assert "event_id=" not in line


def test_gather_context_includes_a_real_calendar_event_end_to_end(
    authenticated_client: TestClient, db_session: Session,
) -> None:
    """One integration-level smoke test proving real DB data actually
    flows through gather_context end-to-end (unlike the pure unit tests
    above, this one IS subject to the shared test database's own
    accumulation across a full-suite run — so it only asserts presence
    when the item is expected to survive the section cap, using a
    distinctive title to search for rather than asserting exact
    section contents)."""
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    starts_at = datetime(2030, 6, 16, 11, 0, tzinfo=timezone.utc)
    event = _create_event(authenticated_client, "Meeting with Hussein - ctx318z", starts_at)

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)

    home_summary = context_module.home_service.build_home_summary(db_session, space.id, TOMORROW_START, WINDOW_END)
    matching = [i for i in home_summary.coming_up if i.id == event["id"] and i.source == "event"]
    assert len(matching) == 1
    assert matching[0].title == "Meeting with Hussein - ctx318z"


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
