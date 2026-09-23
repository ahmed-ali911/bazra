from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_space_id, get_current_user
from app.database import get_db
from app.modules.calendar import service
from app.modules.calendar.schemas import AgendaItem, CalendarEventCreate, CalendarEventResponse, CalendarEventUpdate
from app.modules.life_areas import service as life_areas_service

router = APIRouter()


def _validate_life_area(db: Session, life_area_id: int | None) -> None:
    if life_area_id is not None and life_areas_service.get_life_area(db, life_area_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown life_area_id")


@router.post("/calendar/events", response_model=CalendarEventResponse, status_code=status.HTTP_201_CREATED)
def create_calendar_event(
    body: CalendarEventCreate,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> CalendarEventResponse:
    _validate_life_area(db, body.life_area_id)
    event = service.create_calendar_event(db, space_id, body)
    return CalendarEventResponse.model_validate(event)


@router.get("/calendar/events/{event_id}", response_model=CalendarEventResponse)
def get_calendar_event(
    event_id: int,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> CalendarEventResponse:
    event = service.get_calendar_event(db, space_id, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar event not found")
    return CalendarEventResponse.model_validate(event)


@router.patch("/calendar/events/{event_id}", response_model=CalendarEventResponse)
def update_calendar_event(
    event_id: int,
    body: CalendarEventUpdate,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> CalendarEventResponse:
    if "life_area_id" in body.model_fields_set:
        _validate_life_area(db, body.life_area_id)
    try:
        event = service.update_calendar_event(db, space_id, event_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar event not found")
    return CalendarEventResponse.model_validate(event)


@router.delete("/calendar/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calendar_event(
    event_id: int,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> None:
    deleted = service.delete_calendar_event(db, space_id, event_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar event not found")


@router.get("/calendar/agenda", response_model=list[AgendaItem])
def get_agenda(
    from_: datetime = Query(alias="from"),
    to: datetime = Query(),
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> list[AgendaItem]:
    try:
        return service.build_agenda(db, space_id, from_, to)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
