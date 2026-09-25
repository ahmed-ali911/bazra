from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    content: str
    tomorrow_start: datetime
    window_end: datetime
    timezone: str


class SendMessageResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
