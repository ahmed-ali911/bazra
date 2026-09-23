from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_space_id, get_current_user
from app.database import get_db
from app.modules.inbox import service
from app.modules.inbox.schemas import InboxItemResponse, InboxItemUpdate

router = APIRouter()


@router.get("/inbox", response_model=list[InboxItemResponse])
def list_inbox_items(
    unread: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> list[InboxItemResponse]:
    items = service.list_items(db, space_id, unread=unread)
    return [InboxItemResponse.model_validate(item) for item in items]


@router.patch("/inbox/{item_id}", response_model=InboxItemResponse)
def update_inbox_item(
    item_id: int,
    body: InboxItemUpdate,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> InboxItemResponse:
    item = service.mark_read(db, space_id, item_id, body.read)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox item not found")
    return InboxItemResponse.model_validate(item)


@router.delete("/inbox/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_inbox_item(
    item_id: int,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> None:
    dismissed = service.dismiss_item(db, space_id, item_id)
    if not dismissed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox item not found")
