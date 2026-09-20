from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel


class User(BaseModel):
    """Single-user model. Exactly one row is expected to exist — enforced by
    seed.py's upsert, not a DB constraint, since a real multi-user system is
    explicitly out of scope (Section 9.1). Global entity, no SpaceScopedMixin,
    per docs/adr/0001-space-scoping-convention.md.
    """

    __tablename__ = "users"

    password_hash: Mapped[str] = mapped_column(String, nullable=False)


class UserSession(BaseModel):
    """Server-side session record. Named UserSession, not Session, to avoid
    colliding with sqlalchemy.orm.Session. Global entity, not space-scoped —
    this is auth infrastructure, not user content.

    token_hash is a sha256 hex digest of the raw session token that's actually
    set in the cookie — the raw token is never stored, only its hash, so a
    database leak doesn't hand over usable session tokens.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
