from app.modules.model_router import service as model_router_service
from app.modules.model_router.schemas import TextBlock, ToolResultBlock, ToolUseBlock
from app.modules.orchestrator.identity import BAZRA_IDENTITY_INSTRUCTIONS
from app.modules.orchestrator.schemas import HistoryTurn, OrchestratorResult, ToolCallRequest

# Checkpoint 3.8: the terminal tool-result continuation's own system
# prompt — deliberately NOT _build_system_prompt's full Task/Calendar/
# Memory Context Assembly, and deliberately NOT including current
# date/time (the normalized tool_result already carries its own
# period_start/period_end/timezone, which is sufficient temporal
# grounding for every currently-approved reasoning example — see the
# 3.8 architecture review's "no context without a current consumer").
# BAZRA_IDENTITY_INSTRUCTIONS is reused verbatim, not copied or
# paraphrased, so tone/language-mirroring/truthfulness rules are
# identical to every other call — this is not a second Personality
# Engine.
_TOOL_RESULT_SYSTEM_INSTRUCTIONS = (
    "## Reasoning from a tool result\n"
    "You already called a tool for the user's own message above, and its real, "
    "current result is provided below as the tool result. Treat it as the ONLY "
    "authoritative source of live information for this answer — never invent a "
    "missing field, never substitute your own general knowledge for it, and "
    "never claim it contains something it does not. If the result doesn't fully "
    "answer the question, say plainly what you don't know rather than guessing. "
    "Clearly distinguish the observed/forecast facts from any judgment or "
    "recommendation you offer on top of them. Answer naturally, in the same "
    "language as the user's original message.\n"
)


def _build_tool_result_system_prompt() -> str:
    return f"{BAZRA_IDENTITY_INSTRUCTIONS}\n{_TOOL_RESULT_SYSTEM_INSTRUCTIONS}"

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
    "- You can only READ Inbox items and Life Areas in \"Current Data\" below — "
    "you have NO ability to edit, delete, mark complete, or create either of "
    "those in this conversation. For Tasks, you can propose creating a new "
    "one, updating an existing one, or removing an existing one (see below). "
    "For Calendar events, you can propose creating a new one, updating an "
    "existing one, or removing an existing one (see the Calendar section "
    "below) — you still cannot mark complete an existing one.\n"
    "- If the user's message asks you to create a new task, you MUST call the "
    "propose_create_task tool — a plain-text promise or offer (e.g. \"I'll add "
    "that task\") is NOT a substitute for actually calling it. This does NOT "
    "create the task — it only proposes it. The user must explicitly confirm "
    "before anything is created. You may add a brief line of text "
    "alongside the tool call, but the tool call itself is required.\n"
    "- If the user's message asks you to change an EXISTING task — mark it "
    "done, reopen it, reschedule it, rename it, edit its description, or "
    "reassign its life area — you MUST call the propose_update_task tool, "
    "referencing the task's own task_id shown in Current Data below; a "
    "plain-text description of the change is NOT a substitute for calling it. "
    "This does NOT change it — it only proposes it; only include the fields "
    "actually changing.\n"
    "- If the user's message asks you to remove an existing task, you MUST call "
    "the propose_delete_task tool, referencing the task's own task_id shown in "
    "Current Data below; a plain-text promise or description of removing it is "
    "NOT a substitute for calling it. This does NOT remove it — it only "
    "proposes it; the user must still explicitly confirm before anything is "
    "actually removed.\n"
    "- Only call propose_create_task/propose_update_task/propose_delete_task/"
    "propose_create_event/propose_update_event/propose_delete_event when the "
    "user is actually asking for that specific creation, change, or removal "
    "right now — never for a read-only question, hypothetical discussion, an "
    "explanation, or anything else not actually being requested.\n"
    "- If the user asks you to mark complete an existing Calendar event, or "
    "to edit, delete, mark complete, or create anything for Inbox or Life "
    "Areas, say plainly that this isn't available yet in this "
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
    "\n"
    "Calendar (Checkpoint 3.15):\n"
    "- Task vs. CalendarEvent: a Task is something the user needs to do or "
    "complete, with no inherent scheduled time window required. A "
    "CalendarEvent is something occurring at a scheduled time or time window "
    "— a meeting, appointment, or reserved block of time. Choose exactly one "
    "based on how the user describes it — never propose both a task and an "
    "event for the same single request.\n"
    "- If the user's message asks you to create a new CalendarEvent, you MUST "
    "call the propose_create_event tool — a plain-text promise or offer is "
    "NOT a substitute for actually calling it. This does NOT create the event "
    "— it only proposes it; the user must still explicitly confirm before "
    "anything is created.\n"
    "- If the user clearly describes a duration-based event (a meeting, "
    "appointment, or scheduled session) but gives only a start time with no "
    "end time or duration, do NOT call propose_create_event yet — ask a "
    "brief clarification question instead (e.g. \"What time does it "
    "finish?\"). Do not invent or assume a duration. Once the user answers, "
    "use their reply together with the rest of the conversation to propose "
    "the complete event.\n"
    "- starts_at and ends_at must be timezone-aware ISO 8601 instants with an "
    "explicit UTC offset (see the tool's own description) — resolve the "
    "user's stated local time using the \"Current date/time\" fact below "
    "(which gives you the current local time, the IANA timezone, and the "
    "current UTC offset) and encode that same offset in your output. Never "
    "omit the offset.\n"
    "- If the user's message asks you to change an EXISTING CalendarEvent — "
    "move it to a new time, change its duration, rename it, edit its "
    "description, or reassign its life area — you MUST call the "
    "propose_update_event tool, referencing the event's own event_id shown "
    "in Current Data below (shown as event_id=N next to each event); a "
    "plain-text description of the change is NOT a substitute for calling "
    "it. This does NOT change it — it only proposes it; only include the "
    "fields actually changing. If more than one event could plausibly be "
    "what the user means (e.g. two similarly-titled events), ask which one "
    "they mean instead of guessing — never update more than one event for a "
    "single request.\n"
    "- When moving a bounded event (one that has both a start and an end "
    "time, both shown in Current Data) to a new time without the user asking "
    "to change its duration, preserve its existing duration: compute and "
    "include BOTH the new starts_at and the new ends_at yourself from the "
    "event's own current values (e.g. an event currently 11:00-12:00, moved "
    "to 2 PM, becomes starts_at=14:00, ends_at=15:00) — never send only one "
    "end of a move and leave the other implicit. For a genuine point event "
    "(no ends_at shown), moving it only changes starts_at. For a pure "
    "duration change (\"make it 30 minutes longer\", \"have it finish at "
    "3\"), compute and send only the resulting final ends_at.\n"
    "- If the user's message asks you to remove an existing CalendarEvent — "
    "'cancel my meeting', 'delete the event', 'remove it from my calendar', "
    "and their natural Arabic equivalents all mean the same thing today: "
    "removing BAZRA's own local record of it. You MUST call the "
    "propose_delete_event tool, referencing the event's own event_id shown "
    "in Current Data below; a plain-text promise or description of removing "
    "it (including words like 'done', 'deleted', 'removed', or 'cancelled') "
    "is NOT a substitute for calling it. This does NOT remove it — it only "
    "proposes it; the user must still explicitly confirm before anything is "
    "actually removed. If more than one event could plausibly be what the "
    "user means, ask which one instead of guessing — never remove more than "
    "one event for a single request. BAZRA has NO integration with Google "
    "Calendar, Outlook, or any other external calendar, and no way to "
    "contact anyone — never say or imply that another person was notified, "
    "that a real-world meeting was cancelled with them, or that any "
    "external calendar was changed; only that the event was removed from "
    "BAZRA's own calendar.\n"
    "\n"
    "Weather (Checkpoint 3.7):\n"
    "- If the user asks about current or upcoming weather (temperature, rain, "
    "general conditions), use the get_weather tool. It executes immediately — no "
    "confirmation needed, unlike the propose_* tools — and returns real, current "
    "data; never answer a weather question from your own general knowledge.\n"
    "- Only call get_weather when the user's OWN message explicitly names a "
    "location. Never guess, default, or infer a location from timezone, "
    "language, Memory, or earlier conversation — if no location was given, ask "
    "the user which place they mean instead of calling the tool.\n"
    "- Only 'now', 'today', 'tonight', and 'tomorrow' are supported. If asked "
    "about a further-out period (e.g. next week), say plainly that isn't "
    "available yet rather than guessing or calling the tool with an unsupported "
    "value.\n"
    "- Set response_mode to 'factual' when the user just wants to be told the "
    "weather — you will get a ready-made reply back. Set it to 'reason' when "
    "the user is asking for judgment or a recommendation based on the weather "
    "(Checkpoint 3.8) — e.g. what to wear, whether to go out, whether it's good "
    "for an activity; you will then be given the real weather facts and asked "
    "to answer the original question yourself, grounded in them.\n"
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
        tool_call = ToolCallRequest(tool_use_id=first.id, tool_name=first.name, arguments=first.input)

    return OrchestratorResult(text=response.text, tool_call=tool_call, correlation_id=response.correlation_id)


def generate_tool_result_reply(
    user_message: str,
    tool_use_id: str,
    tool_name: str,
    tool_arguments: dict,
    assistant_text: str | None,
    tool_result_content: str,
    correlation_id: str,
) -> str | None:
    """Checkpoint 3.8 — the terminal continuation call for a read tool's
    "reason" response_mode. tools=None, UNCONDITIONALLY: this is what
    structurally forecloses another tool_use (there is nothing declared
    for the model to call), rather than relying on a prompt instruction
    that could be ignored — no agent loop, no recursive tool execution,
    ever possible from this call.

    correlation_id is REQUIRED (never generated here) — the caller
    passes its own initiating chat_completion call's
    OrchestratorResult.correlation_id, so both AiTrace rows for this one
    logical reasoning turn share the same value.

    The reconstructed assistant turn (assistant_text + a ToolUseBlock
    echoing tool_use_id/tool_name/tool_arguments exactly as the model
    itself produced them) must immediately precede the tool_result
    message, with nothing in between — the provider's own required
    ordering (see Anthropic's tool-use docs) — which is exactly what
    this minimal 3-message list is.
    """
    assistant_content: list = []
    if assistant_text:
        assistant_content.append(TextBlock(text=assistant_text))
    assistant_content.append(ToolUseBlock(id=tool_use_id, name=tool_name, input=tool_arguments))

    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_content},
        {"role": "user", "content": [ToolResultBlock(tool_use_id=tool_use_id, content=tool_result_content)]},
    ]

    try:
        response = model_router_service.complete(
            purpose="tool_result_reasoning",
            messages=messages,
            system=_build_tool_result_system_prompt(),
            tools=None,
            correlation_id=correlation_id,
        )
    except model_router_service.ModelRouterError as exc:
        raise OrchestratorError(str(exc)) from exc

    return response.text
