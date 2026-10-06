from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime


# Checkpoint 5.7H — Manual Gemini Test Mode's own strict, closed
# request-level enum (section 12 of its own brief: "the frontend must
# NOT be allowed to submit provider='anything'/model='anything'" — a
# bare string field would let the browser attempt exactly that; pydantic
# rejects anything outside this Literal set at the API boundary before
# chat/service.py ever sees it). "default" preserves pre-5.7H behavior
# exactly; "google_gemini_test" is the ONE currently-supported manual
# override. The actual provider/model mapping for "google_gemini_test"
# is owned entirely by chat/service.py (see its own
# _MODEL_PROVIDER_OVERRIDE_MAP) — this schema only names the choice,
# never the underlying provider/model strings, so the browser can never
# supply either directly.
ModelProviderOverride = Literal["default", "google_gemini_test"]


class SendMessageRequest(BaseModel):
    content: str
    tomorrow_start: datetime
    window_end: datetime
    timezone: str
    # Checkpoint 5.7H — optional, defaults to "default" (pre-5.7H
    # behavior, byte-for-byte) so every existing caller (including every
    # pre-5.7H test and the frontend before its own update) continues to
    # work unchanged without ever supplying this field.
    model_provider_override: ModelProviderOverride = "default"


class SendMessageResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
