from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.modules.auth import service
from app.modules.auth.models import User
from app.modules.auth.schemas import LoginRequest

router = APIRouter()


@router.post("/auth/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    user = service.get_the_user(db)
    if user is None or not service.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

    raw_token = service.create_session(db, user)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=int(service.SESSION_LIFETIME.total_seconds()),
        path="/",
    )
    return {"status": "ok"}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        service.invalidate_session(db, raw_token)
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"status": "ok"}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)) -> dict[str, bool]:
    return {"authenticated": True}
