from dataclasses import dataclass
from typing import Literal

# A fixed, code-level closed set — stored as a plain String column on
# AiTrace, not a native DB enum, matching Task.status's own precedent
# (validated at a code boundary, not requiring an ALTER TYPE to extend).
# Extend this set as real callers are added (Chat in 3.2, Memory
# extraction in 3.4, tool-result continuation in 3.8, the mutation-claim
# verifier in Checkpoint 3.25) rather than accepting an arbitrary string.
ModelCallPurpose = Literal[
    "chat_completion", "memory_extraction", "tool_result_reasoning", "claim_verification",
    "proactive_narration",
]
VALID_PURPOSES: frozenset[str] = frozenset({
    "chat_completion", "memory_extraction", "tool_result_reasoning", "claim_verification",
    "proactive_narration",
})


@dataclass
class ToolUseBlock:
    """One structured tool-call the model made, if any — Checkpoint 3.3.
    name/input come straight from the provider's own tool_use content
    block; input is NOT re-validated here (that happens at the domain
    boundary, e.g. against TaskCreate, in whichever module owns the
    tool). id (Checkpoint 3.8) is the provider's own tool_use_id — kept
    so a later tool_result continuation can reference exactly this
    call; also reused as an OUTBOUND representation (see
    ToolResultBlock) when a caller replays this exact block back to the
    model as part of the assistant's own prior turn.
    """

    id: str
    name: str
    input: dict


@dataclass
class TextBlock:
    """A plain text content block — Checkpoint 3.8. Used only on the
    OUTBOUND side, to replay the model's own accompanying text as part
    of a reconstructed assistant turn in a tool-result continuation;
    the inbound/response side still just uses ModelResponse.text."""

    text: str


@dataclass
class ToolResultBlock:
    """The OUTBOUND counterpart to a ToolUseBlock — Checkpoint 3.8:
    supplies the deterministically-executed tool's result back to the
    model in a continuation call. content is a plain string (Anthropic
    supports a bare string as tool_result content) — callers serialize
    whatever structured payload they have (e.g. a normalized domain
    result) into a JSON string themselves; this module never inspects
    or re-validates that content, matching ToolUseBlock.input's own
    "not re-validated here" precedent.
    """

    tool_use_id: str
    content: str
    is_error: bool = False


# A message's content may be a plain string (unchanged since 3.2) or,
# as of 3.8, a list of the blocks above — used to replay a prior
# assistant tool_use turn and to supply a tool_result. Anthropic's own
# wire-format dict shapes (the literal "type": "tool_use" keys etc.)
# are constructed ONLY inside model_router/service.py's
# _serialize_content — nothing outside this module ever needs to know
# that shape.
MessageContent = str | list["TextBlock | ToolUseBlock | ToolResultBlock"]


@dataclass
class ModelResponse:
    text: str | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    tool_uses: list[ToolUseBlock]
    correlation_id: str
    stop_reason: str | None = None
