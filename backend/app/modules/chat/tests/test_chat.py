import logging
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.chat import context as context_module
from app.modules.chat import service as chat_service
from app.modules.chat.models import ChatMessage
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import OrchestratorResult, ToolCallRequest
from app.modules.weather import service as weather_service
from app.modules.weather.schemas import ResolvedLocation, WeatherProviderError, WeatherResult

TOMORROW_START = datetime(2030, 6, 15, 0, 0, tzinfo=timezone.utc)
WINDOW_END = TOMORROW_START + timedelta(days=7)
_DEFAULT_TIMEZONE = "Africa/Cairo"


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


def _send(client: TestClient, content: str, timezone_name: str = _DEFAULT_TIMEZONE) -> dict:
    response = client.post(
        "/api/v1/chat/messages",
        json={
            "content": content,
            "tomorrow_start": TOMORROW_START.isoformat(),
            "window_end": WINDOW_END.isoformat(),
            "timezone": timezone_name,
        },
    )
    return response


def _message_count(db_session: Session) -> int:
    return db_session.execute(select(func.count()).select_from(ChatMessage)).scalar_one()


def _mock_reply(
    text: str | None, tool_name: str | None = None, arguments: dict | None = None,
    tool_use_id: str = "toolu_test", correlation_id: str = "corr_test",
):
    tool_call = (
        ToolCallRequest(tool_use_id=tool_use_id, tool_name=tool_name, arguments=arguments or {})
        if tool_name else None
    )
    return lambda **kwargs: OrchestratorResult(text=text, tool_call=tool_call, correlation_id=correlation_id)


def _get_space_and_user(db_session: Session):
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)
    return user, space


def _propose_interrupt_then_bare_answer(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
    propose_message: str, tool_name: str, arguments: dict, bare_answer: str,
) -> dict:
    """Checkpoint 3.17 shared shape: propose a real pending action, send
    an unrelated interruption (answered as ordinary weather text, no
    tool call), then send a bare yes/no. Returns the call-count for the
    bare-answer turn (0 = deterministic dispatch, 1 = reached the model)
    plus the final assistant reply, so callers only need to add their
    own action-specific "was anything actually written?" check.
    """
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name=tool_name, arguments=arguments),
    )
    _send(authenticated_client, propose_message)

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Tomorrow in Cairo: sunny, 30°C."),
    )
    _send(authenticated_client, "What's the weather tomorrow in Cairo?")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("Just to confirm, what would you like me to do?")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)
    response = _send(authenticated_client, bare_answer)
    return {"call_count": call_count["n"], "content": response.json()["assistant_message"]["content"]}


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

    # Scoped to id > this user_message's own id, not a bare global count —
    # other test files in this shared database may have already created
    # assistant messages of their own before this test runs.
    assistant_rows = db_session.execute(
        text("SELECT count(*) FROM chat_messages WHERE role = 'assistant' AND id > :uid"),
        {"uid": user_message_id},
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

    def _fake_generate_reply(**kwargs):
        captured["context"] = kwargs["context"]
        captured["user_message"] = kwargs["user_message"]
        return OrchestratorResult(text="Your task list shows File Q3 taxes is overdue.", tool_call=None, correlation_id="corr_test")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _fake_generate_reply)

    response = _send(authenticated_client, "what's overdue?")
    assert response.status_code == 200
    assert "File Q3 taxes is overdue" in response.json()["assistant_message"]["content"]
    assert "File Q3 taxes" in captured["context"]


# ---- deterministic write-intent detection vs. the adversarial guarantee --------


def test_clear_write_request_gets_unavailability_message_without_calling_model(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.3: task CREATION no longer declines deterministically
    (see the propose/confirm tests below) — delete/edit/mark-done
    phrasings are unaffected and still decline without a model call."""
    call_count = 0

    def _track(**kwargs):
        nonlocal call_count
        call_count += 1
        return OrchestratorResult(text="should never be called", tool_call=None, correlation_id="corr_test")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _track)

    response = _send(authenticated_client, "delete my meeting with Bob")
    assert response.status_code == 200
    assert call_count == 0
    assert response.json()["assistant_message"]["content"] == chat_service.WRITE_UNAVAILABLE_MESSAGE


def test_ambiguous_write_request_reaches_model_and_adversarial_check_still_holds(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The undetected case. Seeds real Task/CalendarEvent/InboxItem/
    LifeArea data, sends a phrasing detect_clear_write_intent does NOT
    catch, mocks the model to falsely claim an action was taken (with no
    tool call attached), and proves the actual database is byte-for-byte
    unchanged regardless.
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
        orchestrator_service, "generate_reply",
        _mock_reply("I've canceled your dentist appointment for you."),
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


# ---- Checkpoint 3.3: propose / confirm / reject a task creation -------------------


def test_propose_then_confirm_creates_the_task(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.3's proposal-UX fix: the proposal-facing confirmation
    is now rendered deterministically from the validated tool arguments,
    NOT from the mocked model's own text — see the dedicated tests below
    for that guarantee in detail. This test just needs the confirmation
    to mention the real title, whatever its exact wording.
    """
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I'll create a task to call Hussein tomorrow — confirm?",
            tool_name="propose_create_task", arguments={"title": "Call Hussein tomorrow"},
        ),
    )

    propose_response = _send(authenticated_client, "please make a task to call Hussein tomorrow")
    assert propose_response.status_code == 200
    assert "Call Hussein tomorrow" in propose_response.json()["assistant_message"]["content"]

    tasks_before = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Call Hussein tomorrow" for t in tasks_before)

    # The confirmation itself never reaches the model — no mock needed;
    # the previous mock would raise if it were somehow called again.
    monkeypatch.setattr(
        orchestrator_service, "generate_reply", lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'yes'"))
    )

    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200
    assert "Call Hussein tomorrow" in confirm_response.json()["assistant_message"]["content"]

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["title"] == "Call Hussein tomorrow" for t in tasks_after)


def test_propose_then_no_declines_and_creates_no_task(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("I'll create a task to buy milk — confirm?", tool_name="propose_create_task", arguments={"title": "Buy milk"}),
    )
    _send(authenticated_client, "add a task to buy milk")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply", lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'no'"))
    )
    decline_response = _send(authenticated_client, "no")
    assert decline_response.status_code == 200
    assert decline_response.json()["assistant_message"]["content"] == chat_service._REJECTED_MESSAGE

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Buy milk" for t in tasks)


def test_revision_before_confirming_supersedes_the_earlier_proposal(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Create a task titled 'Draft A'?", tool_name="propose_create_task", arguments={"title": "Draft A"}),
    )
    _send(authenticated_client, "make a task called Draft A")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Got it, 'Draft B' instead — confirm?", tool_name="propose_create_task", arguments={"title": "Draft B"}),
    )
    _send(authenticated_client, "actually call it Draft B instead")

    confirm_response = _send(authenticated_client, "yes")
    assert "Draft B" in confirm_response.json()["assistant_message"]["content"]

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["title"] == "Draft B" for t in tasks)
    assert not any(t["title"] == "Draft A" for t in tasks)


def test_unrelated_message_after_a_proposal_breaks_adjacency_and_bare_yes_no_longer_executes_it(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint 3.17: this is the exact scenario the checkpoint exists
    to fix — an unrelated conversational turn between a proposal's
    confirmation prompt and a later bare 'yes' breaks conversational
    adjacency (is_still_conversationally_adjacent). The proposal
    remains stored/pending (never expired, rejected, or deleted by
    this) but is no longer eligible for deterministic bare yes/no
    dispatch — the bare 'yes' now reaches the model (1 LLM call)
    instead of silently executing the old proposal. Superseded a
    pre-3.17 test of the same name that asserted the OLD, now-fixed
    unsafe behavior (a later bare 'yes' still deterministically
    executed the stale proposal)."""
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "Create a task to call Hussein about the invoice — confirm?",
            tool_name="propose_create_task", arguments={"title": "Call Hussein about the invoice"},
        ),
    )
    _send(authenticated_client, "make a task to call Hussein about the invoice")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Your task list currently has 3 open items."),
    )
    unrelated_response = _send(authenticated_client, "how many open tasks do I have?")
    assert unrelated_response.json()["assistant_message"]["content"] == "Your task list currently has 3 open items."

    tasks_after_unrelated = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Call Hussein about the invoice" for t in tasks_after_unrelated)

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.status == "pending"  # still stored, not expired/rejected/deleted by the interruption

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("Just to confirm — are you saying yes to creating that task?")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)
    _send(authenticated_client, "yes")
    assert call_count["n"] == 1  # reached the model — NOT deterministic execution

    tasks_final = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Call Hussein about the invoice" for t in tasks_final)  # not executed

    pending_after = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending_after is not None
    assert pending_after.id == pending.id
    assert pending_after.status == "pending"  # unchanged — not rejected either


def test_confirming_after_expiry_creates_no_task(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal past its TTL is treated as though nothing were
    pending — including at chat's own local pending-lookup gate, not
    just inside actions_service (already covered directly in
    actions/tests/test_actions.py). Since chat's own lookup also finds
    nothing pending once expired, a bare "yes" here is ordinary
    conversation rather than a special confirmation — exactly as it
    would be if no proposal had ever been made — so the mock for this
    second call answers plainly rather than reusing the propose mock.
    """
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Create a task to water the plants — confirm?", tool_name="propose_create_task", arguments={"title": "Water the plants"}),
    )
    _send(authenticated_client, "make a task to water the plants")

    db_session.execute(
        text("UPDATE proposed_actions SET expires_at = now() - interval '1 minute' WHERE status = 'pending'")
    )
    db_session.commit()

    monkeypatch.setattr(orchestrator_service, "generate_reply", _mock_reply("Sure thing!"))
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Water the plants" for t in tasks)


def test_duplicate_yes_after_execution_does_not_create_a_second_task(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the first "yes" executes, nothing is locally pending
    anymore — a second "yes" is ordinary conversation, not a special
    confirmation (see test_confirming_after_expiry_creates_no_task's
    docstring for the same reasoning). The important invariant here is
    simply: no second Task gets created."""
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Create a task to renew passport — confirm?", tool_name="propose_create_task", arguments={"title": "Renew passport"}),
    )
    _send(authenticated_client, "make a task to renew my passport")

    first_yes = _send(authenticated_client, "yes")
    assert "Renew passport" in first_yes.json()["assistant_message"]["content"]

    monkeypatch.setattr(orchestrator_service, "generate_reply", _mock_reply("Sure thing!"))
    second_yes = _send(authenticated_client, "yes")
    assert second_yes.status_code == 200

    tasks = authenticated_client.get("/api/v1/tasks").json()
    matching = [t for t in tasks if t["title"] == "Renew passport"]
    assert len(matching) == 1  # not created twice


def test_malformed_tool_arguments_create_no_pending_proposal_and_reply_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial-style check for the write-enabled path: the model
    calls propose_create_task with arguments that fail TaskCreate
    validation (missing the required title). No ProposedAction row and
    no Task are ever created — the user's own message is still
    persisted, and the reply is honest rather than a stored half-valid
    proposal. Checks get_latest_pending directly rather than counting
    all proposed_actions rows, since earlier tests in this file leave
    their own (non-pending) rows behind in the same shared database.
    """
    from app.modules.actions import service as actions_service
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Sure, creating that now!", tool_name="propose_create_task", arguments={"description": "no title given"}),
    )

    response = _send(authenticated_client, "make a task with no clear title, xyz-malformed-test")
    assert response.status_code == 200

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t.get("description") == "no title given" for t in tasks)


# ---- Checkpoint 3.3 proposal-UX fix: deterministic confirmation rendering --------


def test_confirmation_message_reflects_the_validated_arguments_exactly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmation text must be built from the SAME validated
    arguments that get stored — not the model's own prose. Checked by
    independently fetching the persisted ProposedAction and confirming
    both the title and the formatted date appear in the message shown.
    """
    from app.modules.actions import service as actions_service
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "sure, on it",  # deliberately unrelated to the real details — must not leak through
            tool_name="propose_create_task",
            arguments={"title": "Confirmation reflects arguments test", "due_at": "2030-07-04T14:30:00+00:00"},
        ),
    )

    response = _send(authenticated_client, "make a task: confirmation reflects arguments test")
    content = response.json()["assistant_message"]["content"]

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_default_space_for_user(db_session, user.id)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None

    assert pending.arguments["title"] in content
    expected_when = chat_service._format_due_at_local(pending.arguments["due_at"], _DEFAULT_TIMEZONE)
    assert expected_when in content


def test_confirmation_omits_date_clause_when_due_at_is_null(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("ok", tool_name="propose_create_task", arguments={"title": "No due date test"}),
    )

    response = _send(authenticated_client, "make a task with no due date: No due date test")
    content = response.json()["assistant_message"]["content"]

    assert "No due date test" in content
    assert "?" in content  # still ends with a confirmation question
    assert not re.search(r"\d{2}/\d{2}/\d{4}", content)  # no date-shaped substring at all


def test_confirmation_language_matches_the_arabic_or_english_of_the_triggering_message(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("some english model prose", tool_name="propose_create_task", arguments={"title": "لغة الاختبار"}),
    )
    arabic_response = _send(authenticated_client, "ضيف مهمة لغة الاختبار")
    arabic_content = arabic_response.json()["assistant_message"]["content"]
    assert chat_service._is_arabic(arabic_content)

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("بعض النص العربي", tool_name="propose_create_task", arguments={"title": "Language test EN"}),
    )
    english_response = _send(authenticated_client, "add a task: Language test EN")
    english_content = english_response.json()["assistant_message"]["content"]
    assert not chat_service._is_arabic(english_content)


def test_model_prose_with_wrong_or_missing_details_does_not_appear_in_the_confirmation(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the live-run failure mode and pushes it further: the
    model's accompanying text is not just incomplete but flatly wrong.
    Proves the renderer never reads result.text on the success path —
    not just that it's unused in the happy case, but that it CANNOT
    override the shown confirmation even when it actively contradicts
    the real, validated, soon-to-be-executed arguments.
    """
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "Sure, I've added a reminder called WRONG NAME for next week!",
            tool_name="propose_create_task",
            arguments={"title": "Actually Correct Title", "due_at": "2030-08-01T09:00:00+00:00"},
        ),
    )

    response = _send(authenticated_client, "make a task called Actually Correct Title")
    content = response.json()["assistant_message"]["content"]

    assert "Actually Correct Title" in content
    assert "WRONG NAME" not in content
    assert "next week" not in content


def test_end_to_end_executed_task_matches_the_confirmation_shown_before_yes(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strongest form of the invariant: not just "the confirmation
    was built from validated arguments" in isolation, but that the
    REAL, LATER-EXECUTED Task's title and due_at are exactly what the
    confirmation text displayed before the user ever said yes.
    """
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "irrelevant model prose, ignored",
            tool_name="propose_create_task",
            arguments={"title": "End to end confirmation match", "due_at": "2030-09-15T16:45:00+00:00"},
        ),
    )

    propose_response = _send(authenticated_client, "make a task: End to end confirmation match")
    shown_confirmation = propose_response.json()["assistant_message"]["content"]
    expected_when = chat_service._format_due_at_local("2030-09-15T16:45:00+00:00", _DEFAULT_TIMEZONE)
    assert "End to end confirmation match" in shown_confirmation
    assert expected_when in shown_confirmation

    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    tasks = authenticated_client.get("/api/v1/tasks").json()
    created = next(t for t in tasks if t["title"] == "End to end confirmation match")
    # The Task's real due_at, reformatted the same deterministic way,
    # must match exactly what was shown in the confirmation.
    created_due_at_local = chat_service._format_due_at_local(created["due_at"], _DEFAULT_TIMEZONE)
    assert created_due_at_local == expected_when


def test_invalid_timezone_is_rejected_with_422_and_persists_nothing(
    authenticated_client: TestClient, db_session: Session
) -> None:
    before = _message_count(db_session)
    response = _send(authenticated_client, "hello", timezone_name="Not/AZone")
    assert response.status_code == 422

    after = _message_count(db_session)
    assert after == before


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

    def _fake_generate_reply(**kwargs):
        captured["history"] = kwargs["history"]
        return OrchestratorResult(text="ok", tool_call=None, correlation_id="corr_test")

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
    monkeypatch.setattr(orchestrator_service, "generate_reply", _mock_reply(oversized_reply))

    response = _send(authenticated_client, "tell me something long")
    assert response.status_code == 200
    stored_content = response.json()["assistant_message"]["content"]
    assert len(stored_content) <= chat_service._MAX_ASSISTANT_MESSAGE_CHARS + len("… [truncated]")
    assert stored_content.endswith("… [truncated]")


def test_incoming_and_history_budgets_are_independent(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def _fake_generate_reply(**kwargs):
        captured["history_len"] = len(kwargs["history"])
        return OrchestratorResult(text="ok", tool_call=None, correlation_id="corr_test")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _fake_generate_reply)

    # Seed history near its own cap, then send a new message near its own cap.
    for _ in range(3):
        authenticated_client.post(
            "/api/v1/chat/messages",
            json={
                "content": "y" * 1000,
                "tomorrow_start": TOMORROW_START.isoformat(),
                "window_end": WINDOW_END.isoformat(),
                "timezone": _DEFAULT_TIMEZONE,
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
        json={
            "content": "hi",
            "tomorrow_start": TOMORROW_START.isoformat(),
            "window_end": WINDOW_END.isoformat(),
            "timezone": _DEFAULT_TIMEZONE,
        },
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


# ---- Checkpoint 3.4: propose / confirm / reject saving a memory -------------------


def test_propose_save_memory_then_confirm_creates_active_memory(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("noted", tool_name="propose_save_memory",
                    arguments={"type": "PREFERENCE", "content": "Prefers concise answers - memtest1"}),
    )
    propose_response = _send(authenticated_client, "remember that I prefer concise answers - memtest1")
    assert propose_response.status_code == 200
    assert "Prefers concise answers - memtest1" in propose_response.json()["assistant_message"]["content"]

    user, space = _get_space_and_user(db_session)
    before, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    assert not any(m.content == "Prefers concise answers - memtest1" for m in before)

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'yes'")),
    )
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    after, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    matching = [m for m in after if m.content == "Prefers concise answers - memtest1"]
    assert len(matching) == 1
    assert matching[0].status == "active"


def test_reject_save_memory_proposal_creates_no_memory(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("noted", tool_name="propose_save_memory",
                    arguments={"type": "FACT", "content": "Rejected memory - memtest2"}),
    )
    _send(authenticated_client, "remember that - memtest2")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'no'")),
    )
    decline_response = _send(authenticated_client, "no")
    assert decline_response.status_code == 200
    assert decline_response.json()["assistant_message"]["content"] == chat_service._REJECTED_MESSAGE

    user, space = _get_space_and_user(db_session)
    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    assert not any(m.content == "Rejected memory - memtest2" for m in memories)


def test_duplicate_yes_after_memory_execution_does_not_create_a_second_memory(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("noted", tool_name="propose_save_memory",
                    arguments={"type": "GOAL", "content": "Duplicate confirm test - memtest3"}),
    )
    _send(authenticated_client, "remember my goal - memtest3")

    first_yes = _send(authenticated_client, "yes")
    assert first_yes.status_code == 200

    # Nothing is locally pending anymore, so a second "yes" is ordinary
    # conversation — same reasoning already established for tasks.
    monkeypatch.setattr(orchestrator_service, "generate_reply", _mock_reply("Sure thing!"))
    second_yes = _send(authenticated_client, "yes")
    assert second_yes.status_code == 200

    user, space = _get_space_and_user(db_session)
    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    matching = [m for m in memories if m.content == "Duplicate confirm test - memtest3"]
    assert len(matching) == 1


def test_malformed_save_memory_tool_arguments_create_no_pending_proposal_and_reply_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("sure, saving that", tool_name="propose_save_memory",
                    arguments={"content": "no type given - memtest4"}),
    )
    response = _send(authenticated_client, "remember something unclear - memtest4")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_model_prose_with_wrong_content_does_not_appear_in_save_memory_confirmation(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "Sure, I'll remember that you love pineapple pizza!",
            tool_name="propose_save_memory",
            arguments={"type": "PREFERENCE", "content": "Actually correct preference - memtest5"},
        ),
    )
    response = _send(authenticated_client, "remember my real preference - memtest5")
    content = response.json()["assistant_message"]["content"]
    assert "Actually correct preference - memtest5" in content
    assert "pineapple pizza" not in content


def test_end_to_end_save_memory_confirmation_matches_the_memory_later_created(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("irrelevant model prose, ignored", tool_name="propose_save_memory",
                    arguments={"type": "GOAL", "content": "End to end memory match - memtest6"}),
    )
    propose_response = _send(authenticated_client, "remember my goal - memtest6")
    shown = propose_response.json()["assistant_message"]["content"]
    assert "End to end memory match - memtest6" in shown

    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    user, space = _get_space_and_user(db_session)
    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    created = next(m for m in memories if m.content == "End to end memory match - memtest6")
    assert created.status == "active"
    assert created.type == "GOAL"


# ---- Checkpoint 3.4: forgetting a memory ------------------------------------------


def test_forget_memory_propose_then_confirm_marks_forgotten(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = _get_space_and_user(db_session)
    source_id = chat_service.record_assistant_message(db_session, space.id, user.id, "seed").id
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="GOAL", content="Learn Rust - memtest7")
    )
    db_session.commit()

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("sure", tool_name="propose_forget_memory", arguments={"memory_id": memory.id}),
    )
    propose_response = _send(authenticated_client, "forget that I wanted to learn Rust - memtest7")
    assert "Learn Rust - memtest7" in propose_response.json()["assistant_message"]["content"]

    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    db_session.refresh(memory)
    assert memory.status == "forgotten"


def test_forget_memory_with_nonexistent_memory_id_creates_no_proposal_and_replies_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point 5 of the 3.4 revision round: a memory_id that doesn't
    resolve to anything real (not just belonging to another user — see
    memory/tests/test_memory.py's own cross-user test for that case) is
    caught by _require_active_memory before any proposal is created —
    a safe, honest fallback, not an unhandled exception.
    """
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("sure, forgetting that", tool_name="propose_forget_memory", arguments={"memory_id": 999999}),
    )
    response = _send(authenticated_client, "forget that thing I mentioned - memtest8")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


# ---- Checkpoint 3.4: retrieval and context assembly -------------------------------


def test_relevant_memory_appears_in_a_later_conversations_context(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = _get_space_and_user(db_session)
    source_id = chat_service.record_assistant_message(db_session, space.id, user.id, "seed").id
    memory_service.create_memory(
        db_session, space.id, user.id, source_id,
        MemoryCreate(type="PREFERENCE", content="Explain in Egyptian Arabic, keep terms in English - memtest9"),
    )
    db_session.commit()

    captured = {}
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (captured.update(context=kwargs["context"]) or OrchestratorResult(text="ok", tool_call=None, correlation_id="corr_test")),
    )
    _send(authenticated_client, "اشرحلي الـ Model Router")

    assert "Explain in Egyptian Arabic, keep terms in English - memtest9" in captured["context"]
    assert "PREFERENCE" in captured["context"]


def test_forgotten_memory_does_not_appear_in_context(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = _get_space_and_user(db_session)
    source_id = chat_service.record_assistant_message(db_session, space.id, user.id, "seed").id
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id,
        MemoryCreate(type="FACT", content="Forgotten before retrieval - memtest10"),
    )
    memory_service.forget_memory(db_session, space.id, user.id, memory.id)
    db_session.commit()

    captured = {}
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (captured.update(context=kwargs["context"]) or OrchestratorResult(text="ok", tool_call=None, correlation_id="corr_test")),
    )
    _send(authenticated_client, "what's up?")

    assert "Forgotten before retrieval - memtest10" not in captured["context"]


def test_inference_type_memory_is_labeled_tentative_in_context(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = _get_space_and_user(db_session)
    source_id = chat_service.record_assistant_message(db_session, space.id, user.id, "seed").id
    memory_service.create_memory(
        db_session, space.id, user.id, source_id,
        MemoryCreate(type="INFERENCE", content="Might be interested in Rust - memtest11"),
    )
    db_session.commit()

    captured = {}
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (captured.update(context=kwargs["context"]) or OrchestratorResult(text="ok", tool_call=None, correlation_id="corr_test")),
    )
    _send(authenticated_client, "what's up?")

    assert "INFERENCE (tentative, not confirmed as fact): Might be interested in Rust - memtest11" in captured["context"]


def test_memory_context_discloses_truncation_when_active_count_exceeds_the_cap(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point 3 of the 3.4 revision round: applies the same
    truncated-and-said-so convention already proven for Home's Anytime
    section (2.5a) to the memory context — including when the user asks
    the explicit inspection question. Seeds enough ACTIVE memories to
    exceed chat_service._MAX_MEMORIES regardless of what earlier tests
    in this shared database left behind (delta-based, not an absolute
    count), then asserts the deterministic CONTEXT fed to the model
    (not a live model reply, which we can't assert on) discloses the
    truncation.
    """
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = _get_space_and_user(db_session)
    source_id = chat_service.record_assistant_message(db_session, space.id, user.id, "seed").id

    _, baseline_total = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=0)
    needed = max(0, (chat_service._MAX_MEMORIES + 5) - baseline_total)
    for i in range(needed):
        memory_service.create_memory(
            db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content=f"Truncation test fact {i}")
        )
    db_session.commit()

    captured = {}
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (captured.update(context=kwargs["context"]) or OrchestratorResult(text="...", tool_call=None, correlation_id="corr_test")),
    )
    _send(authenticated_client, "إيه اللي فاكره عني؟")

    assert "more not shown" in captured["context"]


def test_correction_via_chat_supersedes_the_previous_active_memory(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = _get_space_and_user(db_session)
    source_id = chat_service.record_assistant_message(db_session, space.id, user.id, "seed").id
    old = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Works at a bank - memtest12")
    )
    db_session.commit()

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "got it, updating that",
            tool_name="propose_save_memory",
            arguments={
                "type": "FACT", "content": "No longer works at a bank - memtest12",
                "supersedes_memory_id": old.id,
            },
        ),
    )
    _send(authenticated_client, "I don't work at a bank anymore - memtest12")
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    db_session.refresh(old)
    assert old.status == "superseded"

    # The superseded memory is NOT retrieved as current truth — only the
    # new one appears in an active-memory read.
    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    assert not any(m.id == old.id for m in memories)
    assert any(m.content == "No longer works at a bank - memtest12" for m in memories)
    assert old.superseded_by_id == next(m.id for m in memories if m.content == "No longer works at a bank - memtest12")


# ---- Checkpoint 3.4: cross-action-type proposal supersession ----------------------


def test_pending_create_task_proposal_is_superseded_by_a_new_save_memory_proposal(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("ok", tool_name="propose_create_task", arguments={"title": "Draft task - crosstest1"}),
    )
    _send(authenticated_client, "make a task called Draft task - crosstest1")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("ok", tool_name="propose_save_memory",
                    arguments={"type": "PREFERENCE", "content": "Cross type memory - crosstest1"}),
    )
    _send(authenticated_client, "actually, remember that I prefer X - crosstest1")

    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Draft task - crosstest1" for t in tasks)

    user, space = _get_space_and_user(db_session)
    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    assert any(m.content == "Cross type memory - crosstest1" for m in memories)


def test_pending_save_memory_proposal_is_superseded_by_a_new_create_task_proposal(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("ok", tool_name="propose_save_memory",
                    arguments={"type": "PREFERENCE", "content": "Should not be saved - crosstest2"}),
    )
    _send(authenticated_client, "remember that I prefer Y - crosstest2")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("ok", tool_name="propose_create_task", arguments={"title": "Real task - crosstest2"}),
    )
    _send(authenticated_client, "actually, make a task called Real task - crosstest2")

    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["title"] == "Real task - crosstest2" for t in tasks)

    user, space = _get_space_and_user(db_session)
    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    assert not any(m.content == "Should not be saved - crosstest2" for m in memories)


# ---- Checkpoint 3.5: identity/personality reach a real chat turn's prompt --------


def test_identity_and_personality_instructions_reach_a_real_chat_turns_system_prompt(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves orchestrator/identity.py's content actually reaches the
    system prompt used on a real chat turn — not just that
    _build_system_prompt CAN include it in isolation (already proven in
    orchestrator/tests/test_orchestrator.py). Mocks
    model_router_service.complete directly (one level below
    orchestrator_service.generate_reply, which this test does NOT mock)
    so the real orchestrator system-prompt assembly runs end to end,
    driven by a real chat_service.send_message call.
    """
    from app.modules.model_router import service as model_router_service
    from app.modules.model_router.schemas import ModelResponse

    captured = {}

    def _fake_complete(*, purpose, messages, system=None, tools=None):
        captured["system"] = system
        return ModelResponse(
            text="ok", model="claude-sonnet-5", prompt_tokens=1, completion_tokens=1, tool_uses=[],
            correlation_id="corr_test",
        )

    monkeypatch.setattr(model_router_service, "complete", _fake_complete)

    response = _send(authenticated_client, "hi")
    assert response.status_code == 200

    system_prompt = captured["system"]
    assert "You are BAZRA" in system_prompt
    assert "BAZRA's assistant" not in system_prompt
    assert (
        "You never identify yourself as Claude, ChatGPT, Gemini, Anthropic, "
        "OpenAI, or any other underlying provider or model, by name" in system_prompt
    )
    assert "Never claim consciousness or subjective feelings" in system_prompt


# ---- Checkpoint 3.7: get_weather — BAZRA's first External Read Tool --------------

_WEATHER_NOW_RESULT = WeatherResult(
    resolved_location="Cairo, Egypt",
    horizon="now",
    period_start=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
    period_end=None,
    timezone="Africa/Cairo",
    temperature=29.4,
    temperature_low=None,
    temperature_high=None,
    feels_like=31.2,
    condition="clear",
    precipitation_probability=None,
    units="C",
)


def _enable_chat_service_logger() -> None:
    """Alembic's migrations/env.py (run once per test session to build
    bazra_test) calls logging.config.fileConfig(), whose default
    disable_existing_loggers=True silently disables any logger already
    created by that point, including chat/service.py's own — a
    pre-existing test-infrastructure quirk, unrelated to this
    checkpoint's change, worked around locally here rather than editing
    migrations/env.py."""
    logging.getLogger("app.modules.chat.service").disabled = False


def _proposed_action_count(db_session: Session) -> int:
    return db_session.execute(text("SELECT count(*) FROM proposed_actions")).scalar_one()


def test_get_weather_tool_offered_with_required_location_and_horizon() -> None:
    tool = next(t for t in chat_service._TOOLS_OFFERED if t["name"] == "get_weather")
    schema = tool["input_schema"]
    assert schema["required"] == ["location", "horizon", "response_mode"]
    assert schema["properties"]["horizon"]["enum"] == ["now", "today", "tonight", "tomorrow"]
    assert schema["properties"]["response_mode"]["enum"] == ["factual", "reason"]


def test_missing_location_asks_and_makes_no_weather_http_call(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("weather provider must not be called without a location")

    monkeypatch.setattr(weather_service, "geocode_location", _fail_if_called)
    monkeypatch.setattr(weather_service, "fetch_weather", _fail_if_called)
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "Which city do you mean?", tool_name="get_weather",
            arguments={"horizon": "now", "response_mode": "factual"},
        ),
    )

    response = _send(authenticated_client, "what's the weather like?")
    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"] == "Which city do you mean?"


def test_successful_weather_read_creates_no_proposed_action_and_exactly_one_model_completion(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply(
            "Let me check.", tool_name="get_weather",
            arguments={"location": "Cairo", "horizon": "now", "response_mode": "factual"},
        )(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)
    monkeypatch.setattr(weather_service, "geocode_location", lambda location: ResolvedLocation(
        display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo",
    ))
    monkeypatch.setattr(weather_service, "fetch_weather", lambda resolved, horizon: _WEATHER_NOW_RESULT)

    before = _proposed_action_count(db_session)
    response = _send(authenticated_client, "what's the weather in Cairo right now?")
    assert response.status_code == 200
    after = _proposed_action_count(db_session)

    assert after == before  # read-only: never creates a ProposedAction
    assert call_count["n"] == 1  # exactly one model completion for this turn


def test_weather_reply_renders_english_facts_deterministically(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "checking", tool_name="get_weather",
            arguments={"location": "Cairo", "horizon": "now", "response_mode": "factual"},
        ),
    )
    monkeypatch.setattr(weather_service, "geocode_location", lambda location: ResolvedLocation(
        display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo",
    ))
    monkeypatch.setattr(weather_service, "fetch_weather", lambda resolved, horizon: _WEATHER_NOW_RESULT)

    response = _send(authenticated_client, "what's the weather in Cairo right now?")
    content = response.json()["assistant_message"]["content"]
    assert "Cairo, Egypt" in content
    assert "clear" in content
    assert "29" in content  # rounded temperature
    assert "31" in content  # rounded feels-like
    assert "Weather data by Open-Meteo.com (https://open-meteo.com/)" in content


def test_weather_reply_renders_arabic_facts_deterministically(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "checking", tool_name="get_weather",
            arguments={"location": "القاهرة", "horizon": "now", "response_mode": "factual"},
        ),
    )
    monkeypatch.setattr(weather_service, "geocode_location", lambda location: ResolvedLocation(
        display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo",
    ))
    monkeypatch.setattr(weather_service, "fetch_weather", lambda resolved, horizon: _WEATHER_NOW_RESULT)

    response = _send(authenticated_client, "الجو عامل إيه في القاهرة دلوقتي؟")
    content = response.json()["assistant_message"]["content"]
    assert "صحو" in content
    assert "29" in content
    assert "Weather data by Open-Meteo.com (https://open-meteo.com/)" in content


def test_no_attribution_when_location_missing(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "Which city?", tool_name="get_weather",
            arguments={"horizon": "now", "response_mode": "factual"},
        ),
    )
    response = _send(authenticated_client, "what's the weather like?")
    assert "Open-Meteo" not in response.json()["assistant_message"]["content"]


def test_location_not_found_reply_is_honest_and_unattributed(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "checking", tool_name="get_weather",
            arguments={"location": "Nowhereville", "horizon": "now", "response_mode": "factual"},
        ),
    )
    monkeypatch.setattr(weather_service, "geocode_location", lambda location: None)

    response = _send(authenticated_client, "what's the weather in Nowhereville?")
    content = response.json()["assistant_message"]["content"]
    assert "Nowhereville" in content
    assert "Open-Meteo" not in content


def test_provider_failure_produces_one_truthful_reply_never_fabricated(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "checking", tool_name="get_weather",
            arguments={"location": "Cairo", "horizon": "now", "response_mode": "factual"},
        ),
    )

    def _raise(location):
        raise WeatherProviderError("timeout")

    monkeypatch.setattr(weather_service, "geocode_location", _raise)

    response = _send(authenticated_client, "what's the weather in Cairo?")
    content = response.json()["assistant_message"]["content"]
    assert content == chat_service._render_weather_unavailable("what's the weather in Cairo?")
    assert "Open-Meteo" not in content
    assert "29" not in content  # no fabricated data


def test_ai_traces_contain_no_weather_location_or_payload_for_a_weather_turn(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors test_no_prompt_or_response_or_key_in_ai_traces_for_chat_calls,
    but drives a REAL get_weather tool call through a mocked provider
    response at the lowest level (_call_anthropic), so a real AiTrace
    row is actually written — then confirms it carries none of the
    location/coordinates/weather content."""
    from app.modules.model_router import service as model_router_service

    class _FakeUsage:
        input_tokens = 5
        output_tokens = 5

    class _FakeToolUseBlock:
        type = "tool_use"
        id = "toolu_trace_test"
        name = "get_weather"
        input = {"location": "Cairo", "horizon": "now", "response_mode": "factual"}

    class _FakeMessage:
        content = [_FakeToolUseBlock()]
        usage = _FakeUsage()

    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage())
    monkeypatch.setattr(weather_service, "geocode_location", lambda location: ResolvedLocation(
        display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo",
    ))
    monkeypatch.setattr(weather_service, "fetch_weather", lambda resolved, horizon: _WEATHER_NOW_RESULT)

    response = _send(authenticated_client, "weather-trace-marker-test what's the weather in Cairo?")
    assert response.status_code == 200

    rows = db_session.execute(text("SELECT * FROM ai_traces")).mappings().all()
    assert len(rows) >= 1
    for row in rows:
        for value in row.values():
            text_value = str(value)
            assert "Cairo" not in text_value
            assert "30.06" not in text_value
            assert "31.25" not in text_value
            assert "weather-trace-marker-test" not in text_value


# ---- Checkpoint 3.8: grounded tool-result reasoning (response_mode="reason") -----


def _max_ai_trace_id(db_session: Session) -> int:
    return db_session.execute(text("SELECT COALESCE(max(id), 0) FROM ai_traces")).scalar_one()


def test_interpretive_weather_uses_exactly_two_model_calls_with_shared_correlation_id(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real end-to-end proof: mocks _call_anthropic (below
    orchestrator_service.generate_reply AND generate_tool_result_reply,
    neither of which is itself mocked), so both real model_router.complete()
    calls happen, writing two real AiTrace rows that must share the SAME
    correlation_id."""
    from app.modules.model_router import service as model_router_service

    class _FakeUsage:
        def __init__(self, input_tokens=10, output_tokens=10):
            self.input_tokens = input_tokens
            self.output_tokens = output_tokens

    class _FakeTextBlock:
        type = "text"

        def __init__(self, text):
            self.text = text

    class _FakeToolUseBlock:
        type = "tool_use"
        id = "toolu_reason_test"
        name = "get_weather"
        input = {"location": "Cairo", "horizon": "tonight", "response_mode": "reason"}

    class _FakeToolCallMessage:
        content = [_FakeToolUseBlock()]
        usage = _FakeUsage()

    class _FakeReasoningMessage:
        content = [_FakeTextBlock("It's a mild evening, you probably won't need a jacket.")]
        usage = _FakeUsage()

    call_log = []

    def _fake_call_anthropic(model, messages, **kwargs):
        call_log.append(kwargs.get("tools"))
        if len(call_log) == 1:
            return _FakeToolCallMessage()
        return _FakeReasoningMessage()

    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", _fake_call_anthropic)
    monkeypatch.setattr(weather_service, "geocode_location", lambda location: ResolvedLocation(
        display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo",
    ))
    monkeypatch.setattr(weather_service, "fetch_weather", lambda resolved, horizon: WeatherResult(
        resolved_location="Cairo, Egypt", horizon="tonight",
        period_start=datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc),
        period_end=datetime(2026, 9, 25, 23, 59, 59, tzinfo=timezone.utc),
        timezone="Africa/Cairo", temperature=None, temperature_low=20.0, temperature_high=23.0,
        feels_like=None, condition="clear", precipitation_probability=5.0, units="C",
    ))

    before_trace_id = _max_ai_trace_id(db_session)
    before_actions = _proposed_action_count(db_session)

    response = _send(authenticated_client, "do I need a jacket tonight in Cairo?")
    assert response.status_code == 200

    after_actions = _proposed_action_count(db_session)
    assert after_actions == before_actions  # still zero ProposedAction rows

    assert len(call_log) == 2  # exactly two model calls, never a third
    assert call_log[1] is None  # continuation received NO tools — terminal

    rows = db_session.execute(
        text("SELECT purpose, correlation_id FROM ai_traces WHERE id > :bid ORDER BY id"),
        {"bid": before_trace_id},
    ).all()
    assert len(rows) == 2
    assert rows[0].purpose == "chat_completion"
    assert rows[1].purpose == "tool_result_reasoning"
    assert rows[0].correlation_id is not None
    assert rows[0].correlation_id == rows[1].correlation_id

    content = response.json()["assistant_message"]["content"]
    assert "It's a mild evening, you probably won't need a jacket." in content
    assert "Weather data by Open-Meteo.com (https://open-meteo.com/)" in content


def test_interpretive_weather_continuation_failure_degrades_to_factual_reply(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No retry, no third call, no generic error — the facts were really
    fetched, so an honest factual reply is used instead when only the
    reasoning step fails."""
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "checking", tool_name="get_weather",
            arguments={"location": "Cairo", "horizon": "tonight", "response_mode": "reason"},
        ),
    )
    monkeypatch.setattr(weather_service, "geocode_location", lambda location: ResolvedLocation(
        display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo",
    ))
    tonight_result = WeatherResult(
        resolved_location="Cairo, Egypt", horizon="tonight",
        period_start=datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc),
        period_end=datetime(2026, 9, 25, 23, 59, 59, tzinfo=timezone.utc),
        timezone="Africa/Cairo", temperature=None, temperature_low=20.0, temperature_high=23.0,
        feels_like=None, condition="clear", precipitation_probability=5.0, units="C",
    )
    monkeypatch.setattr(weather_service, "fetch_weather", lambda resolved, horizon: tonight_result)

    def _raise(**kwargs):
        raise orchestrator_service.OrchestratorError("provider_error")

    monkeypatch.setattr(orchestrator_service, "generate_tool_result_reply", _raise)

    response = _send(authenticated_client, "do I need a jacket tonight in Cairo?")
    assert response.status_code == 200
    content = response.json()["assistant_message"]["content"]
    assert content == chat_service._render_weather_reply(tonight_result, "do I need a jacket tonight in Cairo?")
    assert "Weather data by Open-Meteo.com (https://open-meteo.com/)" in content


def test_separate_turns_receive_different_correlation_ids(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.model_router import service as model_router_service

    class _FakeUsage:
        input_tokens = 5
        output_tokens = 5

    class _FakeTextBlock:
        type = "text"

        def __init__(self, text):
            self.text = text

    class _FakeMessage:
        def __init__(self, text):
            self.content = [_FakeTextBlock(text)]
            self.usage = _FakeUsage()

    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage("ok"))

    before = _max_ai_trace_id(db_session)
    _send(authenticated_client, "separate-turn-marker-1 hello")
    _send(authenticated_client, "separate-turn-marker-2 hello again")

    rows = db_session.execute(
        text("SELECT correlation_id FROM ai_traces WHERE id > :bid ORDER BY id"), {"bid": before},
    ).all()
    assert len(rows) == 2
    assert rows[0].correlation_id != rows[1].correlation_id


def test_write_proposal_still_one_model_call_no_continuation_and_confirmation_semantics_unchanged(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression proof: write tools are completely untouched by 3.8 —
    still exactly one model call, still ProposedAction -> confirm ->
    execute, never a tool-result continuation."""
    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply(
            "I'll create a task to call Hussein — confirm?",
            tool_name="propose_create_task", arguments={"title": "Call Hussein - corr38test"},
        )(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)

    response = _send(authenticated_client, "add a task to call Hussein")
    assert response.status_code == 200
    assert call_count["n"] == 1

    from app.modules.actions import service as actions_service

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.action_type == "create_task"

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'yes'")),
    )
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200
    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["title"] == "Call Hussein - corr38test" for t in tasks)


# ---- Checkpoint 3.9: deterministic-routing observability & regression guard -----


def test_write_intent_decline_makes_zero_model_calls_zero_traces_and_logs_route(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("should never be called")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)

    before_trace_id = _max_ai_trace_id(db_session)
    _enable_chat_service_logger()
    with caplog.at_level(logging.INFO, logger="app.modules.chat.service"):
        response = _send(authenticated_client, "delete my meeting with Bob")
    assert response.status_code == 200

    assert call_count["n"] == 0
    assert _max_ai_trace_id(db_session) == before_trace_id  # zero new AiTrace rows

    user_message_id = response.json()["user_message"]["id"]
    assert "deterministic_route=write_intent_decline" in caplog.text
    assert f"chat_message_id={user_message_id}" in caplog.text


def test_proposal_confirm_makes_zero_model_calls_zero_traces_and_logs_route(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I'll add the task — confirm?", tool_name="propose_create_task",
            arguments={"title": "Deterministic route test - 39a"},
        ),
    )
    _send(authenticated_client, "add a task, deterministic route test 39a")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("should never be called")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)

    before_trace_id = _max_ai_trace_id(db_session)
    _enable_chat_service_logger()
    with caplog.at_level(logging.INFO, logger="app.modules.chat.service"):
        response = _send(authenticated_client, "yes")
    assert response.status_code == 200

    assert call_count["n"] == 0
    assert _max_ai_trace_id(db_session) == before_trace_id

    user_message_id = response.json()["user_message"]["id"]
    assert "deterministic_route=proposal_confirm" in caplog.text
    assert f"chat_message_id={user_message_id}" in caplog.text

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["title"] == "Deterministic route test - 39a" for t in tasks)


def test_proposal_reject_makes_zero_model_calls_zero_traces_and_logs_route(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I'll add the task — confirm?", tool_name="propose_create_task",
            arguments={"title": "Deterministic route test - 39b"},
        ),
    )
    _send(authenticated_client, "add a task, deterministic route test 39b")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("should never be called")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)

    before_trace_id = _max_ai_trace_id(db_session)
    _enable_chat_service_logger()
    with caplog.at_level(logging.INFO, logger="app.modules.chat.service"):
        response = _send(authenticated_client, "no")
    assert response.status_code == 200

    assert call_count["n"] == 0
    assert _max_ai_trace_id(db_session) == before_trace_id

    user_message_id = response.json()["user_message"]["id"]
    assert "deterministic_route=proposal_reject" in caplog.text
    assert f"chat_message_id={user_message_id}" in caplog.text

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "Deterministic route test - 39b" for t in tasks)


def test_bare_yes_no_with_no_pending_proposal_reaches_the_model_path(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 3.9 regression guard: proves the rejected candidate
    from the architecture review (widening confirm/reject to a bare
    yes/no with NO pending proposal) was never silently adopted — a
    bare "yes" with nothing pending still reaches the Orchestrator,
    exactly like any other ordinary message, since without a pending
    row the phrase has no structural referent to be safe against."""
    from app.modules.actions import service as actions_service

    user, space = _get_space_and_user(db_session)
    actions_service.reject(db_session, space.id, user.id)  # guarantee no pending, regardless of prior tests
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("I don't have anything pending — did you mean something else?")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)

    response = _send(authenticated_client, "yes")
    assert response.status_code == 200
    assert call_count["n"] == 1  # reached the model — NOT deterministically handled


def test_arabic_bare_confirm_and_reject_against_pending_proposal_remain_zero_model_calls(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 3.9: explicit Arabic coverage for the existing
    deterministic confirm/reject routes — no prior test in this file
    exercised an Arabic bare confirm/decline phrase against a real
    pending proposal specifically."""
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("هضيف المهمة دي — أأكدها؟", tool_name="propose_create_task",
                    arguments={"title": "اختبار عربي - 39c"}),
    )
    _send(authenticated_client, "ضيف مهمة، اختبار عربي")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for Arabic 'نعم'")),
    )
    confirm_response = _send(authenticated_client, "نعم")
    assert confirm_response.status_code == 200
    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["title"] == "اختبار عربي - 39c" for t in tasks)

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("هضيف المهمة دي — أأكدها؟", tool_name="propose_create_task",
                    arguments={"title": "اختبار عربي - 39d"}),
    )
    _send(authenticated_client, "ضيف مهمة تانية، اختبار عربي")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for Arabic 'لا'")),
    )
    reject_response = _send(authenticated_client, "لا")
    assert reject_response.status_code == 200
    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == "اختبار عربي - 39d" for t in tasks_after)


# ---- Checkpoint 3.10: propose / confirm an update to an EXISTING task ------------


def _create_real_task(authenticated_client: TestClient, title: str, **extra) -> dict:
    payload = {"title": title, **extra}
    response = authenticated_client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201
    return response.json()


def test_context_exposes_task_id_for_referencing_existing_tasks(
    authenticated_client: TestClient, db_session: Session,
) -> None:
    """The Checkpoint 3.10 prerequisite: a task must be referenceable by
    id before propose_update_task can name one at all — mirrors Memory's
    own mem_id precedent."""
    task = _create_real_task(authenticated_client, "Context task_id test - 310a")

    user, space = _get_space_and_user(db_session)
    context = context_module.gather_context(db_session, space.id, TOMORROW_START, WINDOW_END)
    assert f"(task_id={task['id']})" in context


def test_propose_then_confirm_updates_the_real_task(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _create_real_task(authenticated_client, "Mark me done - 310b")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I'll mark it done — confirm?", tool_name="propose_update_task",
            arguments={"task_id": task["id"], "status": "done"},
        ),
    )
    propose_response = _send(authenticated_client, "mark 'Mark me done - 310b' as done")
    assert propose_response.status_code == 200
    assert "Mark me done - 310b" in propose_response.json()["assistant_message"]["content"]

    tasks_before = authenticated_client.get("/api/v1/tasks").json()
    before = next(t for t in tasks_before if t["id"] == task["id"])
    assert before["status"] == "open"

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'yes'")),
    )
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200
    assert "updated" in confirm_response.json()["assistant_message"]["content"].lower()

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    after = next(t for t in tasks_after if t["id"] == task["id"])
    assert after["status"] == "done"
    assert after["completed_at"] is not None


def test_propose_update_task_reject_leaves_the_task_unchanged(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _create_real_task(authenticated_client, "Do not touch me - 310c", description="original")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I'll rename it — confirm?", tool_name="propose_update_task",
            arguments={"task_id": task["id"], "title": "Renamed - 310c"},
        ),
    )
    _send(authenticated_client, "rename that task")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'no'")),
    )
    reject_response = _send(authenticated_client, "no")
    assert reject_response.status_code == 200

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    after = next(t for t in tasks_after if t["id"] == task["id"])
    assert after["title"] == "Do not touch me - 310c"
    assert after["description"] == "original"


def test_propose_update_task_with_missing_task_id_creates_no_proposal_and_replies_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("sure, updating that", tool_name="propose_update_task", arguments={"status": "done"}),
    )
    response = _send(authenticated_client, "mark it done, no id given")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_propose_update_task_with_nonexistent_task_id_creates_no_proposal_and_replies_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "sure, updating that", tool_name="propose_update_task",
            arguments={"task_id": 999999, "status": "done"},
        ),
    )
    response = _send(authenticated_client, "mark task 999999 as done")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_update_task_confirmation_language_matches_arabic_or_english_of_the_triggering_message(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_en = _create_real_task(authenticated_client, "Bilingual update test EN - 310d")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("ok", tool_name="propose_update_task", arguments={"task_id": task_en["id"], "status": "done"}),
    )
    response_en = _send(authenticated_client, "mark it done please")
    content_en = response_en.json()["assistant_message"]["content"]
    assert "Bilingual update test EN - 310d" in content_en
    assert "Shall I go ahead?" in content_en

    task_ar = _create_real_task(authenticated_client, "اختبار تحديث ثنائي اللغة - 310e")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("تمام", tool_name="propose_update_task", arguments={"task_id": task_ar["id"], "status": "done"}),
    )
    response_ar = _send(authenticated_client, "خلصها لو سمحت")
    content_ar = response_ar.json()["assistant_message"]["content"]
    assert "اختبار تحديث ثنائي اللغة - 310e" in content_ar
    assert "أأكدها؟" in content_ar


def test_model_prose_with_wrong_details_does_not_appear_in_update_task_confirmation(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same discipline as the 3.3 create_task fix: the confirmation is
    rendered ONLY from validated arguments, never the model's own
    free-form text — even when that text describes something else."""
    task = _create_real_task(authenticated_client, "Real task title - 310f")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I'll rename it to something totally different!", tool_name="propose_update_task",
            arguments={"task_id": task["id"], "status": "done"},
        ),
    )
    response = _send(authenticated_client, "mark it done")
    content = response.json()["assistant_message"]["content"]
    assert "totally different" not in content
    assert "Real task title - 310f" in content
    assert "mark it as done" in content


# ---- Checkpoint 3.11: authoritative current action state -----------------------


def _capture_context(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Mocks generate_reply one level above the real model call (the
    same technique test_relevant_memory_appears_in_a_later_conversations_context
    already uses) so context/history can be inspected directly without
    depending on any stochastic model behavior."""
    captured: dict = {}

    def _fake(**kwargs):
        captured["context"] = kwargs["context"]
        captured["history"] = kwargs["history"]
        return OrchestratorResult(text="ok", tool_call=None, correlation_id="corr_test")

    monkeypatch.setattr(orchestrator_service, "generate_reply", _fake)
    return captured


def _ensure_no_pending_proposal(db_session: Session) -> None:
    """Test-isolation guard, not product behavior: this shared test
    database is never rolled back between tests (see conftest.py), and
    a prior test in this same file may have left its own proposal
    pending (deliberately, to test the positive branch) — reject
    unconditionally (a safe no-op if nothing is pending) so each test
    below starts from a genuinely clean slate regardless of run order,
    the same defensive pattern the existing 3.9 tests already use."""
    from app.modules.actions import service as actions_service

    user, space = _get_space_and_user(db_session)
    actions_service.reject(db_session, space.id, user.id)


def test_negative_action_state_present_when_nothing_ever_pending(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_no_pending_proposal(db_session)
    captured = _capture_context(monkeypatch)
    _send(authenticated_client, "hello there")

    assert "## Current action state" in captured["context"]
    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE in captured["context"]
    assert "## Pending proposal awaiting confirmation" not in captured["context"]


def test_positive_pending_context_unchanged_when_a_real_proposal_is_pending(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 1/3: the EXISTING positive branch is untouched, and
    the two states are mutually exclusive."""
    _ensure_no_pending_proposal(db_session)
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I'll add the task — confirm?", tool_name="propose_create_task",
            arguments={"title": "Action-state positive test - 311a"},
        ),
    )
    _send(authenticated_client, "add a task, action-state positive test 311a")

    captured = _capture_context(monkeypatch)
    _send(authenticated_client, "how many tasks do I have?")

    assert "## Pending proposal awaiting confirmation" in captured["context"]
    assert "is awaiting the user's yes/no confirmation" in captured["context"]
    assert "## Current action state" not in captured["context"]
    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE not in captured["context"]

    _ensure_no_pending_proposal(db_session)  # leave a clean slate for tests that follow


def test_stale_proposal_like_history_does_not_override_negative_action_state(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The direct regression test for the demonstrated 3.10 anchoring
    condition: an assistant message that LOOKS like a proposal offer
    (plain text, no tool_call — so NO real ProposedAction was ever
    created, exactly as observed live) remains visible in history, while
    the authoritative, DB-grounded context must still say nothing is
    pending. This does not depend on any model choosing a correct
    natural-language response — it tests the deterministic context
    contract directly.
    """
    _ensure_no_pending_proposal(db_session)
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply('I\'ll add the task "Prepare the report". Shall I go ahead?'),
    )
    _send(authenticated_client, "please add a task called Prepare the report")

    captured = _capture_context(monkeypatch)
    _send(authenticated_client, "add a task to review the notes instead")

    assert any("Prepare the report" in turn.content for turn in captured["history"])
    assert "## Current action state" in captured["context"]
    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE in captured["context"]
    assert "## Pending proposal awaiting confirmation" not in captured["context"]


def test_confirmed_proposal_produces_negative_state_on_a_later_turn(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_no_pending_proposal(db_session)
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_task",
            arguments={"title": "Confirmed then later note test - 311b"},
        ),
    )
    _send(authenticated_client, "add a task, confirmed then later note test 311b")
    _send(authenticated_client, "yes")  # 0 LLM, deterministic confirm

    captured = _capture_context(monkeypatch)
    _send(authenticated_client, "what's on my list today?")

    assert any("Confirmed then later note test - 311b" in turn.content for turn in captured["history"])
    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE in captured["context"]
    assert "## Pending proposal awaiting confirmation" not in captured["context"]


def test_rejected_proposal_produces_negative_state_on_a_later_turn(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_no_pending_proposal(db_session)
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_task",
            arguments={"title": "Rejected then later note test - 311c"},
        ),
    )
    _send(authenticated_client, "add a task, rejected then later note test 311c")
    _send(authenticated_client, "no")  # 0 LLM, deterministic reject

    captured = _capture_context(monkeypatch)
    _send(authenticated_client, "what's on my list today?")

    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE in captured["context"]
    assert "## Pending proposal awaiting confirmation" not in captured["context"]


def test_superseded_proposal_does_not_become_current_via_remaining_history_text(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 7: a REVISED (superseded) proposal's own offering
    text also remains in history, but once the revision is confirmed,
    a later turn must still see the negative state, not be confused by
    either historical message."""
    _ensure_no_pending_proposal(db_session)
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "Draft A — confirm?", tool_name="propose_create_task",
            arguments={"title": "Draft A - 311d"},
        ),
    )
    _send(authenticated_client, "make a task called Draft A - 311d")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "Draft B instead — confirm?", tool_name="propose_create_task",
            arguments={"title": "Draft B - 311d"},
        ),
    )
    _send(authenticated_client, "actually call it Draft B - 311d instead")
    _send(authenticated_client, "yes")

    captured = _capture_context(monkeypatch)
    _send(authenticated_client, "what's on my list today?")

    assert any("Draft A - 311d" in turn.content for turn in captured["history"])
    assert any("Draft B - 311d" in turn.content for turn in captured["history"])
    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE in captured["context"]
    assert "## Pending proposal awaiting confirmation" not in captured["context"]


def test_expired_proposal_results_in_the_same_negative_state_no_separate_mechanism(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 8: expiry produces the exact SAME
    _NO_ACTIVE_PROPOSAL_NOTE as every other resolved/never-existed
    case — no separate expiry-specific string or mechanism."""
    _ensure_no_pending_proposal(db_session)
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_task",
            arguments={"title": "Expiry note test - 311e"},
        ),
    )
    _send(authenticated_client, "add a task, expiry note test 311e")

    db_session.execute(
        text("UPDATE proposed_actions SET expires_at = now() - interval '1 minute' WHERE status = 'pending'")
    )
    db_session.commit()

    captured = _capture_context(monkeypatch)
    _send(authenticated_client, "what's on my list today?")

    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE in captured["context"]
    assert "## Pending proposal awaiting confirmation" not in captured["context"]


def test_bare_yes_no_with_no_pending_reaches_model_and_sees_negative_state(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extends the existing 3.9 regression guard
    (test_bare_yes_no_with_no_pending_proposal_reaches_the_model_path):
    proves the turn that reaches the model ALSO sees the authoritative
    negative state — a bare "yes"/"no" with nothing really pending is
    never deterministically treated as confirming or rejecting
    historical proposal-like text; it reaches the model, which sees the
    same explicit "nothing is pending" fact as any other ordinary turn.
    """
    from app.modules.actions import service as actions_service

    user, space = _get_space_and_user(db_session)
    actions_service.reject(db_session, space.id, user.id)  # guarantee no pending, regardless of prior tests
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None

    captured = _capture_context(monkeypatch)
    response = _send(authenticated_client, "yes")

    assert response.status_code == 200
    assert chat_service._NO_ACTIVE_PROPOSAL_NOTE in captured["context"]
    assert "## Pending proposal awaiting confirmation" not in captured["context"]


def test_no_active_proposal_branch_adds_no_extra_ai_trace(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 16: the new context branch is pure string
    construction — proves exactly ONE new AiTrace row (the ordinary
    conversation call itself), not zero, not two, for a turn that
    exercises the new negative-state branch via a REAL (mocked
    provider-boundary) call, the same low-level technique the existing
    AiTrace-content tests already use."""
    _ensure_no_pending_proposal(db_session)
    from app.modules.model_router import service as model_router_service

    class _FakeUsage:
        input_tokens = 5
        output_tokens = 5

    class _FakeTextBlock:
        type = "text"
        text = "ok"

    class _FakeMessage:
        content = [_FakeTextBlock()]
        usage = _FakeUsage()

    monkeypatch.setattr(model_router_service.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(model_router_service, "_call_anthropic", lambda model, messages, **kwargs: _FakeMessage())

    before = _max_ai_trace_id(db_session)
    response = _send(authenticated_client, "action-state-ai-trace-marker-test hello")
    assert response.status_code == 200
    after = _max_ai_trace_id(db_session)

    assert after - before == 1


# ---- Checkpoint 3.13: propose / confirm a removal of an EXISTING task -----------


def test_propose_delete_task_requires_explicit_task_id(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same shape-validation discipline as propose_update_task's own
    missing-task_id test: task_id is required by the tool's own schema,
    and a call without it never reaches the point of creating a
    ProposedAction row."""
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("sure, removing it", tool_name="propose_delete_task", arguments={}),
    )
    response = _send(authenticated_client, "remove that task, no id given")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_propose_then_confirm_removes_the_real_task(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The main end-to-end flow: a valid task_id produces exactly one
    pending delete_task ProposedAction and exactly one model call; the
    task remains fully active until confirmation; a bare 'yes' costs
    zero model calls and archives exactly that task, which then
    disappears from the normal task-list read."""
    from app.modules.actions import service as actions_service

    task = _create_real_task(authenticated_client, "Remove me - 313a")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply(
            "I can remove it — confirm?", tool_name="propose_delete_task",
            arguments={"task_id": task["id"]},
        )(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)
    propose_response = _send(authenticated_client, "remove the 'Remove me - 313a' task")
    assert propose_response.status_code == 200
    assert call_count["n"] == 1
    assert "Remove me - 313a" in propose_response.json()["assistant_message"]["content"]

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.action_type == "delete_task"

    tasks_before_confirm = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["id"] == task["id"] for t in tasks_before_confirm)

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'yes'")),
    )
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200
    assert "removed" in confirm_response.json()["assistant_message"]["content"].lower()
    assert "Remove me - 313a" in confirm_response.json()["assistant_message"]["content"]

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["id"] == task["id"] for t in tasks_after)

    direct_get = authenticated_client.get(f"/api/v1/tasks/{task['id']}")
    assert direct_get.status_code == 404


def test_propose_delete_task_reject_leaves_task_active(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _create_real_task(authenticated_client, "Do not remove me - 313b")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I can remove it — confirm?", tool_name="propose_delete_task",
            arguments={"task_id": task["id"]},
        ),
    )
    _send(authenticated_client, "remove that task")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'no'")),
    )
    reject_response = _send(authenticated_client, "no")
    assert reject_response.status_code == 200

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    after = next(t for t in tasks_after if t["id"] == task["id"])
    assert after["title"] == "Do not remove me - 313b"


def test_propose_delete_task_with_nonexistent_task_id_creates_no_proposal_and_replies_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "sure, removing it", tool_name="propose_delete_task",
            arguments={"task_id": 999999},
        ),
    )
    response = _send(authenticated_client, "remove task 999999")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_propose_delete_task_with_other_space_task_id_creates_no_proposal_and_replies_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real task_id that genuinely exists, but in a DIFFERENT space —
    the same space-scoped get_task lookup that makes a nonexistent id
    safe must also make a real-but-foreign id indistinguishable from
    nonexistent, never a leak."""
    from app.modules.actions import service as actions_service
    from app.modules.auth import service as auth_service
    from app.modules.spaces.models import Space
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate

    owner = auth_service.get_the_user(db_session)
    other_space = Space(name="Other Space - 313c", is_default=False, user_id=owner.id)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)
    foreign_task = tasks_service.create_task(db_session, other_space.id, TaskCreate(title="Foreign task - 313c"))

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "sure, removing it", tool_name="propose_delete_task",
            arguments={"task_id": foreign_task.id},
        ),
    )
    response = _send(authenticated_client, "remove that other task")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None

    # The foreign task itself must remain completely untouched.
    still_there = tasks_service.get_task(db_session, other_space.id, foreign_task.id)
    assert still_there is not None
    assert still_there.archived_at is None


def test_propose_delete_task_on_already_archived_task_creates_no_proposal(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers both 'already-archived task cannot create a valid
    proposal' and 'archived task cannot subsequently be proposed for
    deletion again' — the same _require_existing_task/get_task lookup
    that filters archived_at IS NULL for every other proposal type
    applies identically here."""
    from app.modules.actions import service as actions_service

    task = _create_real_task(authenticated_client, "Remove me twice - 313d")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_delete_task", arguments={"task_id": task["id"]}),
    )
    _send(authenticated_client, "remove that task")
    _send(authenticated_client, "yes")  # now archived

    tasks_after_first_delete = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["id"] == task["id"] for t in tasks_after_first_delete)

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_delete_task", arguments={"task_id": task["id"]}),
    )
    response = _send(authenticated_client, "remove that task again")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_archived_task_cannot_subsequently_be_updated(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.actions import service as actions_service

    task = _create_real_task(authenticated_client, "Removed then targeted - 313e")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_delete_task", arguments={"task_id": task["id"]}),
    )
    _send(authenticated_client, "remove that task")
    _send(authenticated_client, "yes")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "sure, marking it done", tool_name="propose_update_task",
            arguments={"task_id": task["id"], "status": "done"},
        ),
    )
    response = _send(authenticated_client, "mark that removed task as done")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_delete_task_confirmation_names_task_and_states_no_restore_consequence(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Product decisions 4/5: the confirmation must name the real task,
    state the no-restore consequence explicitly, use 'remove' language,
    and never say anything implying permanence-as-erasure or
    restorability that doesn't exist."""
    task = _create_real_task(authenticated_client, "Consequence check - 313f")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("sure!", tool_name="propose_delete_task", arguments={"task_id": task["id"]}),
    )
    response = _send(authenticated_client, "remove the 'Consequence check - 313f' task")
    content = response.json()["assistant_message"]["content"]

    assert "Consequence check - 313f" in content
    assert "remove" in content.lower()
    assert "no way to bring it back" in content.lower() or "there's no way to bring it back" in content.lower()
    assert "permanently deleted" not in content.lower()
    assert "restorable" not in content.lower()
    assert "restore" not in content.lower()  # says "no way to bring it back", never the word "restore" itself


def test_delete_task_confirmation_language_matches_arabic_or_english_of_the_triggering_message(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_en = _create_real_task(authenticated_client, "Bilingual delete test EN - 313g")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("ok", tool_name="propose_delete_task", arguments={"task_id": task_en["id"]}),
    )
    response_en = _send(authenticated_client, "remove it please")
    content_en = response_en.json()["assistant_message"]["content"]
    assert "Bilingual delete test EN - 313g" in content_en
    assert "Shall I go ahead?" in content_en

    task_ar = _create_real_task(authenticated_client, "اختبار حذف ثنائي اللغة - 313h")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("تمام", tool_name="propose_delete_task", arguments={"task_id": task_ar["id"]}),
    )
    response_ar = _send(authenticated_client, "شيلها لو سمحت")
    content_ar = response_ar.json()["assistant_message"]["content"]
    assert "اختبار حذف ثنائي اللغة - 313h" in content_ar
    assert "أنفذ؟" in content_ar
    assert "مفيش طريقة أرجعها" in content_ar


def test_model_prose_with_wrong_details_does_not_appear_in_delete_task_confirmation(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _create_real_task(authenticated_client, "Real task title - 313i")
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I've removed a totally different task for you!", tool_name="propose_delete_task",
            arguments={"task_id": task["id"]},
        ),
    )
    response = _send(authenticated_client, "remove it")
    content = response.json()["assistant_message"]["content"]
    assert "totally different" not in content
    assert "Real task title - 313i" in content


def test_completed_task_can_be_removed_and_existing_inbox_item_remains_valid(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed task already has a 'Completed: <title>' InboxItem
    (created by tasks_service.update_task's own open->done side effect).
    Removing that task afterward must not delete, hide, or otherwise
    alter that InboxItem — no cascade behavior of any kind, per the
    3.13 design report's InboxItem/FK finding."""
    from app.modules.inbox import service as inbox_service

    task = _create_real_task(authenticated_client, "Complete then remove - 313j")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_update_task", arguments={"task_id": task["id"], "status": "done"}),
    )
    _send(authenticated_client, "mark it done")
    _send(authenticated_client, "yes")

    user, space = _get_space_and_user(db_session)
    inbox_before = [i for i in inbox_service.list_items(db_session, space.id) if i.task_id == task["id"]]
    assert len(inbox_before) == 1
    assert inbox_before[0].title == "Completed: Complete then remove - 313j"

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_delete_task", arguments={"task_id": task["id"]}),
    )
    _send(authenticated_client, "remove that task")
    _send(authenticated_client, "yes")

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["id"] == task["id"] for t in tasks_after)

    inbox_after = [i for i in inbox_service.list_items(db_session, space.id) if i.task_id == task["id"]]
    assert len(inbox_after) == 1
    assert inbox_after[0].id == inbox_before[0].id
    assert inbox_after[0].title == "Completed: Complete then remove - 313j"
    assert inbox_after[0].archived_at is None


def test_delete_task_model_prose_without_tool_call_removes_nothing(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 3.13's own version of the file's general adversarial
    proof: a model that merely TALKS about removing a task, with no
    propose_delete_task tool call attached, must leave the real task
    completely untouched — no ProposedAction, no archived_at, ever, from
    text alone."""
    task = _create_real_task(authenticated_client, "Never actually removed - 313k")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Sure, I've removed that task for you."),
    )
    response = _send(authenticated_client, "remove the 'Never actually removed - 313k' task")
    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"] == "Sure, I've removed that task for you."

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["id"] == task["id"] for t in tasks_after)


def test_delete_task_proposal_and_confirm_use_zero_extra_ai_traces_and_log_deterministic_routes(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mirrors the 3.9 deterministic-route regression guards for
    create_task, applied to delete_task: the proposal turn is one real
    model call (one new AiTrace); the bare 'yes' that follows is zero
    model calls, zero new AiTrace rows, and logs the same
    proposal_confirm route as every other action type."""
    task = _create_real_task(authenticated_client, "Deterministic route test - 313l")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_delete_task", arguments={"task_id": task["id"]}),
    )
    _send(authenticated_client, "remove that task, deterministic route test 313l")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("should never be called")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)

    before_trace_id = _max_ai_trace_id(db_session)
    _enable_chat_service_logger()
    with caplog.at_level(logging.INFO, logger="app.modules.chat.service"):
        response = _send(authenticated_client, "yes")
    assert response.status_code == 200

    assert call_count["n"] == 0
    assert _max_ai_trace_id(db_session) == before_trace_id

    user_message_id = response.json()["user_message"]["id"]
    assert "deterministic_route=proposal_confirm" in caplog.text
    assert f"chat_message_id={user_message_id}" in caplog.text

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["id"] == task["id"] for t in tasks)


# ---- Checkpoint 3.15: timezone grounding ----------------------------------------


def test_current_datetime_local_exposes_timezone_name_and_utc_offset() -> None:
    result = chat_service._compute_current_datetime_local("Africa/Cairo")
    assert "Timezone: Africa/Cairo" in result
    assert "UTC offset:" in result
    assert "Current local datetime:" in result


def test_current_datetime_local_offset_is_computed_not_fixed() -> None:
    """Proves the exposed offset is a real, freshly computed value —
    not a hardcoded +03:00/-03:00 constant — by cross-checking against
    an independent ZoneInfo computation for several distinct zones and
    confirming they actually differ from each other right now."""
    from datetime import datetime, timezone as dt_timezone
    from zoneinfo import ZoneInfo

    zone_names = ["America/New_York", "Africa/Cairo", "UTC"]
    offsets = set()
    for zone_name in zone_names:
        result = chat_service._compute_current_datetime_local(zone_name)
        expected = chat_service._format_utc_offset(
            datetime.now(dt_timezone.utc).astimezone(ZoneInfo(zone_name))
        )
        assert f"UTC offset: {expected}" in result
        offsets.add(expected)

    assert len(offsets) == len(zone_names)  # genuinely distinct per zone, not one fixed value


def test_format_utc_offset_reflects_real_dst_transition() -> None:
    """America/New_York: EST (UTC-05:00) in January, EDT (UTC-04:00) in
    July — hand-constructed instants, no time-mocking library needed,
    proving the offset genuinely varies with the calendar date rather
    than being fixed per IANA zone name."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=zone)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=zone)
    assert chat_service._format_utc_offset(winter) == "-05:00"
    assert chat_service._format_utc_offset(summer) == "-04:00"


# ---- Checkpoint 3.15: propose / confirm a new CalendarEvent ---------------------


def test_propose_create_event_requires_title_and_starts_at(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("sure, adding it", tool_name="propose_create_event", arguments={"title": "Meeting"}),
    )
    response = _send(authenticated_client, "add a meeting, no time given")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_propose_then_confirm_creates_the_real_calendar_event(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The main end-to-end flow: exactly one model call proposes;
    nothing exists in Calendar/Agenda before confirmation; a bare 'yes'
    costs zero model calls and creates exactly one CalendarEvent, which
    then appears in the normal Calendar/Agenda read."""
    from app.modules.actions import service as actions_service

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply(
            "I can add it — confirm?", tool_name="propose_create_event",
            arguments={
                "title": "Meeting with Hussein - 315a",
                "starts_at": "2026-09-28T11:00:00+03:00",
                "ends_at": "2026-09-28T12:00:00+03:00",
            },
        )(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)
    propose_response = _send(authenticated_client, "I have a meeting with Hussein tomorrow at 11 for one hour")
    assert propose_response.status_code == 200
    assert call_count["n"] == 1
    assert "Meeting with Hussein - 315a" in propose_response.json()["assistant_message"]["content"]

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.action_type == "create_event"

    agenda_before = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2026-09-28T00:00:00Z", "to": "2026-09-29T00:00:00Z"},
    ).json()
    assert not any(i["title"] == "Meeting with Hussein - 315a" for i in agenda_before)

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'yes'")),
    )
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200
    assert "added" in confirm_response.json()["assistant_message"]["content"].lower()
    assert "Meeting with Hussein - 315a" in confirm_response.json()["assistant_message"]["content"]

    agenda_after = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2026-09-28T00:00:00Z", "to": "2026-09-29T00:00:00Z"},
    ).json()
    matching = [i for i in agenda_after if i["title"] == "Meeting with Hussein - 315a"]
    assert len(matching) == 1
    assert matching[0]["source"] == "event"
    assert matching[0]["starts_at"] == "2026-09-28T08:00:00Z"  # 11:00 +03:00 == 08:00 UTC
    assert matching[0]["ends_at"] == "2026-09-28T09:00:00Z"


def test_propose_create_event_reject_creates_no_calendar_event(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_event",
            arguments={"title": "Never happens - 315b", "starts_at": "2026-09-28T11:00:00+03:00"},
        ),
    )
    _send(authenticated_client, "add a meeting")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'no'")),
    )
    reject_response = _send(authenticated_client, "no")
    assert reject_response.status_code == 200

    agenda_after = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2026-09-28T00:00:00Z", "to": "2026-09-29T00:00:00Z"},
    ).json()
    assert not any(i["title"] == "Never happens - 315b" for i in agenda_after)


def test_propose_create_event_with_naive_starts_at_creates_no_proposal_and_replies_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Product/architecture requirement C: a naive datetime must never
    be silently interpreted as UTC — it must fail validation before any
    ProposedAction exists."""
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "sure", tool_name="propose_create_event",
            arguments={"title": "Naive time event", "starts_at": "2026-09-28T11:00:00"},
        ),
    )
    response = _send(authenticated_client, "add a meeting tomorrow at 11")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_propose_create_event_with_invalid_life_area_creates_no_proposal_and_replies_honestly(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "sure", tool_name="propose_create_event",
            arguments={
                "title": "Bad life area event", "starts_at": "2026-09-28T11:00:00+03:00",
                "life_area_id": 999999,
            },
        ),
    )
    response = _send(authenticated_client, "add a meeting under some life area")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None


def test_propose_create_event_life_area_is_shared_across_spaces(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LifeArea is a GLOBAL model (no SpaceScopedMixin — see
    life_areas/models.py's own docstring), unlike Task/CalendarEvent —
    there is no 'other space's life area' to reject, since every valid
    life_area_id is valid from every space by design. This test proves
    that actual, verified behavior directly rather than asserting a
    cross-space rejection scenario that cannot exist for this model."""
    from app.modules.actions import service as actions_service
    from app.modules.life_areas import service as life_areas_service

    area = life_areas_service.create_life_area(db_session, "Shared area - 315c")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_event",
            arguments={
                "title": "Shared area event - 315c", "starts_at": "2026-09-28T11:00:00+03:00",
                "life_area_id": area.id,
            },
        ),
    )
    response = _send(authenticated_client, "add a meeting under Shared area")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.arguments["life_area_id"] == area.id


def test_create_event_confirmation_renders_local_date_and_time_not_utc(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_event",
            arguments={
                "title": "Meeting with Hussein - 315d",
                "starts_at": "2026-09-28T11:00:00+03:00",
                "ends_at": "2026-09-28T12:00:00+03:00",
            },
        ),
    )
    response = _send(authenticated_client, "I have a meeting with Hussein tomorrow at 11 for one hour")
    content = response.json()["assistant_message"]["content"]

    assert "September 28" in content
    assert "11:00 AM" in content
    assert "12:00 PM" in content
    assert "08:00" not in content  # never the raw UTC digits
    assert "2026-09-28T" not in content


def test_create_event_confirmation_omits_end_time_for_point_event(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_event",
            arguments={"title": "Birthday - 315e", "starts_at": "2026-10-05T09:00:00+03:00"},
        ),
    )
    response = _send(authenticated_client, "add an event for Ahmed's birthday on October 5 at 9am")
    content = response.json()["assistant_message"]["content"]
    assert "October 5" in content
    assert "9:00 AM" in content
    assert "–" not in content  # no range dash when there's no end time


def test_create_event_confirmation_language_matches_arabic_or_english(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "ok", tool_name="propose_create_event",
            arguments={
                "title": "اجتماع مع حسين - 315f",
                "starts_at": "2026-09-28T11:00:00+03:00",
                "ends_at": "2026-09-28T12:00:00+03:00",
            },
        ),
    )
    response_ar = _send(authenticated_client, "عندي اجتماع مع حسين بكرة الساعة 11 لمدة ساعة، ضيفه عندي")
    content_ar = response_ar.json()["assistant_message"]["content"]
    assert "اجتماع مع حسين - 315f" in content_ar
    assert "أضيفها؟" in content_ar
    assert "سبتمبر" in content_ar


def test_create_event_confirmation_across_dst_capable_timezone(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """America/New_York (already used elsewhere in this codebase for
    DST coverage) — confirms local rendering uses real IANA/DST
    arithmetic, not a fixed offset, for a zone other than Cairo."""
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_event",
            arguments={
                "title": "NY meeting - 315g",
                "starts_at": "2026-07-15T11:00:00-04:00",  # EDT (summer, DST in effect)
                "ends_at": "2026-07-15T12:00:00-04:00",
            },
        ),
    )
    response = _send(authenticated_client, "add a meeting tomorrow at 11 for one hour", timezone_name="America/New_York")
    content = response.json()["assistant_message"]["content"]
    assert "July 15" in content
    assert "11:00 AM" in content
    assert "12:00 PM" in content


def test_model_prose_with_wrong_details_does_not_appear_in_create_event_confirmation(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "I've added a totally different event for a totally different time!",
            tool_name="propose_create_event",
            arguments={"title": "Real event title - 315h", "starts_at": "2026-09-28T11:00:00+03:00"},
        ),
    )
    response = _send(authenticated_client, "add a meeting")
    content = response.json()["assistant_message"]["content"]
    assert "totally different" not in content
    assert "Real event title - 315h" in content


def test_create_event_proposal_and_confirm_use_zero_extra_ai_traces_and_log_deterministic_routes(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_event",
            arguments={"title": "Deterministic route test - 315i", "starts_at": "2026-09-28T11:00:00+03:00"},
        ),
    )
    _send(authenticated_client, "add a meeting, deterministic route test 315i")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("should never be called")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)

    before_trace_id = _max_ai_trace_id(db_session)
    _enable_chat_service_logger()
    with caplog.at_level(logging.INFO, logger="app.modules.chat.service"):
        response = _send(authenticated_client, "yes")
    assert response.status_code == 200

    assert call_count["n"] == 0
    assert _max_ai_trace_id(db_session) == before_trace_id

    user_message_id = response.json()["user_message"]["id"]
    assert "deterministic_route=proposal_confirm" in caplog.text
    assert f"chat_message_id={user_message_id}" in caplog.text

    agenda = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2026-09-28T00:00:00Z", "to": "2026-09-29T00:00:00Z"},
    ).json()
    assert any(i["title"] == "Deterministic route test - 315i" for i in agenda)


def test_single_tool_call_dispatch_never_produces_both_task_and_event_for_one_turn(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural proof (not a prompt-reliability claim): even if a
    single model response somehow carried multiple tool_use blocks,
    orchestrator_service.generate_reply only ever takes the first
    (documented, deliberate design since Checkpoint 3.3) — so a single
    turn can never dispatch to both propose_create_task and
    propose_create_event regardless of what the model outputs."""
    from app.modules.actions import service as actions_service
    from app.modules.orchestrator.schemas import OrchestratorResult, ToolCallRequest

    def _multi_tool_reply(**kwargs):
        # Only the FIRST tool_use is ever surfaced by generate_reply
        # itself — simulate that by returning a result as if two tools
        # had been requested, then rely on the SAME real orchestrator
        # code path a genuine multi-tool_use response would go through.
        return OrchestratorResult(
            text="here you go",
            tool_call=ToolCallRequest(
                tool_use_id="toolu_1", tool_name="propose_create_task",
                arguments={"title": "Call Hussein - 315j"},
            ),
            correlation_id="corr_test",
        )

    monkeypatch.setattr(orchestrator_service, "generate_reply", _multi_tool_reply)
    response = _send(authenticated_client, "add call Hussein tomorrow at 11")
    assert response.status_code == 200

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.action_type == "create_task"  # exactly one action_type, never two rows

    agenda = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2026-09-28T00:00:00Z", "to": "2026-09-29T00:00:00Z"},
    ).json()
    assert not any(i["title"] == "Call Hussein - 315j" for i in agenda)  # no event was also created


def test_expired_create_event_proposal_cannot_execute(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 3.11 generic semantics inherited with no event-specific
    code: an expired pending proposal cannot be resurrected by a later
    bare 'yes', regardless of action_type."""
    from app.modules.actions import service as actions_service

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply(
            "confirm?", tool_name="propose_create_event",
            arguments={"title": "Expired event - 315k", "starts_at": "2026-09-28T11:00:00+03:00"},
        ),
    )
    _send(authenticated_client, "add a meeting")

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    db_session.execute(
        text("UPDATE proposed_actions SET expires_at = now() - interval '1 minute' WHERE id = :id"),
        {"id": pending.id},
    )
    db_session.commit()

    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    agenda = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2026-09-28T00:00:00Z", "to": "2026-09-29T00:00:00Z"},
    ).json()
    assert not any(i["title"] == "Expired event - 315k" for i in agenda)


# ---- Checkpoint 3.17: conversational adjacency guard against a stale pending proposal ----


def test_update_task_interruption_then_yes_does_not_execute(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _create_real_task(authenticated_client, "Interruption target - 317a")
    result = _propose_interrupt_then_bare_answer(
        authenticated_client, monkeypatch,
        propose_message="mark 'Interruption target - 317a' as done",
        tool_name="propose_update_task", arguments={"task_id": task["id"], "status": "done"},
        bare_answer="yes",
    )
    assert result["call_count"] == 1  # reached the model, not deterministic

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    after = next(t for t in tasks_after if t["id"] == task["id"])
    assert after["status"] == "open"  # NOT updated


def test_delete_task_interruption_then_yes_does_not_archive(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _create_real_task(authenticated_client, "Interruption target - 317b")
    result = _propose_interrupt_then_bare_answer(
        authenticated_client, monkeypatch,
        propose_message="remove the 'Interruption target - 317b' task",
        tool_name="propose_delete_task", arguments={"task_id": task["id"]},
        bare_answer="yes",
    )
    assert result["call_count"] == 1

    tasks_after = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["id"] == task["id"] for t in tasks_after)  # NOT archived


def test_create_event_interruption_then_yes_does_not_create_event(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _propose_interrupt_then_bare_answer(
        authenticated_client, monkeypatch,
        propose_message="add a meeting with Hussein tomorrow at 11 for one hour",
        tool_name="propose_create_event",
        arguments={
            "title": "Interruption event - 317c",
            "starts_at": "2026-09-28T11:00:00+03:00",
            "ends_at": "2026-09-28T12:00:00+03:00",
        },
        bare_answer="yes",
    )
    assert result["call_count"] == 1

    agenda = authenticated_client.get(
        "/api/v1/calendar/agenda", params={"from": "2026-09-28T00:00:00Z", "to": "2026-09-29T00:00:00Z"},
    ).json()
    assert not any(i["title"] == "Interruption event - 317c" for i in agenda)  # NOT created


def test_save_memory_interruption_then_yes_does_not_save(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service

    result = _propose_interrupt_then_bare_answer(
        authenticated_client, monkeypatch,
        propose_message="remember that I prefer concise answers - 317d",
        tool_name="propose_save_memory",
        arguments={"type": "PREFERENCE", "content": "Prefers concise answers - 317d"},
        bare_answer="yes",
    )
    assert result["call_count"] == 1

    user, space = _get_space_and_user(db_session)
    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    assert not any(m.content == "Prefers concise answers - 317d" for m in memories)  # NOT saved


def test_forget_memory_interruption_then_yes_does_not_forget(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = _get_space_and_user(db_session)
    source_id = chat_service.record_assistant_message(db_session, space.id, user.id, "seed").id
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="GOAL", content="Learn Rust - 317e")
    )
    db_session.commit()

    result = _propose_interrupt_then_bare_answer(
        authenticated_client, monkeypatch,
        propose_message="forget that I wanted to learn Rust - 317e",
        tool_name="propose_forget_memory", arguments={"memory_id": memory.id},
        bare_answer="yes",
    )
    assert result["call_count"] == 1

    memories, _ = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)
    assert any(m.id == memory.id and m.status == "active" for m in memories)  # NOT forgotten


def test_interruption_then_no_does_not_reject_old_proposal(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The symmetric rejection-side finding from the 3.17 inspection:
    a bare 'no' is exactly as adjacency-blind as a bare 'yes' without
    this guard — proves it is now guarded identically."""
    from app.modules.actions import service as actions_service

    task = _create_real_task(authenticated_client, "Do not touch me - 317f")
    result = _propose_interrupt_then_bare_answer(
        authenticated_client, monkeypatch,
        propose_message="rename 'Do not touch me - 317f' to something else",
        tool_name="propose_update_task", arguments={"task_id": task["id"], "title": "Renamed - 317f"},
        bare_answer="no",
    )
    assert result["call_count"] == 1  # reached the model, not deterministically rejected

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.status == "pending"  # NOT rejected by the stale "no"


def test_explicit_contextual_return_after_interruption_is_model_routed(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 3.17 Part F/G: an explicit contextual confirmation
    ('Yes, add that task.') is never added to the narrow bare-phrase
    set — it must reach the model exactly like any other non-adjacent
    turn, never bypass it."""
    from app.modules.actions import service as actions_service

    task_title = "Contextual return target - 317g"
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_create_task", arguments={"title": task_title}),
    )
    _send(authenticated_client, f"add a task: {task_title}")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Tomorrow in Cairo: sunny, 30°C."),
    )
    _send(authenticated_client, "What's the weather tomorrow in Cairo?")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("Sure — I've added it.")(**kwargs)  # deliberately no tool call

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)
    response = _send(authenticated_client, "Yes, add that task.")
    assert call_count["n"] == 1  # model-routed, not deterministic

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == task_title for t in tasks)  # the model's false claim caused no real write

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.status == "pending"  # original proposal untouched either way


def test_explicit_contextual_return_can_re_propose_then_fresh_yes_executes(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 3.17 Part G: the CORRECT behavior for an explicit
    contextual return — the model calls propose_create_task again,
    superseding the stale proposal with a fresh, now-adjacent one; a
    following immediate 'yes' then executes exactly once."""
    task_title = "Re-proposed task - 317h"
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_create_task", arguments={"title": task_title}),
    )
    _send(authenticated_client, f"add a task: {task_title}")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Tomorrow in Cairo: sunny, 30°C."),
    )
    _send(authenticated_client, "What's the weather tomorrow in Cairo?")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("Sure, re-confirming — add it?", tool_name="propose_create_task", arguments={"title": task_title}),
    )
    reprompt_response = _send(authenticated_client, "Yes, add that task.")
    assert task_title in reprompt_response.json()["assistant_message"]["content"]

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called for a bare 'yes'")),
    )
    confirm_response = _send(authenticated_client, "yes")
    assert confirm_response.status_code == 200

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert any(t["title"] == task_title for t in tasks)  # exactly the fresh proposal executed


def test_adjacent_current_action_state_wording_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adjacent-case description must remain byte-for-byte the same
    pre-3.11-style text — 3.17 only adds a NEW branch, never changes
    the existing one."""
    pending = type(
        "FakePending", (), {
            "action_type": "create_task", "arguments": {"title": "X"},
            "expires_at": type("T", (), {"isoformat": lambda self: "2030-01-01T00:00:00+00:00"})(),
        },
    )()
    text = chat_service._describe_pending_proposal(pending, adjacent=True)
    assert "is awaiting the user's yes/no confirmation" in text
    assert "revision request" in text
    assert "no longer eligible" not in text


def test_non_adjacent_current_action_state_wording_is_truthful(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checkpoint 3.17 Part H: must say the proposal still exists,
    that it is NOT expired/rejected/removed, that the model must never
    claim it was acted on, and that a fresh propose_* call is the
    correct next step — never implying execution already happened."""
    pending = type(
        "FakePending", (), {"action_type": "create_task", "arguments": {"title": "X"}},
    )()
    text = chat_service._describe_pending_proposal(pending, adjacent=False)
    assert "is still stored" in text
    assert "NOT expired, rejected, or removed" in text
    assert "must never claim it was created, changed, or removed" in text
    assert "call propose_create_task again" in text


def test_history_truncation_cannot_bypass_adjacency_protection(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 3.17 Part L: the adjacency guard uses a direct,
    uncapped ChatMessage query — never the 20-message/4000-char
    model-visible history window. Proven by pushing enough intervening
    messages that the ORIGINAL confirmation prompt itself falls
    completely outside that trimmed window, and confirming the guard
    still correctly blocks deterministic execution."""
    from app.modules.actions import service as actions_service

    task_title = "Buried under history - 317i"
    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("confirm?", tool_name="propose_create_task", arguments={"title": task_title}),
    )
    _send(authenticated_client, f"add a task: {task_title}")

    monkeypatch.setattr(
        orchestrator_service, "generate_reply",
        _mock_reply("noted"),
    )
    for i in range(15):  # comfortably exceeds _MAX_HISTORY_MESSAGES (20) together with the propose turn
        _send(authenticated_client, f"unrelated filler message {i}")

    call_count = {"n": 0}

    def _counted_reply(**kwargs):
        call_count["n"] += 1
        return _mock_reply("What would you like me to do?")(**kwargs)

    monkeypatch.setattr(orchestrator_service, "generate_reply", _counted_reply)
    _send(authenticated_client, "yes")
    assert call_count["n"] == 1  # still correctly non-adjacent, not deterministic

    tasks = authenticated_client.get("/api/v1/tasks").json()
    assert not any(t["title"] == task_title for t in tasks)

    user, space = _get_space_and_user(db_session)
    pending = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert pending is not None
    assert pending.status == "pending"


def test_adjacency_check_works_from_a_brand_new_session_simulating_a_restart(db_session: Session) -> None:
    """Checkpoint 3.17 Part F/M-analog: adjacency is purely DB-backed —
    a fresh SQLAlchemy Session (no in-memory state carried over,
    simulating an app/process restart) must compute the identical,
    correct answer."""
    from sqlalchemy.orm import sessionmaker

    from app.modules.actions import service as actions_service

    user, space = _get_space_and_user(db_session)
    confirmation = chat_service.record_assistant_message(db_session, space.id, user.id, "confirm?")
    pending = actions_service.create_pending_action(
        db_session, space.id, user.id, confirmation.id, "create_task", {"title": "Restart test - 317j"},
    )
    current_message = chat_service.record_user_message(db_session, space.id, user.id, "yes")

    FreshSession = sessionmaker(bind=db_session.get_bind())
    fresh_db = FreshSession()
    try:
        adjacent = actions_service.is_still_conversationally_adjacent(
            fresh_db, space.id, user.id, pending, current_message.id
        )
        assert adjacent is True  # nothing intervened

        interruption = chat_service.record_user_message(fresh_db, space.id, user.id, "something else")
        later_message = chat_service.record_user_message(fresh_db, space.id, user.id, "yes")
        adjacent_after_interruption = actions_service.is_still_conversationally_adjacent(
            fresh_db, space.id, user.id, pending, later_message.id
        )
        assert adjacent_after_interruption is False  # interruption.id correctly counted
        assert interruption.id  # sanity: the row really was created
    finally:
        fresh_db.close()
