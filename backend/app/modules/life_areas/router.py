from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_space_id, get_current_user
from app.database import get_db
from app.modules.life_areas import service
from app.modules.life_areas.schemas import LifeAreaCreate, LifeAreaResponse, LifeAreaUpdate, MyWorldSummary

router = APIRouter()


@router.get("/life-areas", response_model=list[LifeAreaResponse])
def list_life_areas(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> list[LifeAreaResponse]:
    return [LifeAreaResponse.model_validate(area) for area in service.list_life_areas(db)]


@router.get("/life-areas/summary", response_model=MyWorldSummary)
def get_my_world_summary(
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> MyWorldSummary:
    return service.build_my_world_summary(db, space_id)


@router.post("/life-areas", response_model=LifeAreaResponse, status_code=status.HTTP_201_CREATED)
def create_life_area(
    body: LifeAreaCreate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> LifeAreaResponse:
    try:
        area = service.create_life_area(db, body.name)
    except service.LifeAreaAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return LifeAreaResponse.model_validate(area)


@router.patch("/life-areas/{life_area_id}", response_model=LifeAreaResponse)
def rename_life_area(
    life_area_id: int,
    body: LifeAreaUpdate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> LifeAreaResponse:
    try:
        area = service.rename_life_area(db, life_area_id, body.name)
    except service.LifeAreaAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if area is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Life area not found")
    return LifeAreaResponse.model_validate(area)


@router.delete("/life-areas/{life_area_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_life_area(
    life_area_id: int,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> None:
    try:
        deleted = service.delete_life_area(db, life_area_id)
    except service.LifeAreaHasLinkedItems as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "life_area_has_linked_items",
                "task_count": exc.task_count,
                "calendar_event_count": exc.calendar_event_count,
                "total_count": exc.task_count + exc.calendar_event_count,
            },
        ) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Life area not found")
