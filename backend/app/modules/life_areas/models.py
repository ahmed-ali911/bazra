from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel


class LifeArea(BaseModel):
    """Cross-domain classification lookup (Work/Personal/Learning/Career/Projects/
    Ideas/Teaching) — see docs/adr/ for why this is a real table (FK + referential
    integrity, single source of truth across every future consuming module) rather
    than a string/enum column repeated per entity. Seeded by this module's own
    migration, same as spaces. Global for now (BaseModel only, no SpaceScopedMixin)
    — if multi-space support ever needs per-space life areas, that's an additive
    migration later, same as User being documented as "genuinely global, for now."
    """

    __tablename__ = "life_areas"

    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
