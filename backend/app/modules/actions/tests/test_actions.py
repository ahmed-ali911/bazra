from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.modules.actions import service as actions_service
from app.modules.actions.models import ProposedAction
from app.modules.auth import service as auth_service
from app.modules.chat import service as chat_service
from app.modules.spaces import service as spaces_service


@pytest.fixture()
def owner(db_session: Session):
    """Self-contained user+space setup — does not rely on some other test
    file having run first (and possibly created the single User row),
    the same idempotent lazy-creation idiom authenticated_client uses.
    """
    from app.modules.auth.models import User

    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)
    return user, space


def _seed_source_message(db_session: Session, space_id: int, user_id: int) -> int:
    message = chat_service.record_assistant_message(db_session, space_id, user_id, "proposal text")
    return message.id


# ---- create_pending_action --------------------------------------------------------


def test_create_pending_action_stores_validated_arguments(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)

    proposal = actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task",
        {"title": "Call Hussein", "due_at": "2030-06-16T10:00:00+00:00"},
    )

    assert proposal.status == "pending"
    assert proposal.arguments["title"] == "Call Hussein"
    assert proposal.source_chat_message_id == source_id
    assert proposal.expires_at > datetime.now(timezone.utc)


def test_create_pending_action_rejects_malformed_arguments_and_creates_no_row(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)

    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.create_pending_action(
            db_session, space.id, user.id, source_id, "create_task", {}  # missing required "title"
        )

    count = db_session.query(ProposedAction).filter(ProposedAction.source_chat_message_id == source_id).count()
    assert count == 0


def test_create_pending_action_supersedes_any_existing_pending(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)

    first = actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task", {"title": "First"}
    )
    second_source_id = _seed_source_message(db_session, space.id, user.id)
    second = actions_service.create_pending_action(
        db_session, space.id, user.id, second_source_id, "create_task", {"title": "Second"}
    )

    db_session.refresh(first)
    assert first.status == "superseded"
    assert second.status == "pending"
    assert actions_service.get_latest_pending(db_session, space.id, user.id).id == second.id


# ---- confirm_and_execute -----------------------------------------------------------


def test_confirm_and_execute_creates_the_real_task(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task", {"title": "Call Hussein"}
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)

    assert result.outcome == "executed"
    assert result.task.title == "Call Hussein"

    proposal = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert proposal is None  # no longer pending

    from app.modules.tasks import service as tasks_service
    task = tasks_service.get_task(db_session, space.id, result.task.id)
    assert task is not None
    assert task.title == "Call Hussein"


def test_confirm_and_execute_with_nothing_pending(db_session: Session, owner) -> None:
    user, space = owner
    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "nothing_pending"
    assert result.task is None


def test_confirm_and_execute_expired_proposal_is_treated_as_nothing_pending(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    proposal = actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task", {"title": "Too late"}, ttl_minutes=10
    )
    proposal.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "nothing_pending"

    from app.modules.tasks import service as tasks_service
    tasks = tasks_service.list_tasks(db_session, space.id)
    assert not any(t.title == "Too late" for t in tasks)


def test_confirm_and_execute_failure_rolls_back_to_pending_not_a_terminal_state(
    db_session: Session, owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chosen MVP semantics: an execution failure rolls the WHOLE
    transaction back, including the confirmed-transition itself — the
    proposal ends up genuinely 'pending' again, not a separate 'failed'
    state, and is safely re-confirmable (retry = say yes again).
    """
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    proposal = actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task", {"title": "Will fail"}
    )
    proposal_id = proposal.id

    from app.modules.tasks import service as tasks_service

    monkeypatch.setattr(
        tasks_service, "create_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated DB failure"))
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "execution_failed"

    db_session.rollback()  # ensure we read the durable, post-rollback state, not any leftover session cache
    from sqlalchemy import select

    fresh = db_session.execute(select(ProposedAction).where(ProposedAction.id == proposal_id)).scalar_one()
    assert fresh.status == "pending"  # NOT 'failed' — rolled back to pending, per the chosen MVP semantics
    assert fresh.executed_task_id is None

    real_tasks = tasks_service.list_tasks(db_session, space.id)
    assert not any(t.title == "Will fail" for t in real_tasks)


def test_confirm_and_execute_retry_after_failure_succeeds(
    db_session: Session, owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry model IS the confirmation model: after a failed
    attempt leaves the proposal pending, confirming again (as if the
    user just said "yes" a second time) succeeds normally once the
    underlying problem is gone."""
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task", {"title": "Retry me"}
    )

    from app.modules.tasks import service as tasks_service

    real_create_task = tasks_service.create_task
    monkeypatch.setattr(tasks_service, "create_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    first_attempt = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert first_attempt.outcome == "execution_failed"

    monkeypatch.setattr(tasks_service, "create_task", real_create_task)
    second_attempt = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert second_attempt.outcome == "executed"
    assert second_attempt.task.title == "Retry me"


# ---- reject --------------------------------------------------------------------------


def test_reject_marks_pending_as_rejected_and_creates_no_task(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task", {"title": "Never mind"}
    )

    rejected = actions_service.reject(db_session, space.id, user.id)
    assert rejected is True
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is None

    from app.modules.tasks import service as tasks_service
    tasks = tasks_service.list_tasks(db_session, space.id)
    assert not any(t.title == "Never mind" for t in tasks)


def test_reject_with_nothing_pending_returns_false(db_session: Session, owner) -> None:
    user, space = owner
    assert actions_service.reject(db_session, space.id, user.id) is False


# ---- cross-user / cross-space isolation ------------------------------------------------


def test_cross_user_isolation_for_confirm_reject_and_lookup(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "create_task", {"title": "User A's task"}
    )

    from app.modules.auth.models import User
    from app.modules.spaces.models import Space

    other_user = User(password_hash=auth_service.hash_password("other"))
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    other_space = Space(name="Other space", is_default=True, user_id=other_user.id)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)

    assert actions_service.get_latest_pending(db_session, other_space.id, other_user.id) is None
    assert actions_service.confirm_and_execute(db_session, other_space.id, other_user.id).outcome == "nothing_pending"
    assert actions_service.reject(db_session, other_space.id, other_user.id) is False

    # The original proposal is untouched by the other user's attempts.
    assert actions_service.get_latest_pending(db_session, space.id, user.id) is not None


# ---- Checkpoint 3.4: confirm_and_execute dispatches on action_type -----------------------


def test_confirm_and_execute_dispatches_save_memory_creates_active_memory(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "save_memory",
        {"type": "PREFERENCE", "content": "Prefers concise answers"},
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)

    assert result.outcome == "executed"
    assert result.task is None
    assert result.memory is not None
    assert result.memory.status == "active"
    assert result.memory.content == "Prefers concise answers"

    from app.modules.memory import service as memory_service
    stored = memory_service.get_active_memory_for_user(db_session, space.id, user.id, result.memory.id)
    assert stored is not None


def test_confirm_and_execute_dispatches_save_memory_with_supersedes_retires_the_old_one(
    db_session: Session, owner
) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    old_memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Works at a bank")
    )
    db_session.commit()

    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "save_memory",
        {"type": "FACT", "content": "No longer works at a bank", "supersedes_memory_id": old_memory.id},
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "executed"

    db_session.refresh(old_memory)
    assert old_memory.status == "superseded"
    assert old_memory.superseded_by_id == result.memory.id


def test_confirm_and_execute_dispatches_forget_memory_marks_forgotten(db_session: Session, owner) -> None:
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="GOAL", content="Learn backend development")
    )
    db_session.commit()

    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "forget_memory", {"memory_id": memory.id}
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)

    assert result.outcome == "executed"
    assert result.memory.id == memory.id
    assert result.memory.status == "forgotten"

    db_session.refresh(memory)
    assert memory.status == "forgotten"


def test_confirm_and_execute_forget_memory_race_where_target_already_gone_rolls_back_to_pending(
    db_session: Session, owner
) -> None:
    """The forget_memory equivalent of the existing execution-failure
    test above: if the target memory is no longer active by confirm
    time (a genuine TOCTOU race — e.g. forgotten via another path
    between proposal and confirmation), execution fails and the whole
    transaction rolls back to 'pending', reusing the same precedented
    semantics rather than inventing bespoke handling for this case.
    """
    from app.modules.memory import service as memory_service
    from app.modules.memory.schemas import MemoryCreate

    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Will be forgotten early")
    )
    db_session.commit()

    proposal = actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "forget_memory", {"memory_id": memory.id}
    )
    proposal_id = proposal.id

    # Simulate the race: the target becomes inactive AFTER the proposal
    # was created but BEFORE it's confirmed.
    memory_service.forget_memory(db_session, space.id, user.id, memory.id)
    db_session.commit()

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "execution_failed"

    db_session.rollback()
    from sqlalchemy import select
    fresh = db_session.execute(select(ProposedAction).where(ProposedAction.id == proposal_id)).scalar_one()
    assert fresh.status == "pending"


def test_validate_arguments_rejects_unknown_action_type(db_session: Session) -> None:
    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.validate_arguments("not_a_real_action", {})


def test_validate_arguments_save_memory_requires_type_and_content(db_session: Session) -> None:
    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.validate_arguments("save_memory", {"content": "missing type"})
    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.validate_arguments("save_memory", {"type": "NOT_A_REAL_TYPE", "content": "x"})


def test_validate_arguments_forget_memory_requires_memory_id(db_session: Session) -> None:
    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.validate_arguments("forget_memory", {})


# ---- Checkpoint 3.10: update_task ---------------------------------------------------


def test_validate_arguments_update_task_requires_task_id(db_session: Session) -> None:
    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.validate_arguments("update_task", {"status": "done"})


def test_validate_arguments_update_task_rejects_a_no_op_with_no_changes(db_session: Session) -> None:
    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.validate_arguments("update_task", {"task_id": 1})


def test_validate_arguments_update_task_only_returns_explicitly_set_fields(db_session: Session) -> None:
    """The load-bearing exclude_unset proof: an update naming only
    `status` must not silently reintroduce title/description/due_at/
    life_area_id as explicit nulls — that would wipe them at execution
    time. See actions_service.validate_arguments's own docstring note.
    """
    validated = actions_service.validate_arguments("update_task", {"task_id": 1, "status": "done"})
    assert validated == {"task_id": 1, "status": "done"}
    assert "title" not in validated
    assert "description" not in validated
    assert "due_at" not in validated
    assert "life_area_id" not in validated


def test_validate_arguments_update_task_silently_ignores_a_model_supplied_completed_at(
    db_session: Session,
) -> None:
    """Correction 4: there is no parallel completion-timestamp path.
    ProposedTaskUpdate has no completed_at field at all (it subclasses
    TaskUpdate, which has none) — Pydantic's default extra="ignore"
    behavior means a model that somehow included one gets it silently
    dropped, never reaching storage or execution, never entering
    model_fields_set."""
    validated = actions_service.validate_arguments(
        "update_task",
        {"task_id": 1, "status": "done", "completed_at": "2020-01-01T00:00:00+00:00"},
    )
    assert "completed_at" not in validated


def test_confirm_and_execute_dispatches_update_task_applies_only_the_changed_field(
    db_session: Session, owner
) -> None:
    """The end-to-end proof that a partial update stays partial through
    the full propose -> store -> confirm round trip: only `status` was
    named, so title/description/due_at/life_area_id must all survive
    untouched."""
    from app.modules.life_areas import service as life_areas_service
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate

    user, space = owner
    life_area = life_areas_service.create_life_area(db_session, "Home - actions39")
    task = tasks_service.create_task(
        db_session, space.id,
        TaskCreate(
            title="Untouched title - actions310", description="Original description",
            due_at=datetime(2030, 6, 20, 9, 0, tzinfo=timezone.utc), life_area_id=life_area.id,
        ),
    )
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "update_task", {"task_id": task.id, "status": "done"},
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)

    assert result.outcome == "executed"
    assert result.task_action == "updated"
    assert result.task.id == task.id
    assert result.task.status == "done"

    db_session.refresh(task)
    assert task.status == "done"
    assert task.title == "Untouched title - actions310"  # untouched
    assert task.description == "Original description"  # untouched
    assert task.due_at == datetime(2030, 6, 20, 9, 0, tzinfo=timezone.utc)  # untouched
    assert task.life_area_id == life_area.id  # untouched


def test_confirm_and_execute_update_task_nonexistent_task_rolls_back_to_pending(
    db_session: Session, owner
) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "update_task", {"task_id": 999999, "status": "done"},
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "execution_failed"

    proposal = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert proposal is not None
    assert proposal.status == "pending"  # reverted, safely re-confirmable


# ---- Checkpoint 3.10 / Correction 4: completed_at ownership -------------------------


def test_update_task_open_to_done_sets_completed_at_via_existing_task_behavior(
    db_session: Session, owner
) -> None:
    """The direct-REST completion behavior (tasks_service.update_task's
    own open->done edge) is exercised UNCHANGED through the new chat
    write path — never a model-supplied timestamp."""
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate

    user, space = owner
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Complete me - actions39b"))
    assert task.completed_at is None

    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "update_task", {"task_id": task.id, "status": "done"},
    )
    result = actions_service.confirm_and_execute(db_session, space.id, user.id)

    assert result.outcome == "executed"
    assert result.task.completed_at is not None


def test_update_task_done_to_open_clears_completed_at_preserving_existing_behavior(
    db_session: Session, owner
) -> None:
    """The existing done->open edge (tasks_service.update_task already
    nulls completed_at on this transition) is preserved exactly through
    the new chat write path."""
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate, TaskUpdate

    user, space = owner
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Reopen me - actions39c"))
    tasks_service.update_task(db_session, space.id, task.id, TaskUpdate(status="done"))
    db_session.refresh(task)
    assert task.completed_at is not None

    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "update_task", {"task_id": task.id, "status": "open"},
    )
    result = actions_service.confirm_and_execute(db_session, space.id, user.id)

    assert result.outcome == "executed"
    assert result.task.status == "open"
    assert result.task.completed_at is None


# ---- Checkpoint 3.13: delete_task ---------------------------------------------------


def test_validate_arguments_delete_task_requires_task_id(db_session: Session) -> None:
    with pytest.raises(actions_service.InvalidActionArgumentsError):
        actions_service.validate_arguments("delete_task", {})


def test_confirm_and_execute_dispatches_delete_task_archives_exactly_that_task(
    db_session: Session, owner
) -> None:
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate

    user, space = owner
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Archive me - actions313a"))
    other_task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Leave me alone - actions313a"))

    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "delete_task", {"task_id": task.id},
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)

    assert result.outcome == "executed"
    assert result.task_action == "deleted"
    assert result.task.id == task.id
    assert result.task.title == "Archive me - actions313a"

    assert tasks_service.get_task(db_session, space.id, task.id) is None  # archived -> filtered out

    db_session.refresh(other_task)
    assert other_task.archived_at is None  # exactly the intended task, nothing else


def test_confirm_and_execute_delete_task_nonexistent_task_rolls_back_to_pending(
    db_session: Session, owner
) -> None:
    """Covers execution-time race A/D from the 3.13 design report: the
    task was archived, removed, or never existed by the time
    confirmation runs — the proposal reverts to genuinely 'pending'
    (no terminal 'failed' state), exactly like every other action type's
    own execution-failure handling."""
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "delete_task", {"task_id": 999999},
    )

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "execution_failed"

    proposal = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert proposal is not None
    assert proposal.status == "pending"  # reverted, safely re-confirmable


def test_confirm_and_execute_delete_task_already_archived_between_proposal_and_confirmation_rolls_back(
    db_session: Session, owner
) -> None:
    """Race A from the 3.13 design report: the task is archived (e.g. by
    a direct REST delete) after the ProposedAction was created but
    before it's confirmed. Execution must fail safely, not archive an
    already-archived row a second time or silently report success."""
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate

    user, space = owner
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Raced away - actions313b"))
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "delete_task", {"task_id": task.id},
    )

    tasks_service.delete_task(db_session, space.id, task.id)  # the race: archived out from under the proposal

    result = actions_service.confirm_and_execute(db_session, space.id, user.id)
    assert result.outcome == "execution_failed"

    proposal = actions_service.get_latest_pending(db_session, space.id, user.id)
    assert proposal is not None
    assert proposal.status == "pending"


def test_reject_delete_task_proposal_leaves_task_active(db_session: Session, owner) -> None:
    from app.modules.tasks import service as tasks_service
    from app.modules.tasks.schemas import TaskCreate

    user, space = owner
    task = tasks_service.create_task(db_session, space.id, TaskCreate(title="Rejected removal - actions313c"))
    source_id = _seed_source_message(db_session, space.id, user.id)
    actions_service.create_pending_action(
        db_session, space.id, user.id, source_id, "delete_task", {"task_id": task.id},
    )

    rejected = actions_service.reject(db_session, space.id, user.id)
    assert rejected is True

    db_session.refresh(task)
    assert task.archived_at is None
    assert tasks_service.get_task(db_session, space.id, task.id) is not None
