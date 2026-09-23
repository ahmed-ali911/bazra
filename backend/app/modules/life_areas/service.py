import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.calendar import service as calendar_service
from app.modules.life_areas.models import LifeArea
from app.modules.life_areas.schemas import LifeAreaSummary, MyWorldSummary, UnassignedSummary
from app.modules.tasks import service as tasks_service


class LifeAreaAlreadyExists(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"A life area named {name!r} already exists")


class LifeAreaHasLinkedItems(Exception):
    def __init__(self, task_count: int, calendar_event_count: int):
        self.task_count = task_count
        self.calendar_event_count = calendar_event_count
        super().__init__("Life area has linked tasks or calendar events")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "area"


def list_life_areas(db: Session) -> list[LifeArea]:
    return list(db.execute(select(LifeArea).order_by(LifeArea.name)).scalars().all())


def get_life_area(db: Session, life_area_id: int) -> LifeArea | None:
    return db.get(LifeArea, life_area_id)


def create_life_area(db: Session, name: str) -> LifeArea:
    area = LifeArea(name=name, slug=_slugify(name))
    db.add(area)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise LifeAreaAlreadyExists(name) from exc
    db.refresh(area)
    return area


def rename_life_area(db: Session, life_area_id: int, name: str) -> LifeArea | None:
    area = get_life_area(db, life_area_id)
    if area is None:
        return None
    area.name = name
    area.slug = _slugify(name)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise LifeAreaAlreadyExists(name) from exc
    db.refresh(area)
    return area


def count_linked_items(db: Session, life_area_id: int) -> tuple[int, int]:
    """(task_count, calendar_event_count) — see
    tasks_service.count_tasks_by_life_area's docstring for why both are
    deliberately global and status/archived-agnostic: they must match
    exactly what the database's own FK constraints would block
    deletion on.
    """
    return (
        tasks_service.count_tasks_by_life_area(db, life_area_id),
        calendar_service.count_events_by_life_area(db, life_area_id),
    )


def delete_life_area(db: Session, life_area_id: int) -> bool:
    area = get_life_area(db, life_area_id)
    if area is None:
        return False
    task_count, event_count = count_linked_items(db, life_area_id)
    if task_count or event_count:
        raise LifeAreaHasLinkedItems(task_count, event_count)
    db.delete(area)
    db.commit()
    return True


def build_my_world_summary(db: Session, space_id: int) -> MyWorldSummary:
    areas = list_life_areas(db)
    open_summary = tasks_service.summarize_open_tasks_by_life_area(db, space_id)

    life_area_summaries = [
        LifeAreaSummary(
            id=area.id,
            name=area.name,
            slug=area.slug,
            open_task_count=open_summary.get(area.id, (0, None))[0],
            most_urgent_due_at=open_summary.get(area.id, (0, None))[1],
        )
        for area in areas
    ]

    unassigned_count, unassigned_due_at = open_summary.get(None, (0, None))
    return MyWorldSummary(
        life_areas=life_area_summaries,
        unassigned=UnassignedSummary(open_task_count=unassigned_count, most_urgent_due_at=unassigned_due_at),
    )
