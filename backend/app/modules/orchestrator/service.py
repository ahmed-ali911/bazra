from app.modules.model_router import service as model_router_service
from app.modules.orchestrator.identity import BAZRA_IDENTITY_INSTRUCTIONS
from app.modules.orchestrator.schemas import HistoryTurn, OrchestratorResult, ToolCallRequest

# Prompt-level instructions only — NOT code-enforced. The code-level
# guarantee that no DOMAIN MUTATION can actually happen regardless of
# what the model says or which tool it calls lives entirely in the fact
# that this module (and chat/) never import a create_*/update_*/
# delete_* DOMAIN function from any other module — calling
# propose_create_task only ever produces a ToolCallRequest, an inert
# data structure; turning that into a real Task requires a SEPARATE,
# later, explicitly-confirmed step in actions_service that this module
# has no path to reach. See this module's own test proving that, plus
# chat's adversarial test proving the database stays unchanged even
# when the model's own text falsely claims otherwise.
_SYSTEM_INSTRUCTIONS = (
    "## Your job in this conversation\n"
    "Answer questions about the user's own tasks, calendar, inbox, and life "
    "areas, using the rules below.\n\n"
    "Rules you must follow:\n"
    "- You can only READ the data provided below in \"Current Data\" — you have NO "
    "ability to edit, delete, mark complete, or create calendar events, inbox "
    "items, or life areas in this conversation.\n"
    "- If the user clearly wants to create a new task, use the propose_create_task "
    "tool. This does NOT create the task — it only proposes it. The user must "
    "explicitly confirm before anything is created. Briefly describe what you're "
    "proposing in your own reply alongside the tool call.\n"
    "- If the user asks you to edit, delete, mark complete, or create anything "
    "other than a task, say plainly that this isn't available yet in this "
    "version — do not claim to have done it, and do not pretend the change "
    "happened.\n"
    "- Only state facts that are explicitly present in \"Current Data\" below. If "
    "something isn't there, say you don't have that information rather than "
    "guessing.\n"
    "- Clearly distinguish what the data explicitly shows (\"your task list "
    "shows...\") from any inference you're making (\"it looks like you might be "
    "busy this week, based on...\").\n"
    "- The data below reflects a limited, bounded snapshot — some items may be "
    "omitted if there were too many to show; the data will say so explicitly when "
    "that happens.\n"
    "- Resolve any relative dates/times (\"tomorrow\", \"tonight\") using the "
    "\"Current date/time\" fact below — never guess or assume today's date.\n"
    "\n"
    "Memory (Checkpoint 3.4):\n"
    "- If the user clearly and explicitly asks you to remember something (a "
    "fact, a preference for how you should behave, or a goal), use the "
    "propose_save_memory tool. This does NOT save it — it only proposes it; the "
    "user must explicitly confirm before it becomes durable. Classify it "
    "honestly: use type INFERENCE for your own tentative interpretation of "
    "something the user said, not FACT — never silently upgrade your own "
    "inference into a claimed fact.\n"
    "- \"What I remember about you\" below lists your currently active memories, "
    "each labeled by type and tagged with a mem_id. Apply PREFERENCE memories "
    "naturally, without asking the user to repeat them. Never restate an "
    "INFERENCE-labeled memory as settled fact — it is explicitly marked tentative "
    "for a reason.\n"
    "- If two or more memories listed below directly contradict each other on the "
    "same topic, do not silently treat one as authoritative — tell the user about "
    "the conflict and ask which is current, e.g. \"You previously told me X, but "
    "I also have Y stored — which is current?\" Do not guess which one is "
    "correct, and do not merge or average them.\n"
    "- If the user's new statement corrects or replaces a specific memory you can "
    "see listed below, call propose_save_memory with supersedes_memory_id set to "
    "that memory's mem_id, so the old one is retired rather than left active "
    "alongside a contradicting new one.\n"
    "- If the user clearly asks you to forget something, use the "
    "propose_forget_memory tool with the mem_id of the specific memory they mean, "
    "exactly as shown below. This does NOT forget it — it only proposes it; the "
    "user must explicitly confirm.\n"
    "- If the user asks what you remember about them, answer only from the "
    "memories actually listed below — do not invent or assume anything beyond "
    "that list.\n"
)


class OrchestratorError(Exception):
    """Wraps model_router_service.ModelRouterError so callers of this
    module don't need to import model_router directly."""


def _build_system_prompt(context: str, current_datetime_local: str) -> str:
    """Identity/personality (who BAZRA is, Checkpoint 3.5) comes first,
    establishing character before the operational tool-use/data rules —
    then the existing turn-specific rules, then the current instant, then
    the retrieved data itself.
    """
    return (
        f"{BAZRA_IDENTITY_INSTRUCTIONS}\n"
        f"{_SYSTEM_INSTRUCTIONS}\n"
        f"## Current date/time\n{current_datetime_local}\n\n"
        f"## Current Data\n{context}"
    )


def generate_reply(
    history: list[HistoryTurn],
    context: str,
    user_message: str,
    current_datetime_local: str,
    tools: list[dict] | None = None,
) -> OrchestratorResult:
    """Coordinates inputs and the provider call. Never touches the
    database itself — history and context are both handed in as plain
    data by the caller (Chat), which is what avoids both a circular
    import (Chat would otherwise need to call back into this module,
    which would need to call back into Chat's own storage to read
    history) and any direct DB access from this module.

    tools is additive (Checkpoint 3.3) — passed straight through to
    Model Router; this function never inspects or validates a tool's
    arguments itself, that happens at the domain boundary in whichever
    module owns the tool (actions_service, for propose_create_task).

    NOT a pure function: the underlying model call is a real side
    effect (network I/O, real cost, non-deterministic output). The
    history-as-argument design solves the circular-import and
    direct-DB-access concerns; it does not make this function pure.
    """
    messages = [{"role": turn.role, "content": turn.content} for turn in history]
    messages.append({"role": "user", "content": user_message})

    try:
        response = model_router_service.complete(
            purpose="chat_completion",
            messages=messages,
            system=_build_system_prompt(context, current_datetime_local),
            tools=tools,
        )
    except model_router_service.ModelRouterError as exc:
        raise OrchestratorError(str(exc)) from exc

    # Multiple tools may be offered (3.3: propose_create_task; 3.4 adds
    # propose_save_memory/propose_forget_memory), but at most one CALL is
    # expected per turn — the first is taken deliberately rather than
    # building support for multiple simultaneous tool calls that nothing
    # in this checkpoint's scope can produce.
    tool_call = None
    if response.tool_uses:
        first = response.tool_uses[0]
        tool_call = ToolCallRequest(tool_name=first.name, arguments=first.input)

    return OrchestratorResult(text=response.text, tool_call=tool_call)
