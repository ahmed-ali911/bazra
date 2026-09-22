from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.modules.life_areas import service
from app.modules.life_areas.schemas import LifeAreaResponse

router = APIRouter()


@router.get("/life-areas", response_model=list[LifeAreaResponse])
def list_life_areas(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> list[LifeAreaResponse]:
    return [LifeAreaResponse.model_validate(area) for area in service.list_life_areas(db)]
