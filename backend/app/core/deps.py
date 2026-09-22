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


def get_current_space_id(db: Session = Depends(get_db)) -> int:
    """Resolves to the one default space — no space-switcher UI exists yet,
    so there is nothing for the frontend to choose. Every space-scoped
    route depends on this alongside get_current_user.
    """
    return spaces_service.get_default_space(db).id
