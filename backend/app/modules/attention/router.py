from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_space_id, get_current_user
from app.database import get_db
from app.modules.attention import surfacing
from app.modules.attention.schemas import AppOpenedRequest, AppOpenedResponse, InvalidTimezoneError
from app.modules.auth.models import User
from app.modules.chat.schemas import ChatMessageResponse

router = APIRouter()


@router.post("/attention/app-opened", response_model=AppOpenedResponse)
def app_opened(
    body: AppOpenedRequest,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    user: User = Depends(get_current_user),
) -> AppOpenedResponse:
    """Checkpoint 4.5e — the V1 production trigger endpoint. `space_id`
    and `user_id` are always derived from the authenticated session
    (existing get_current_space_id/get_current_user conventions),
    never from the request body. Silence is a normal 200 response, not
    an error — see surfacing.evaluate_and_surface_app_opened's own
    docstring for what is and is not a deterministic-silence condition.
    """
    try:
        result = surfacing.evaluate_and_surface_app_opened(db, space_id, user.id, body.timezone)
    except InvalidTimezoneError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    if result.status == "silence":
        return AppOpenedResponse(status="silence")
    return AppOpenedResponse(status="surfaced", message=ChatMessageResponse.model_validate(result.message))
