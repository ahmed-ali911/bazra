from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Closed, code-level sets — plain strings at rest, matching Task.status/
# ProposedActionStatus's own convention (validated at a code boundary,
# not requiring an ALTER TYPE to extend).
MemoryType = Literal["FACT", "PREFERENCE", "GOAL", "INFERENCE"]
VALID_MEMORY_TYPES: frozenset[str] = frozenset({"FACT", "PREFERENCE", "GOAL", "INFERENCE"})

# No "rejected" here — rejecting a PROPOSAL (actions.ProposedAction.status)
# never produces a Memory row at all, so there's nothing on this side to
# mark rejected. "forgotten" is the explicit-user-request soft-delete,
# distinct from "superseded" (replaced by a specific newer memory) so
# provenance/audit can tell the two apart without full version history.
MemoryStatus = Literal["active", "superseded", "forgotten"]


class MemoryCreate(BaseModel):
    type: MemoryType
    content: str
    supersedes_memory_id: int | None = None


class MemoryForget(BaseModel):
    memory_id: int


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    content: str
    status: str
    superseded_by_id: int | None
    created_at: datetime
    updated_at: datetime
