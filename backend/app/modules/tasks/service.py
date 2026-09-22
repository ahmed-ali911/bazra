from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.tasks.models import Task
from app.modules.tasks.schemas import TaskCreate, TaskUpdate


def list_tasks(db: Session, space_id: int, status: str | None = None) -> list[Task]:
    query = scoped_query(Task, space_id).where(Task.archived_at.is_(None))
    if status is not None:
        query = query.where(Task.status == status)
    query = query.order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
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

    # completed_at is derived from a status transition, never client-supplied
    # directly — set it as a side effect here, in the one place status
    # actually changes.
    if "status" in updates:
        new_status = updates["status"]
        if new_status == "done" and task.status != "done":
            task.completed_at = datetime.now(timezone.utc)
        elif new_status != "done" and task.status == "done":
            task.completed_at = None

    for field, value in updates.items():
        setattr(task, field, value)

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
