from dataclasses import dataclass


@dataclass
class HistoryTurn:
    """A plain role/content pair — never a real ChatMessage ORM row.
    Chat builds these (already bounded — see chat/service.py's history
    caps) and hands them to Orchestrator as data; Orchestrator never
    reads chat's own storage itself.
    """

    role: str
    content: str


@dataclass
class ToolCallRequest:
    """A tool the model asked to call — Checkpoint 3.3. This is a
    REQUEST, not an action: nothing about receiving this means anything
    was executed. Naming this "request", not "call" or "action" alone,
    is deliberate — see actions_service for where a request like this
    turns into a stored, confirmable proposal, and only ever a
    DIFFERENT, later, explicitly-confirmed step turns a proposal into a
    real domain mutation.
    """

    tool_name: str
    arguments: dict


@dataclass
class OrchestratorResult:
    """text may be None when the model called a tool with no
    accompanying text (a valid, expected shape once tools are offered).
    tool_call is None whenever the model didn't call anything — the
    common case for ordinary read-only questions."""

    text: str | None
    tool_call: ToolCallRequest | None
