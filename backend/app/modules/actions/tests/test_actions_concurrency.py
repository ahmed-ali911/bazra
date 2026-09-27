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


def test_two_concurrent_create_event_confirmations_result_in_exactly_one_calendar_event(
    db_session: Session,
) -> None:
    """Checkpoint 3.15: the SAME real-Postgres row-lock proof, for the
    new create_event action_type. No executed_event_id FK column exists
    on ProposedAction (adding one would be a migration, out of scope),
    so success is verified by ProposedAction.status plus counting the
    matching CalendarEvent rows directly — the same "one execution
    wins, exactly one domain row results" guarantee, just checked
    through the row itself rather than a FK back-reference.
    """
    from app.modules.calendar import service as calendar_service

    SessionLocal = sessionmaker(bind=db_session.get_bind())

    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)

    source_message = chat_service.record_assistant_message(
        db_session, space.id, user.id, "concurrency create_event test proposal"
    )
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_message.id, "create_event",
        {"title": "Concurrent create-event test", "starts_at": "2026-09-28T11:00:00+03:00"},
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
        proposal = (
            verify_session.query(ProposedAction)
            .filter(ProposedAction.source_chat_message_id == source_message.id)
            .one()
        )
        assert proposal.status == "executed"

        from datetime import datetime, timezone

        events = calendar_service.build_agenda(
            verify_session, space_id,
            from_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            to_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
        )
        matching = [e for e in events if e.title == "Concurrent create-event test"]
        assert len(matching) == 1, (
            f"exactly one CalendarEvent should have been created despite two concurrent "
            f"confirmation attempts, found {len(matching)}"
        )

        second_attempt = actions_service.confirm_and_execute(verify_session, space_id, user_id)
        assert second_attempt.outcome == "nothing_pending"  # no duplicate execution possible after the fact
    finally:
        verify_session.close()


def test_conversation_lock_prevents_the_adjacency_race_under_forced_interleaving(db_session: Session) -> None:
    """Checkpoint 3.17's own mandatory concurrency proof.

    The inspection's own forced-interleaving experiment demonstrated
    that a PLAIN ChatMessage id-range query is unsafe under real
    concurrency: Postgres allocates a sequence id at INSERT time, not
    at COMMIT time, so a concurrently-inserted, lower-id row can stay
    completely invisible to a reader for as long as its own
    transaction remains open — letting that reader wrongly conclude
    "nothing intervened" for a message that, moments later, would
    correctly count as an intervening turn.

    This test forces the EXACT SAME adversarial interleaving through
    the REAL, shipped `_acquire_conversation_lock` +
    `is_still_conversationally_adjacent` combination (not a
    reimplementation) and proves it closes the window: the concurrent
    "interruption" thread cannot even get an id for its row until the
    "current turn" thread's entire critical section — lock acquire,
    insert, adjacency check, commit (which releases the lock) — has
    fully finished.
    """
    import time

    from app.modules.chat import service as chat_service

    SessionLocal = sessionmaker(bind=db_session.get_bind())

    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)
    space_id, user_id = space.id, user.id

    confirmation = chat_service.record_assistant_message(db_session, space_id, user_id, "confirm? - 317lock")
    pending = actions_service.create_pending_action(
        db_session, space_id, user_id, confirmation.id, "create_task", {"title": "Lock race test - 317lock"},
    )

    t1_result: dict = {}
    t1_started_insert = threading.Event()
    t1_finished = threading.Event()

    def _t1_concurrent_interruption() -> None:
        session: Session = SessionLocal()
        try:
            t1_started_insert.set()
            chat_service._acquire_conversation_lock(session, space_id, user_id)  # blocks while T2 holds it
            msg = chat_service.record_user_message(session, space_id, user_id, "interruption - 317lock", commit=False)
            t1_result["id"] = msg.id
            session.commit()
        finally:
            session.close()
        t1_finished.set()

    main_session: Session = SessionLocal()
    chat_service._acquire_conversation_lock(main_session, space_id, user_id)

    t1 = threading.Thread(target=_t1_concurrent_interruption)
    t1.start()
    t1_started_insert.wait(timeout=5)
    time.sleep(0.3)  # give T1 every chance to race ahead if the lock did not actually serialize it
    assert "id" not in t1_result, "T1 must be blocked on the conversation lock, not free to insert"

    current_message = chat_service.record_user_message(
        main_session, space_id, user_id, "yes - 317lock", commit=False
    )
    current_message_id = current_message.id
    adjacent = actions_service.is_still_conversationally_adjacent(
        main_session, space_id, user_id, pending, current_message_id
    )
    assert adjacent is True  # correct: while the lock is held, T1 genuinely has not inserted yet
    main_session.commit()  # releases the conversation lock
    main_session.close()

    t1_finished.wait(timeout=5)
    t1.join(timeout=5)
    assert "id" in t1_result, "T1 should have proceeded once the lock was released"
    assert t1_result["id"] > current_message_id  # only allocated its id AFTER T2's entire critical section

    verify_session: Session = SessionLocal()
    try:
        later_message = chat_service.record_user_message(
            verify_session, space_id, user_id, "yes again - 317lock"
        )
        adjacent_now = actions_service.is_still_conversationally_adjacent(
            verify_session, space_id, user_id, pending, later_message.id
        )
        assert adjacent_now is False  # T1's real interruption is correctly seen as intervening now
    finally:
        verify_session.close()
