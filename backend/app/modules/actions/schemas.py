from dataclasses import dataclass
from typing import Literal

from app.modules.calendar.schemas import CalendarEventResponse
from app.modules.memory.schemas import MemoryResponse
from app.modules.tasks.schemas import TaskResponse

# Closed, code-level sets — plain strings at rest (matching Task.status/
# AiTrace.purpose's own convention), not native DB enums. Extended in
# Checkpoint 3.4 (save_memory, forget_memory), Checkpoint 3.10
# (update_task), Checkpoint 3.13 (delete_task), Checkpoint 3.15
# (create_event), and Checkpoint 3.18 (update_event) rather than
# accepting an arbitrary string.
ActionType = Literal[
    "create_task", "save_memory", "forget_memory", "update_task", "delete_task", "create_event", "update_event",
]
VALID_ACTION_TYPES: frozenset[str] = frozenset(
    {"create_task", "save_memory", "forget_memory", "update_task", "delete_task", "create_event", "update_event"}
)

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

    task_action (Checkpoint 3.10, extended 3.13) exists because Task has
    no equivalent natural per-object signal: an updated (or removed)
    task's own state doesn't distinguish "just created" from "just
    updated" from "just removed" the way a memory's status already
    does. Set only alongside task, only when outcome=="executed"; left
    None (its default) for every action_type that predates 3.10, so
    create_task's own existing "I've created..." reply is unaffected by
    construction, not by a special case.

    event_action (Checkpoint 3.18) is CalendarEvent's own exact
    counterpart to task_action, for the exact same reason: 'event' is
    now shared between create_event ("created") and update_event
    ("updated"), and without this the deterministic reply would have no
    way to say "updated" instead of "added" for the latter.
    """

    outcome: Literal["executed", "rejected", "nothing_pending", "execution_failed"]
    task: TaskResponse | None = None
    memory: MemoryResponse | None = None
    task_action: Literal["created", "updated", "deleted"] | None = None
    event: CalendarEventResponse | None = None
    event_action: Literal["created", "updated"] | None = None
