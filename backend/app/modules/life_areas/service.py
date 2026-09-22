from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.life_areas.models import LifeArea


def list_life_areas(db: Session) -> list[LifeArea]:
    return list(db.execute(select(LifeArea).order_by(LifeArea.name)).scalars().all())


def get_life_area(db: Session, life_area_id: int) -> LifeArea | None:
    return db.get(LifeArea, life_area_id)
