"""BaseModel / SpaceScopedMixin convention. See docs/adr/0001-space-scoping-convention.md."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BaseModel(Base):
    """Plain base for every table: id, created_at, updated_at. No space concept."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SpaceScopedMixin:
    """Mixin for space-owned entities — inherit alongside BaseModel, never instead of it.

    Space is a security/isolation boundary. Task (Checkpoint 2.2) is the first real
    space-owned entity, so this now carries the FK to spaces.id that
    docs/adr/0001-space-scoping-convention.md deferred until that point. Every query
    against a space-scoped table must filter by space_id — see core/space_scoping.py's
    scoped_query() helper, which every space-scoped service function goes through.

    Structurally separate from LifeAreaScopedMixin below: do not let space-filtering
    helpers grow to also handle life_area_id, and vice versa. They look similar
    (both FK-based scoping mixins) but represent different concepts — Space is
    isolation/ownership, Life Area is cross-domain classification.
    """

    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True, nullable=False)


class LifeAreaScopedMixin:
    """Mixin for entities that can optionally belong to a life area — a cross-domain
    classification concept (Work/Personal/Learning/Career/Projects/Ideas/Teaching),
    structurally separate from SpaceScopedMixin's security/isolation boundary.

    Nullable: not every entity needs a life area, and forcing one when nothing fits
    produces bad data rather than better organization. No enforcement/filtering
    convention analogous to scoped_query() exists for this — a life_area_id is just
    an optional classification, not an access-control boundary, and must never be
    treated as one.
    """

    life_area_id: Mapped[int | None] = mapped_column(ForeignKey("life_areas.id"), index=True, nullable=True)
