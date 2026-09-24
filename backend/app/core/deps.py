from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.modules.auth import service
from app.modules.auth.models import User
from app.modules.spaces import service as spaces_service


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Reusable protection dependency: reads the session cookie, resolves it to
    a User via auth.service, raises 401 (never 404 — the route still matches,
    only the dependency fails) if missing, unrecognized, or expired.
    """
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        user = service.get_session_user(db, raw_token)
        if user is not None:
            return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def get_current_space_id(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> int:
    """Resolves to the AUTHENTICATED USER's own default space (Checkpoint
    3.2) — a real, structural check via Space.user_id, not merely "the
    one space that happens to exist." No space-switcher UI exists yet,
    so there is still nothing for the frontend to choose, but the
    resolution itself is now genuinely tied to who's asking. Every
    existing route already declares both get_current_user and
    get_current_space_id as separate Depends() — FastAPI caches
    get_current_user's result per request, so it is not re-executed
    twice; no calling route's signature needs to change for this fix.
    """
    return spaces_service.get_default_space_for_user(db, user.id).id
