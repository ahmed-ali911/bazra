from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.inbox import service as inbox_service
from app.modules.tasks.models import Task
from app.modules.tasks.schemas import TaskCreate, TaskUpdate


def list_tasks(
    db: Session,
    space_id: int,
    status: str | None = None,
    life_area_id: int | None = None,
    unassigned: bool | None = None,
) -> list[Task]:
    """The general task-list/filter endpoint — life_area_id and
    unassigned are mutually exclusive by construction here (the router
    rejects sending both before this is ever called); unassigned=True
    filters to life_area_id IS NULL, otherwise an explicit life_area_id
    filters to an exact match.
    """
    query = scoped_query(Task, space_id).where(Task.archived_at.is_(None))
    if status is not None:
        query = query.where(Task.status == status)
    if unassigned:
        query = query.where(Task.life_area_id.is_(None))
    elif life_area_id is not None:
        query = query.where(Task.life_area_id == life_area_id)
    query = query.order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc())
    return list(db.execute(query).scalars().all())


def count_tasks_by_life_area(db: Session, life_area_id: int) -> int:
    """GLOBAL count (no space_id filter) of ALL tasks referencing this
    life area, regardless of status or archived_at — deliberately
    unscoped and unfiltered, because this exists to answer "how many
    rows would block deleting this life area", which is exactly what
    Postgres's own FK constraint checks against: every row, in every
    space, archived or not, done or not. Not to be confused with
    summarize_open_tasks_by_life_area below, which IS space-scoped and
    open-only, for a completely different purpose (what My World
    displays to the current space's user).
    """
    return db.execute(
        select(func.count()).select_from(Task).where(Task.life_area_id == life_area_id)
    ).scalar_one()


def summarize_open_tasks_by_life_area(db: Session, space_id: int) -> dict[int | None, tuple[int, datetime | None]]:
    """count + MOST URGENT due_at (SQL MIN, which ignores NULL due_at,
    and includes overdue tasks — an overdue task IS the most urgent
    thing in that area, not something this hides) of OPEN, non-archived
    tasks in this space, grouped by life_area_id. Includes a None key
    for Unassigned. One GROUP BY query, not N+1 per area. Space-scoped
    and open-only — the opposite of count_tasks_by_life_area above,
    which is deliberately global and status-agnostic for a different
    purpose (deletion-safety counting, not display).
    """
    rows = db.execute(
        select(Task.life_area_id, func.count(), func.min(Task.due_at))
        .where(Task.space_id == space_id, Task.status == "open", Task.archived_at.is_(None))
        .group_by(Task.life_area_id)
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def list_tasks_due_between(db: Session, space_id: int, from_at: datetime, to_at: datetime) -> list[Task]:
    """Narrow, single-purpose query for Calendar's agenda read-model —
    not a general filtering API. Half-open range [from_at, to_at):
    from_at <= due_at < to_at, so a task due exactly at from_at is
    included and one due exactly at to_at is not, keeping adjacent
    agenda ranges from double-counting a boundary task.

    Deliberately does NOT filter by status — Calendar's agenda is a
    calendar-native view of "what falls on this date," and a task
    completed the day it was due still legitimately belongs there. Home's
    Coming Up section wants the opposite (a completed task isn't
    "upcoming"), which is exactly why list_open_tasks_due_between below is
    a SEPARATE function rather than this one gaining a status filter —
    changing this function's behavior would silently change Calendar's
    own agenda, which is out of scope here.
    """
    query = (
        scoped_query(Task, space_id)
        .where(Task.archived_at.is_(None))
        .where(Task.due_at >= from_at, Task.due_at < to_at)
        .order_by(Task.due_at.asc())
    )
    return list(db.execute(query).scalars().all())


def list_open_tasks_due_before(db: Session, space_id: int, before_at: datetime) -> list[Task]:
    """Narrow, single-purpose query for Home's Focus Today section — every
    OPEN task due before `before_at` (the caller's local tomorrow-start),
    with no lower bound at all, so this naturally includes anything
    overdue however far back together with anything still due later
    today. Not a general filtering API — see list_tasks() for that.
    """
    query = (
        scoped_query(Task, space_id)
        .where(Task.archived_at.is_(None))
        .where(Task.status == "open")
        .where(Task.due_at.is_not(None))
        .where(Task.due_at < before_at)
        .order_by(Task.due_at.asc())
    )
    return list(db.execute(query).scalars().all())


def list_open_tasks_due_between(db: Session, space_id: int, from_at: datetime, to_at: datetime) -> list[Task]:
    """Narrow, single-purpose query for Home's Coming Up section — the
    OPEN-only counterpart to list_tasks_due_between above. Kept as its
    own function rather than adding a status filter to that one, since
    Calendar's own agenda deliberately does NOT exclude done tasks (see
    that function's docstring) and must not change as a side effect of
    building Home.
    """
    query = (
        scoped_query(Task, space_id)
        .where(Task.archived_at.is_(None))
        .where(Task.status == "open")
        .where(Task.due_at >= from_at, Task.due_at < to_at)
        .order_by(Task.due_at.asc())
    )
    return list(db.execute(query).scalars().all())


def list_open_tasks_without_due_date(db: Session, space_id: int) -> list[Task]:
    """Narrow query for Home's Anytime section — open tasks with no due
    date at all."""
    query = (
        scoped_query(Task, space_id)
        .where(Task.archived_at.is_(None))
        .where(Task.status == "open")
        .where(Task.due_at.is_(None))
        .order_by(Task.created_at.desc())
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
