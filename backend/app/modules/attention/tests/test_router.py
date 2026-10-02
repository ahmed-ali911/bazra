"""Checkpoint 4.5e — the POST /attention/app-opened HTTP contract.
Deliberately isolated from the full decide/narrate/revalidate/persist
integration (already covered end-to-end by test_surfacing.py) by
monkeypatching surfacing.evaluate_and_surface_app_opened itself — this
file only proves the ENDPOINT's own contract: auth, request/response
shape, and that the client cannot inject anything beyond `timezone`.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.attention import router as attention_router_module
from app.modules.attention import surfacing
from app.modules.attention.schemas import InvalidTimezoneError
from app.modules.chat.models import ChatMessage

_CAIRO = "Africa/Cairo"


def _silence_result():
    return surfacing.AppOpenedSurfaceResult(status="silence", reason="no_eligible_candidate")


def _surfaced_result(db_session: Session, space_id: int, user_id: int):
    message = ChatMessage(space_id=space_id, user_id=user_id, role="assistant", content="عندك مهمة متأخرة.")
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return surfacing.AppOpenedSurfaceResult(status="surfaced", message=message)


def test_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/v1/attention/app-opened", json={"timezone": _CAIRO})
    assert response.status_code == 401


def test_silence_response_shape(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attention_router_module.surfacing, "evaluate_and_surface_app_opened", lambda *a, **k: _silence_result())

    response = authenticated_client.post("/api/v1/attention/app-opened", json={"timezone": _CAIRO})

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "silence", "message": None}
    # Internal policy reasoning never reaches the client.
    assert "reason" not in body
    assert "score" not in body
    assert "fallback_reason" not in body


def test_surfaced_response_shape(
    authenticated_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.auth import service as auth_service
    from app.modules.spaces import service as spaces_service

    user = auth_service.get_the_user(db_session)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)

    def _fake(db, space_id, user_id, timezone_name):
        return _surfaced_result(db, space_id, user_id)

    monkeypatch.setattr(attention_router_module.surfacing, "evaluate_and_surface_app_opened", _fake)

    response = authenticated_client.post("/api/v1/attention/app-opened", json={"timezone": _CAIRO})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "surfaced"
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "عندك مهمة متأخرة."
    assert "id" in body["message"]
    assert "created_at" in body["message"]
    # No internal policy fields leaked alongside the message either.
    assert "score" not in body
    assert "fallback_reason" not in body


def test_invalid_timezone_returns_422(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise InvalidTimezoneError("Not/ARealZone")

    monkeypatch.setattr(attention_router_module.surfacing, "evaluate_and_surface_app_opened", _raise)

    response = authenticated_client.post("/api/v1/attention/app-opened", json={"timezone": "Not/ARealZone"})

    assert response.status_code == 422


def test_client_cannot_inject_anything_beyond_timezone(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def _fake(db, space_id, user_id, timezone_name):
        captured["space_id"] = space_id
        captured["user_id"] = user_id
        captured["timezone_name"] = timezone_name
        return _silence_result()

    monkeypatch.setattr(attention_router_module.surfacing, "evaluate_and_surface_app_opened", _fake)

    response = authenticated_client.post(
        "/api/v1/attention/app-opened",
        json={
            "timezone": _CAIRO,
            "candidate": {"signal_type": "TASK_OVERDUE", "source_id": 1},
            "score": 999,
            "signal_type": "TASK_OVERDUE",
            "source_id": 1,
            "narration": "I already did it.",
            "surfaced_at": "2020-01-01T00:00:00Z",
            "now": "2099-01-01T00:00:00Z",
            "permission_to_speak": True,
        },
    )

    assert response.status_code == 200
    # Exactly four positional/keyword arguments reached the service —
    # db, space_id, user_id, timezone_name — and timezone_name is
    # exactly the one legitimate client-supplied value, unchanged.
    assert captured["timezone_name"] == _CAIRO
    assert set(captured.keys()) == {"space_id", "user_id", "timezone_name"}


def test_backend_generates_now_request_schema_has_no_now_field() -> None:
    from app.modules.attention.schemas import AppOpenedRequest

    assert "now" not in AppOpenedRequest.model_fields
    assert "surfaced_at" not in AppOpenedRequest.model_fields
    assert set(AppOpenedRequest.model_fields.keys()) == {"timezone"}
