from dataclasses import dataclass
from typing import Literal

# A fixed, code-level closed set — stored as a plain String column on
# AiTrace, not a native DB enum, matching Task.status's own precedent
# (validated at a code boundary, not requiring an ALTER TYPE to extend).
# Extend this set as real callers are added (Chat in 3.2, Memory
# extraction in 3.4) rather than accepting an arbitrary string.
ModelCallPurpose = Literal["chat_completion", "memory_extraction"]
VALID_PURPOSES: frozenset[str] = frozenset({"chat_completion", "memory_extraction"})


@dataclass
class ModelResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
