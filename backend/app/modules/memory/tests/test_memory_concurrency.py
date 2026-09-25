"""Verifies the row-lock replay guard already proven for task creation
(actions/tests/test_actions_concurrency.py) also holds for save_memory —
proving the ACTUAL downstream dispatch (memory_service.create_memory)
has no concurrency bug of its own, not just that the ProposedAction-level
lock exists (already proven). Real two Postgres sessions, real threads.
"""

import threading

from sqlalchemy.orm import Session, sessionmaker

from app.modules.actions import service as actions_service
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.chat import service as chat_service
from app.modules.memory import service as memory_service
from app.modules.spaces import service as spaces_service


def test_two_concurrent_memory_confirmations_result_in_exactly_one_active_memory(db_session: Session) -> None:
    SessionLocal = sessionmaker(bind=db_session.get_bind())

    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)
    source_message = chat_service.record_assistant_message(
        db_session, space.id, user.id, "concurrency memory proposal"
    )
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_message.id, "save_memory",
        {"type": "PREFERENCE", "content": "Concurrent memory confirm test"},
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
        memories, _ = memory_service.get_relevant_memories(verify_session, space_id, user_id, limit=1000)
        matching = [m for m in memories if m.content == "Concurrent memory confirm test"]
        assert len(matching) == 1, (
            f"exactly one Memory should have been created despite two concurrent "
            f"confirmation attempts, found {len(matching)}"
        )
    finally:
        verify_session.close()
