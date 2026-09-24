from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel, SpaceScopedMixin


class ChatMessage(BaseModel, SpaceScopedMixin):
    """Scoped by BOTH space_id (SpaceScopedMixin) AND an explicit
    user_id — extending the same reasoning already applied to Memory's
    ownership: this is personal conversational content, and a future
    multi-user version without explicit per-user ownership would leak
    conversations across users by construction, not by bug. Every read
    of this table (list_recent_messages, the GET endpoint) filters by
    BOTH columns directly, rather than relying solely on whatever the
    Space->User ownership fix enforces elsewhere — defense in depth,
    since the column exists precisely to make that check direct.

    Single ongoing conversation per space — no separate Conversation
    grouping entity in this checkpoint; an additive migration later if
    multiple named threads are ever needed.

    role is a plain string ("user" | "assistant"), matching Task.status's
    own convention. content is the actual message text — this is a
    genuinely different concern from ai_traces' "never store prompt/
    response text" rule (a different table, owned by a different module,
    for a different purpose): a chat history necessarily stores what was
    actually said, that's what it's for.
    """

    __tablename__ = "chat_messages"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
