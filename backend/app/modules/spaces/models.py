from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel


class Space(BaseModel):
    """The security/isolation boundary from docs/adr/0001-space-scoping-convention.md.
    Exactly one row exists for now (is_default=True), seeded by this module's own
    migration — no secret content involved, unlike the single User row, so no manual
    seed script is needed the way auth's seed.py is. No space-switcher UI yet;
    get_current_space_id (core/deps.py) always resolves to the default space.
    """

    __tablename__ = "spaces"

    name: Mapped[str] = mapped_column(String, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
