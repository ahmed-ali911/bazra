from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.inbox import service as inbox_service
from app.modules.tasks.models import Task
from app.modules.tasks.schemas import TaskCreate, TaskUpdate


def list_tasks(db: Session, space_id: int, status: str | None = None) -> list[Task]:
    query = scoped_query(Task, space_id).where(Task.archived_at.is_(None))
    if status is not None:
        query = query.where(Task.status == status)
    query = query.order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
    return list(db.execute(query).scalars().all())


def list_tasks_due_between(db: Session, space_id: int, from_at: datetime, to_at: datetime) -> list[Task]:
    """Narrow, single-purpose query for Calendar's agenda read-model —
    not a general filtering API. Half-open range [from_at, to_at):
    from_at <= due_at < to_at, so a task due exactly at from_at is
    included and one due exactly at to_at is not, keeping adjacent
    agenda ranges from double-counting a boundary task.
    """
    query = (
        scoped_query(Task, space_id)
        .where(Task.archived_at.is_(None))
        .where(Task.due_at >= from_at, Task.due_at < to_at)
        .order_by(Task.due_at.asc())
    )
    return list(db.execute(query).scalars().all())


def get_task(db: Session, space_id: int, task_id: int) -> Task | None:
    query = scoped_query(Task, space_id).where(Task.id == task_id, Task.archived_at.is_(None))
    return db.execute(query).scalar_one_or_none()


def create_task(db: Session, space_id: int, data: TaskCreate) -> Task:
    task = Task(space_id=space_id, **data.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(db: Session, space_id: int, task_id: int, data: TaskUpdate) -> Task | None:
    task = get_task(db, space_id, task_id)
    if task is None:
        return None

    updates = data.model_dump(exclude_unset=True)
    previous_status = task.status

    # completed_at is derived from a status transition, never client-supplied
    # directly — set it as a side effect here, in the one place status
    # actually changes.
    just_completed = False
    if "status" in updates:
        new_status = updates["status"]
        if new_status == "done" and previous_status != "done":
            task.completed_at = datetime.now(timezone.utc)
            just_completed = True
        elif new_status != "done" and previous_status == "done":
            task.completed_at = None

    for field, value in updates.items():
        setattr(task, field, value)

    # Fires only on the open -> done edge (never on an update that leaves
    # status alone, and never twice for two updates that both merely keep
    # status at "done"), so an already-done task doesn't accumulate a new
    # InboxItem on every unrelated field edit. Runs after the field-update
    # loop above (so a request that renames AND completes a task in one
    # PATCH snapshots the NEW title), but with commit=False — the insert
    # joins the SAME transaction as the Task's own field changes below, so
    # one db.commit() persists both together, or, on failure, rolls back
    # both. Without this, a Task could end up durably "done" with no
    # corresponding InboxItem if the insert failed after an earlier,
    # separate Task commit had already succeeded. This is an MVP
    # integration trigger to establish and verify the write-time Inbox
    # flow — not a frozen rule that every completed task must generate an
    # inbox item; which events do so is expected to evolve with future
    # Attention/Automation capabilities (Phase 7).
    if just_completed:
        inbox_service.create_item(
            db, space_id, task_id=task.id, title=f"Completed: {task.title}", commit=False
        )

    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, space_id: int, task_id: int) -> bool:
    """Soft delete: sets archived_at rather than removing the row, so a
    future Inbox item referencing this task never dangles."""
    task = get_task(db, space_id, task_id)
    if task is None:
        return False
    task.archived_at = datetime.now(timezone.utc)
    db.commit()
    return True
