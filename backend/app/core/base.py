"""BaseModel / SpaceScopedMixin convention. See docs/adr/0001-space-scoping-convention.md."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, func
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

    space_id has no FK constraint yet: the `spaces` table doesn't exist until a real
    space-owned entity needs it. Add the FK at that point. Every query against a
    space-scoped table must filter by space_id.
    """

    space_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
