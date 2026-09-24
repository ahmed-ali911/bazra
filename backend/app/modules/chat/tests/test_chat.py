from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.chat import service as chat_service
from app.modules.chat.models import ChatMessage
from app.modules.orchestrator import service as orchestrator_service

TOMORROW_START = datetime(2030, 6, 15, 0, 0, tzinfo=timezone.utc)
WINDOW_END = TOMORROW_START + timedelta(days=7)


@pytest.fixture(autouse=True)
def _redirect_model_router_trace_session(test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chat calls model_router_service.complete(), whose AiTrace writes
    go through their own independent session (deliberately not the
    request's own — see model_router/service.py) — that session
    defaults to the real dev database, same reasoning as model_router's
    own test file's identical fixture. Without this, a real trace write
    triggered from a chat test would silently land in the wrong
    database.
    """
    from app.modules.model_router import service as model_router_service

    monkeypatch.setattr(model_router_service, "_trace_session_factory", sessionmaker(bind=test_engine))


def _send(client: TestClient, content: str) -> dict:
    response = client.post(
        "/api/v1/chat/messages",
        json={"content": content, "tomorrow_start": TOMORROW_START.isoformat(), "window_end": WINDOW_END.isoformat()},
    )
    return response


def _message_count(db_session: Session) -> int:
    return db_session.execute(select(func.count()).select_from(ChatMessage)).scalar_one()


# ---- persistence and model-failure behavior ------------------------------------


def test_chat_persists_user_message_immediately_even_on_model_failure(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply", lambda **kwargs: (_ for _ in ()).throw(
            orchestrator_service.OrchestratorError("provider_error")
        )
    )

    response = _send(authenticated_client, "what's on my calendar this month, in detail please")
    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "model_call_failed"
    user_message_id = response.json()["detail"]["user_message_id"]

    row = db_session.execute(
        text("SELECT role, content FROM chat_messages WHERE id = :id"), {"id": user_message_id}
    ).one()
    assert row.role == "user"

    assistant_rows = db_session.execute(
        text("SELECT count(*) FROM chat_messages WHERE role = 'assistant'")
    ).scalar_one()
    assert assistant_rows == 0


def test_chat_answers_grounded_in_real_seeded_data(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mocks the model call itself (capturing its arguments) rather than
    depending on a real, non-deterministic model response — proves
    context assembly gathered real data without needing a live call.
    """
    authenticated_client.post(
        "/api/v1/tasks",
        json={"title": "File Q3 taxes", "due_at": "2020-01-01T00:00:00Z"},
    )

    captured = {}

    def _fake_generate_reply(*, history, context, user_message):
        captured["context"] = context
        captured["user_message"] = user_message
        return "Your task list shows File Q3 taxes is overdue."

    monkeypatch.setattr(orchestrator_service, "generate_reply", _fake_generate_reply)

    response = _send(authenticated_client, "what's overdue?")
    assert response.status_code == 200
    assert "File Q3 taxes is overdue" in response.json()["assistant_message"]["content"]
    assert "File Q3 taxes" in captured["context"]


# ---- deterministic write-intent detection vs. the adversarial guarantee --------


def test_clear_write_request_gets_unavailability_message_without_calling_model(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def _track(**kwargs):
        nonlocal call_count
        call_count += 1
        return "should never be called"

    monkeypatch.setattr(orchestrator_service, "generate_reply", _track)

    response = _send(authenticated_client, "please create a task called Buy milk")
    assert response.status_code == 200
    assert call_count == 0
    assert response.json()["assistant_message"]["content"] == chat_service.WRITE_UNAVAILABLE_MESSAGE

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Buy milk" for t in tasks)


def test_ambiguous_write_request_reaches_model_and_adversarial_check_still_holds(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The undetected case. Seeds real Task/CalendarEvent/InboxItem/
    LifeArea data, sends a phrasing detect_clear_write_intent does NOT
    catch, mocks the model to falsely claim an action was taken, and
    proves the actual database is byte-for-byte unchanged regardless.
    """
    from app.modules.chat.write_intent import detect_clear_write_intent

    ambiguous_message = "I won't be free for my dentist appointment anymore"
    assert detect_clear_write_intent(ambiguous_message) is False

    task_before = authenticated_client.post("/api/v1/tasks", json={"title": "Untouched task"}).json()
    event_before = authenticated_client.post(
        "/api/v1/calendar/events", json={"title": "Dentist appointment", "starts_at": "2031-01-01T09:00:00Z"}
    ).json()
    life_area_before = authenticated_client.post("/api/v1/life-areas", json={"name": "Health"}).json()
    tasks_before = authenticated_client.get("/api/v1/tasks").json()
    events_before = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2030-01-01T00:00:00Z", "to": "2032-01-01T00:00:00Z"}
    ).json()
    life_areas_before = authenticated_client.get("/api/v1/life-areas").json()

    monkeypatch.setattr(
        orchestrator_service, "generate_reply", lambda **kwargs: "I've canceled your dentist appointment for you."
    )

    response = _send(authenticated_client, ambiguous_message)
    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"] == "I've canceled your dentist appointment for you."

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    events_after = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2030-01-01T00:00:00Z", "to": "2032-01-01T00:00:00Z"}
    ).json()
    life_areas_after = authenticated_client.get("/api/v1/life-areas").json()

    assert tasks_after == tasks_before
    assert events_after == events_before
    assert life_areas_after == life_areas_before
    assert task_before["id"] in [t["id"] for t in tasks_after]
    assert any(e["id"] == event_before["id"] for e in events_after if e["source"] == "event")
    assert life_area_before["id"] in [a["id"] for a in life_areas_after]


# ---- bounded conversation history ------------------------------------------------


def test_history_capped_at_max_message_count(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seeds more than _MAX_HISTORY_MESSAGES real messages via the real
    API, then confirms only the most recent N reach the Orchestrator —
    through the real send_message -> list_recent_messages flow, not
    _trim_to_char_budget in isolation (a separate concern, tested below).
    """
    captured = {}

    def _fake_generate_reply(*, history, context, user_message):
        captured["history"] = history
        return "ok"

    monkeypatch.setattr(orchestrator_service, "generate_reply", _fake_generate_reply)

    for i in range(chat_service._MAX_HISTORY_MESSAGES + 10):
        _send(authenticated_client, f"seed message {i}")

    _send(authenticated_client, "final message")
    assert len(captured["history"]) <= chat_service._MAX_HISTORY_MESSAGES


def test_history_char_budget_truncates_single_oversized_message_rather_than_violating_cap() -> None:
    oversized = ChatMessage(space_id=1, user_id=1, role="user", content="x" * 10_000)
    trimmed = chat_service._trim_to_char_budget([oversized], max_chars=chat_service._MAX_HISTORY_CHARS)

    assert len(trimmed) == 1
    assert len(trimmed[0].content) == chat_service._MAX_HISTORY_CHARS
    assert trimmed[0].content == ("x" * 10_000)[-chat_service._MAX_HISTORY_CHARS :]


def test_history_char_budget_drops_oldest_first_when_over_budget() -> None:
    messages = [
        ChatMessage(space_id=1, user_id=1, role="user", content="a" * 2000),
        ChatMessage(space_id=1, user_id=1, role="assistant", content="b" * 2000),
        ChatMessage(space_id=1, user_id=1, role="user", content="c" * 2000),
    ]
    trimmed = chat_service._trim_to_char_budget(messages, max_chars=3000)

    total_len = sum(len(t.content) for t in trimmed)
    assert total_len <= 3000
    # The oldest ("a"*2000) must be dropped first.
    assert not any(turn.content.startswith("a") for turn in trimmed)
    assert any(turn.content.startswith("c") for turn in trimmed)  # newest kept


def test_history_presented_in_chronological_order() -> None:
    messages = [
        ChatMessage(space_id=1, user_id=1, role="user", content="first"),
        ChatMessage(space_id=1, user_id=1, role="assistant", content="second"),
        ChatMessage(space_id=1, user_id=1, role="user", content="third"),
    ]
    trimmed = chat_service._trim_to_char_budget(messages, max_chars=100_000)
    assert [t.content for t in trimmed] == ["first", "second", "third"]


def test_incoming_message_over_length_cap_rejected_before_persisting(
    authenticated_client: TestClient, db_session: Session
) -> None:
    before = _message_count(db_session)
    too_long = "x" * (chat_service._MAX_INCOMING_MESSAGE_CHARS + 1)

    response = _send(authenticated_client, too_long)
    assert response.status_code == 422

    after = _message_count(db_session)
    assert after == before


def test_assistant_reply_over_length_cap_is_truncated_before_persisting(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    oversized_reply = "y" * (chat_service._MAX_ASSISTANT_MESSAGE_CHARS + 500)
    monkeypatch.setattr(orchestrator_service, "generate_reply", lambda **kwargs: oversized_reply)

    response = _send(authenticated_client, "tell me something long")
    assert response.status_code == 200
    stored_content = response.json()["assistant_message"]["content"]
    assert len(stored_content) <= chat_service._MAX_ASSISTANT_MESSAGE_CHARS + len("… [truncated]")
    assert stored_content.endswith("… [truncated]")


def test_incoming_and_history_budgets_are_independent(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def _fake_generate_reply(*, history, context, user_message):
        captured["history_len"] = len(history)
        return "ok"

    monkeypatch.setattr(orchestrator_service, "generate_reply", _fake_generate_reply)

    # Seed history near its own cap, then send a new message near its own cap.
    for _ in range(3):
        authenticated_client.post(
            "/api/v1/chat/messages",
            json={
                "content": "y" * 1000,
                "tomorrow_start": TOMORROW_START.isoformat(),
                "window_end": WINDOW_END.isoformat(),
            },
        )

    near_cap_message = "z" * (chat_service._MAX_INCOMING_MESSAGE_CHARS - 10)
    response = _send(authenticated_client, near_cap_message)
    assert response.status_code == 200
    assert response.json()["user_message"]["content"] == near_cap_message


# ---- user/space isolation ---------------------------------------------------------


def test_chat_history_excludes_other_users_messages_even_with_matching_space_id(db_session: Session) -> None:
    """Directly inserts a second user's message tagged with the SAME
    space_id as the first — bypassing normal app flow — to prove
    list_recent_messages' own filter holds regardless of whatever the
    Space ownership fix does elsewhere. Defense in depth.
    """
    from app.modules.auth import service as auth_service
    from app.modules.auth.models import User
    from app.modules.spaces import service as spaces_service

    first_user = auth_service.get_the_user(db_session)
    if first_user is None:
        first_user = User(password_hash=auth_service.hash_password("x"))
        db_session.add(first_user)
        db_session.commit()
        db_session.refresh(first_user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, first_user.id)

    second_user = User(password_hash=auth_service.hash_password("y"))
    db_session.add(second_user)
    db_session.commit()
    db_session.refresh(second_user)

    db_session.add(ChatMessage(space_id=space.id, user_id=first_user.id, role="user", content="first user's message"))
    db_session.add(
        ChatMessage(space_id=space.id, user_id=second_user.id, role="user", content="second user's message")
    )
    db_session.commit()

    history = chat_service.list_recent_messages(db_session, space.id, first_user.id)
    contents = [m.content for m in history]
    assert "first user's message" in contents
    assert "second user's message" not in contents


def test_chat_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat/messages",
        json={"content": "hi", "tomorrow_start": TOMORROW_START.isoformat(), "window_end": WINDOW_END.isoformat()},
    )
    assert response.status_code == 401
    assert client.get("/api/v1/chat/messages").status_code == 401


# ---- ai_traces reconfirmation -----------------------------------------------------


def test_no_prompt_or_response_or_key_in_ai_traces_for_chat_calls(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.model_router import service as model_router_service

    marker = "CHAT_MARKER_SECRET_XYZ"

    class _FakeUsage:
        input_tokens = 5
        output_tokens = 5

    class _FakeTextBlock:
        type = "text"
        text = f"response with {marker}"

    class _FakeMessage:
        content = [_FakeTextBlock()]
        usage = _FakeUsage()

    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage())

    response = _send(authenticated_client, f"question containing {marker}")
    assert response.status_code == 200

    rows = db_session.execute(text("SELECT * FROM ai_traces")).mappings().all()
    assert len(rows) >= 1
    for row in rows:
        for value in row.values():
            assert marker not in str(value)
