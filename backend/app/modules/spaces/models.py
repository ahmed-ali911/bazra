from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel


class Space(BaseModel):
    """The security/isolation boundary from docs/adr/0001-space-scoping-convention.md.
    Exactly one row exists for now (is_default=True), seeded by this module's own
    migration — no secret content involved, unlike the single User row, so no manual
    seed script is needed the way auth's seed.py is. No space-switcher UI yet;
    get_current_space_id (core/deps.py) resolves to the CURRENT USER's own default
    space (Checkpoint 3.2) — a real, structural ownership check via user_id below,
    not merely "the one space that happens to exist." Every space-scoped table's
    isolation guarantee ultimately rests on this FK being correct.

    user_id was added in Checkpoint 3.2 specifically because Chat introduced the
    first table (ChatMessage) with an explicit per-user column, which exposed that
    Space itself had no owner at all before this — "the app is currently
    single-user" was an operational fact, never a technical guarantee. See that
    migration for the explicit, checked (not assumed) backfill of the one existing
    row.
    """

    __tablename__ = "spaces"

    name: Mapped[str] = mapped_column(String, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
