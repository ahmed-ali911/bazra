from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_space_id, get_current_user
from app.database import get_db
from app.modules.auth.models import User
from app.modules.chat import service
from app.modules.chat.schemas import ChatMessageResponse, SendMessageRequest, SendMessageResponse

router = APIRouter()


@router.post("/chat/messages", response_model=SendMessageResponse)
def send_message(
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    user: User = Depends(get_current_user),
) -> SendMessageResponse:
    try:
        user_message, assistant_message = service.send_message(
            db, space_id, user.id, body.content, body.tomorrow_start, body.window_end
        )
    except service.MessageTooLongError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except service.ChatModelCallFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "model_call_failed", "user_message_id": exc.user_message_id},
        ) from exc
    except ValueError as exc:
        # Propagated from home_service.build_home_summary's own
        # tomorrow_start/window_end range validation.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return SendMessageResponse(
        user_message=ChatMessageResponse.model_validate(user_message),
        assistant_message=ChatMessageResponse.model_validate(assistant_message),
    )


@router.get("/chat/messages", response_model=list[ChatMessageResponse])
def list_messages(
    limit: int = Query(default=50),
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    user: User = Depends(get_current_user),
) -> list[ChatMessageResponse]:
    messages = service.list_recent_messages(db, space_id, user.id, limit=limit)
    return [ChatMessageResponse.model_validate(m) for m in messages]
