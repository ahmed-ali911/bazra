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
