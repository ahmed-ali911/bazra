from dataclasses import dataclass
from typing import Literal

from app.modules.memory.schemas import MemoryResponse
from app.modules.tasks.schemas import TaskResponse

# Closed, code-level sets — plain strings at rest (matching Task.status/
# AiTrace.purpose's own convention), not native DB enums. Extended in
# Checkpoint 3.4 (save_memory, forget_memory) rather than accepting an
# arbitrary string.
ActionType = Literal["create_task", "save_memory", "forget_memory"]
VALID_ACTION_TYPES: frozenset[str] = frozenset({"create_task", "save_memory", "forget_memory"})

# Deliberately no "failed" — see actions/service.py's
# confirm_and_execute docstring for why an execution failure rolls back
# to "pending" (safely re-confirmable) rather than reaching a terminal
# failure state that would need its own retry mechanism.
ProposedActionStatus = Literal["pending", "confirmed", "executed", "rejected", "expired", "superseded"]


@dataclass
class ConfirmResult:
    """outcome is one of: "executed" | "rejected" | "nothing_pending" |
    "execution_failed". At most one of task/memory is populated, and
    only when outcome=="executed" — which one depends on the executed
    proposal's action_type. For a "forget_memory" execution, memory is
    the AFFECTED memory (status will read "forgotten"); for
    "save_memory", it's the newly created one (status "active") — the
    memory's own status is what distinguishes the two, rather than
    adding a redundant action_type field here.
    """

    outcome: Literal["executed", "rejected", "nothing_pending", "execution_failed"]
    task: TaskResponse | None = None
    memory: MemoryResponse | None = None
