from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.spaces.models import Space


def get_default_space(db: Session) -> Space:
    """Resolves to the one seeded default space. Raises if none exists —
    that would mean the seed migration didn't run, a setup problem worth
    surfacing loudly rather than papering over.
    """
    return db.execute(select(Space).where(Space.is_default.is_(True))).scalar_one()
