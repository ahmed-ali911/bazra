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
from app.modules.actions.models import ProposedAction
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.chat import service as chat_service
from app.modules.inbox import service as inbox_service
from app.modules.spaces import service as spaces_service
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate


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


def test_two_concurrent_update_task_confirmations_result_in_exactly_one_execution(db_session: Session) -> None:
    """Checkpoint 3.10: the SAME real-Postgres row-lock proof, for the
    new update_task action_type — proves the generic dispatcher (not
    just create_task's own original path) genuinely inherits the
    guarantee, rather than assuming it does. Marks a real Task
    open->done, which also triggers tasks_service.update_task's own
    InboxItem side effect — asserting that side effect happened exactly
    once is the concrete "no duplicate side effect" proof Correction 5
    asks for; a bug that let both threads execute would create it
    twice.
    """
    from app.modules.inbox import service as inbox_service
    from app.modules.tasks.schemas import TaskCreate

    SessionLocal = sessionmaker(bind=db_session.get_bind())

    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)

    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Concurrent update-task test"))
    source_message = chat_service.record_assistant_message(
        db_session, space.id, user.id, "concurrency update_task test proposal"
    )
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_message.id, "update_task",
        {"task_id": task.id, "status": "done"},
    )
    space_id, user_id, task_id = space.id, user.id, task.id

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
        real_task = tasks_service.get_task(verify_session, space_id, task_id)
        assert real_task.status == "done"  # not double-flipped/left inconsistent

        proposal = (
            verify_session.query(ProposedAction)
            .filter(ProposedAction.source_chat_message_id == source_message.id)
            .one()
        )
        assert proposal.status == "executed"
        assert proposal.executed_task_id == task_id

        completion_items = [
            i for i in inbox_service.list_items(verify_session, space_id)
            if i.title == f"Completed: {real_task.title}"
        ]
        assert len(completion_items) == 1, (
            "exactly one InboxItem side effect should exist despite two concurrent "
            f"confirmation attempts, found {len(completion_items)}"
        )
    finally:
        verify_session.close()


def test_two_concurrent_delete_task_confirmations_result_in_exactly_one_execution(db_session: Session) -> None:
    """Checkpoint 3.13: the SAME real-Postgres row-lock proof, for the
    new delete_task action_type — proves the generic dispatcher extends
    to soft-deletion for free, exactly as it already did for update_task
    in 3.10. Only one confirmation attempt may win the row lock and
    archive the task; the other must observe nothing left pending, and
    the Task must end up archived exactly once (never re-archived with
    a different archived_at, never left un-archived)."""
    SessionLocal = sessionmaker(bind=db_session.get_bind())

    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)

    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Concurrent delete-task test"))
    source_message = chat_service.record_assistant_message(
        db_session, space.id, user.id, "concurrency delete_task test proposal"
    )
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_message.id, "delete_task",
        {"task_id": task.id},
    )
    space_id, user_id, task_id = space.id, user.id, task.id

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
        real_task = tasks_service.get_task(verify_session, space_id, task_id)
        assert real_task is None  # archived_at set -> excluded from the normal, filtered lookup

        raw_task = verify_session.get(tasks_service.Task, task_id)
        assert raw_task is not None
        assert raw_task.archived_at is not None  # archived exactly once, never un-archived

        proposal = (
            verify_session.query(ProposedAction)
            .filter(ProposedAction.source_chat_message_id == source_message.id)
            .one()
        )
        assert proposal.status == "executed"
        assert proposal.executed_task_id == task_id

        second_attempt = actions_service.confirm_and_execute(verify_session, space_id, user_id)
        assert second_attempt.outcome == "nothing_pending"  # no duplicate execution possible after the fact
    finally:
        verify_session.close()
