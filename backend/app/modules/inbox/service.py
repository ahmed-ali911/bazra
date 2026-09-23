from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.inbox.models import InboxItem


def create_item(
    db: Session, space_id: int, *, task_id: int | None, title: str, commit: bool = True
) -> InboxItem:
    """Called directly by other modules' service.py (no event bus) at the
    moment something worth the user's attention happens. In this
    checkpoint the only caller is tasks.service.update_task, on the
    open -> done transition — an MVP integration trigger used to establish
    and verify this write-time flow, not a frozen product rule that every
    completed task must generate an inbox item forever. Which events
    generate InboxItems is expected to change as Attention/Automation
    capabilities (Phase 7) mature, without requiring InboxItem's own
    lifecycle to change.

    commit=True by default so this remains directly callable/committing on
    its own. A caller that needs the InboxItem inserted as part of its OWN
    atomic write (e.g. tasks.service.update_task, so a Task can never end
    up durably "done" with no corresponding InboxItem if the insert fails)
    passes commit=False and performs the single covering db.commit()
    itself — see update_task for the concrete case this exists for.
    """
    item = InboxItem(space_id=space_id, task_id=task_id, title=title)
    db.add(item)
    if commit:
        db.commit()
        db.refresh(item)
    return item


def list_items(db: Session, space_id: int, unread: bool | None = None) -> list[InboxItem]:
    query = scoped_query(InboxItem, space_id).where(InboxItem.archived_at.is_(None))
    if unread is True:
        query = query.where(InboxItem.read_at.is_(None))
    elif unread is False:
        query = query.where(InboxItem.read_at.is_not(None))
    query = query.order_by(InboxItem.created_at.desc())
    return list(db.execute(query).scalars().all())


def get_item(db: Session, space_id: int, item_id: int) -> InboxItem | None:
    query = scoped_query(InboxItem, space_id).where(
        InboxItem.id == item_id, InboxItem.archived_at.is_(None)
    )
    return db.execute(query).scalar_one_or_none()


def mark_read(db: Session, space_id: int, item_id: int, read: bool) -> InboxItem | None:
    item = get_item(db, space_id, item_id)
    if item is None:
        return None
    item.read_at = datetime.now(timezone.utc) if read else None
    db.commit()
    db.refresh(item)
    return item


def dismiss_item(db: Session, space_id: int, item_id: int) -> bool:
    """Soft delete: sets archived_at. Reuses the exact same soft-delete
    mechanism as Task/CalendarEvent — "dismissed" and "archived" are the
    same thing here, not two separate concepts."""
    item = get_item(db, space_id, item_id)
    if item is None:
        return False
    item.archived_at = datetime.now(timezone.utc)
    db.commit()
    return True
