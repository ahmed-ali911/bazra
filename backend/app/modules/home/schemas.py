from pydantic import BaseModel

from app.modules.calendar.schemas import AgendaItem
from app.modules.inbox.schemas import InboxItemResponse
from app.modules.tasks.schemas import TaskResponse


class HomeSummary(BaseModel):
    """Reuses each source module's own response schema rather than
    duplicating field lists — coming_up reuses AgendaItem, the same
    normalized source+id shape Calendar's own agenda already returns.
    """

    focus_today: list[TaskResponse]
    coming_up: list[AgendaItem]
    needs_attention: list[InboxItemResponse]
    anytime: list[TaskResponse]
