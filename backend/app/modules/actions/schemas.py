from dataclasses import dataclass
from typing import Literal

from app.modules.tasks.schemas import TaskResponse

# Closed, code-level sets — plain strings at rest (matching Task.status/
# AiTrace.purpose's own convention), not native DB enums. action_type has
# exactly one member in this checkpoint; extend here, not by accepting
# an arbitrary string, when a second action type is ever added.
ActionType = Literal["create_task"]
VALID_ACTION_TYPES: frozenset[str] = frozenset({"create_task"})

# Deliberately no "failed" — see actions/service.py's
# confirm_and_execute docstring for why an execution failure rolls back
# to "pending" (safely re-confirmable) rather than reaching a terminal
# failure state that would need its own retry mechanism.
ProposedActionStatus = Literal["pending", "confirmed", "executed", "rejected", "expired", "superseded"]


@dataclass
class ConfirmResult:
    """outcome is one of: "executed" | "rejected" | "nothing_pending" |
    "execution_failed". task is populated only when outcome=="executed".
    """

    outcome: Literal["executed", "rejected", "nothing_pending", "execution_failed"]
    task: TaskResponse | None = None
