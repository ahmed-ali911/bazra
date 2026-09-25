"""Verifies the row-lock replay guard in confirm_and_execute against a REAL
Postgres database with two genuinely concurrent sessions — not the shared
single db_session fixture used elsewhere, since the whole point is to prove
that a second confirmation attempt against the same row actually blocks on
Postgres's own row lock rather than racing in application code.

Uses sessionmaker(bind=db_session.get_bind()) — the same pattern
test_tasks.py's commit=False test uses for a "fresh session" — so this
runs against the real bazra_test database via the same engine every other
test uses, not the dev database.
"""

import threading

from sqlalchemy.orm import Session, sessionmaker

from app.modules.actions import service as actions_service
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.chat import service as chat_service
from app.modules.spaces import service as spaces_service
from app.modules.tasks import service as tasks_service


def test_two_concurrent_confirmations_result_in_exactly_one_executed_task(db_session: Session) -> None:
    SessionLocal = sessionmaker(bind=db_session.get_bind())

    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)
    source_message = chat_service.record_assistant_message(
        db_session, space.id, user.id, "concurrency test proposal"
    )
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_message.id, "create_task",
        {"title": "Concurrent confirm test"},
    )
    space_id, user_id = space.id, user.id

    results: list = [None, None]

    def _attempt(index: int) -> None:
        session: Session = SessionLocal()
        try:
            results[index] = actions_service.confirm_and_execute(session, space_id, user_id)
        finally:
            session.close()

    t1 = threading.Thread(target=_attempt, args=(0,))
    t2 = threading.Thread(target=_attempt, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    outcomes = sorted(r.outcome for r in results)
    assert outcomes == ["executed", "nothing_pending"], (
        "exactly one concurrent confirmation attempt should execute; the other "
        f"should observe nothing left pending (row-lock replay guard), got {outcomes}"
    )

    verify_session: Session = SessionLocal()
    try:
        real_tasks = tasks_service.list_tasks(verify_session, space_id)
        matching = [t for t in real_tasks if t.title == "Concurrent confirm test"]
        assert len(matching) == 1, (
            f"exactly one Task should have been created despite two concurrent "
            f"confirmation attempts, found {len(matching)}"
        )
    finally:
        verify_session.close()
