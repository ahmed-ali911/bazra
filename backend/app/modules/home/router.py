from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_space_id, get_current_user
from app.database import get_db
from app.modules.home import service
from app.modules.home.schemas import HomeSummary

router = APIRouter()


@router.get("/home/summary", response_model=HomeSummary)
def get_home_summary(
    tomorrow_start: datetime = Query(),
    window_end: datetime = Query(),
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> HomeSummary:
    try:
        return service.build_home_summary(db, space_id, tomorrow_start, window_end)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
