from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.spaces.models import Space


def get_default_space_for_user(db: Session, user_id: int) -> Space:
    """Resolves to the CURRENT USER's own default space — a real,
    structural ownership check via Space.user_id (Checkpoint 3.2), not
    merely "the one space that happens to exist." Replaces the earlier
    get_default_space(), which resolved is_default=True globally with no
    regard for who was asking — see spaces/models.py's Space docstring
    for why that was a real gap, not a deliberate simplification.

    Raises if none exists for this user — a properly set-up environment
    always ensures a space exists via get_or_create_default_space_for_user
    at the same point the User row itself is created (seed.py, or the
    test suite's own user-creation step), so get_current_space_id (the
    real caller of this function on every request) should never actually
    hit this. A raise here means that setup step was skipped, a problem
    worth surfacing loudly rather than papering over.
    """
    return db.execute(
        select(Space).where(Space.user_id == user_id, Space.is_default.is_(True))
    ).scalar_one()


def get_or_create_default_space_for_user(db: Session, user_id: int) -> Space:
    """Idempotently ensures a default Space exists for this user, creating
    one if not. Mirrors how the single User row itself is created lazily
    (seed.py's upsert, or a test fixture) rather than migration-seeded —
    Space moved to this same lazy-creation pattern in Checkpoint 3.2, once
    it needed a real owner and could no longer be unconditionally
    bulk-seeded with no user to assign. Called from seed.py and from the
    test suite's own authenticated_client fixture, right after the user
    itself is created/ensured.
    """
    space = db.execute(
        select(Space).where(Space.user_id == user_id, Space.is_default.is_(True))
    ).scalar_one_or_none()
    if space is not None:
        return space
    space = Space(name="Personal", is_default=True, user_id=user_id)
    db.add(space)
    db.commit()
    db.refresh(space)
    return space
