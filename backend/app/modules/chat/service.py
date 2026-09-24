from datetime import datetime

from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.chat import context as context_module
from app.modules.chat.models import ChatMessage
from app.modules.chat.write_intent import WRITE_UNAVAILABLE_MESSAGE, detect_clear_write_intent
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import HistoryTurn

# Four independent bounds, per the explicit review requirement — none of
# these were defined before, and the conversation would otherwise grow
# unbounded:
_MAX_HISTORY_MESSAGES = 20  # most recent N messages considered at all
_MAX_HISTORY_CHARS = 4000  # a second, independent cap — whichever binds first wins
_MAX_INCOMING_MESSAGE_CHARS = 4000  # the NEW message, on top of history, not counted inside its budget
_MAX_ASSISTANT_MESSAGE_CHARS = 4000  # complementary, defense-in-depth: caps future history at the source


class MessageTooLongError(Exception):
    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        super().__init__(f"Message too long (max {max_chars} characters)")


class ChatModelCallFailed(Exception):
    def __init__(self, user_message_id: int):
        self.user_message_id = user_message_id
        super().__init__("Model call failed")


def record_user_message(db: Session, space_id: int, user_id: int, content: str) -> ChatMessage:
    message = ChatMessage(space_id=space_id, user_id=user_id, role="user", content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def record_assistant_message(db: Session, space_id: int, user_id: int, content: str) -> ChatMessage:
    if len(content) > _MAX_ASSISTANT_MESSAGE_CHARS:
        content = content[:_MAX_ASSISTANT_MESSAGE_CHARS] + "… [truncated]"
    message = ChatMessage(space_id=space_id, user_id=user_id, role="assistant", content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def list_recent_messages(
    db: Session, space_id: int, user_id: int, limit: int = _MAX_HISTORY_MESSAGES
) -> list[ChatMessage]:
    """Filters by BOTH space_id and user_id explicitly — defense in
    depth, not relying solely on whatever the Space->User ownership fix
    enforces elsewhere. Selects the most recent `limit` by created_at
    DESC, then reverses to chronological order for presentation —
    selection criterion and presentation order are different axes.
    """
    rows = (
        db.execute(
            scoped_query(ChatMessage, space_id)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def _trim_to_char_budget(messages: list[ChatMessage], max_chars: int) -> list[HistoryTurn]:
    """Selects from newest to oldest, stopping once the budget is
    reached. If even the single newest message alone exceeds the
    budget, it is truncated (kept, clipped to its most recent
    `remaining` characters) rather than excluded entirely — some recent
    context is judged more useful than none. This guarantees the
    combined length of everything returned is ALWAYS <= max_chars, with
    no exception case (the original design's "and kept" condition let an
    oversized single message through unclipped — fixed here).
    """
    total = 0
    kept: list[HistoryTurn] = []
    for message in reversed(messages):  # newest first
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(message.content) > remaining:
            kept.append(HistoryTurn(role=message.role, content=message.content[-remaining:]))
            break
        total += len(message.content)
        kept.append(HistoryTurn(role=message.role, content=message.content))
    return list(reversed(kept))


def send_message(
    db: Session,
    space_id: int,
    user_id: int,
    content: str,
    tomorrow_start: datetime,
    window_end: datetime,
) -> tuple[ChatMessage, ChatMessage]:
    """The user's own message is ALWAYS persisted immediately, before
    anything else is attempted — they genuinely sent it. An assistant
    message is persisted ONLY on success (either the deterministic
    unavailability reply, or a real model reply) — never a synthetic
    "error" turn on model failure, which would pollute future context
    with a fake conversational turn that never actually happened.
    """
    if len(content) > _MAX_INCOMING_MESSAGE_CHARS:
        raise MessageTooLongError(_MAX_INCOMING_MESSAGE_CHARS)

    user_message = record_user_message(db, space_id, user_id, content)

    if detect_clear_write_intent(content):
        # Deterministic path — no model call, no cost, no ai_traces row,
        # since nothing was attempted.
        assistant_message = record_assistant_message(db, space_id, user_id, WRITE_UNAVAILABLE_MESSAGE)
        return user_message, assistant_message

    context = context_module.gather_context(db, space_id, tomorrow_start, window_end)
    history_rows = list_recent_messages(db, space_id, user_id)
    history = _trim_to_char_budget(history_rows, _MAX_HISTORY_CHARS)

    try:
        reply_text = orchestrator_service.generate_reply(history=history, context=context, user_message=content)
    except orchestrator_service.OrchestratorError as exc:
        raise ChatModelCallFailed(user_message.id) from exc

    assistant_message = record_assistant_message(db, space_id, user_id, reply_text)
    return user_message, assistant_message
