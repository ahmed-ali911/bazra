import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.auth.models import User, UserSession

SESSION_LIFETIME = timedelta(days=14)


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), password_hash.encode())


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def get_the_user(db: Session) -> User | None:
    """Single-user system: there is at most one User row in normal
    operation. Explicitly ordered by id — Checkpoint 3.2's own tests are
    the first to deliberately create a second, persistent User row (to
    prove cross-user isolation through the real auth path), which
    exposed that an unordered .first() has no guaranteed stable pick
    once more than one row exists. Ordering ensures this always resolves
    to the ORIGINAL user, not whichever row Postgres happens to return.
    """
    return db.execute(select(User).order_by(User.id.asc())).scalars().first()


def create_session(db: Session, user: User) -> str:
    raw_token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + SESSION_LIFETIME,
    )
    db.add(session)
    db.commit()
    return raw_token


def get_session_user(db: Session, raw_token: str) -> User | None:
    token_hash = _hash_token(raw_token)
    session = db.execute(
        select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.expires_at > datetime.now(timezone.utc),
        )
    ).scalar_one_or_none()
    if session is None:
        return None
    return db.get(User, session.user_id)


def invalidate_session(db: Session, raw_token: str) -> None:
    token_hash = _hash_token(raw_token)
    session = db.execute(select(UserSession).where(UserSession.token_hash == token_hash)).scalar_one_or_none()
    if session is not None:
        db.delete(session)
        db.commit()
