from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.modules.auth import service
from app.modules.auth.models import User


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
