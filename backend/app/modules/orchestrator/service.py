import logging

from app.modules.model_router import service as model_router_service
from app.modules.model_router.schemas import TextBlock, ToolResultBlock, ToolUseBlock
from app.modules.orchestrator.identity import BAZRA_IDENTITY_INSTRUCTIONS
from app.modules.orchestrator.schemas import HistoryTurn, OrchestratorResult, ToolCallRequest

logger = logging.getLogger(__name__)

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
#
# Checkpoint 3.23: respond_with_text is exactly as inert as
# propose_create_task in this same sense — it is UNTRUSTED MODEL
# PROSE, never execution/confirmation/mutation evidence, and chat/
# never treats calling it as anything more than "here is some text to
# maybe show the user" (subject to the same 3.21 truthfulness guard as
# the old bare-text path it replaces). tool_choice is now forced to
# "any" for this primary chat call (see generate_reply), so a bare,
# tool-less text reply is no longer a possible SHAPE of response at
# all — but that is a STRUCTURAL guarantee about response shape, not a
# new guarantee about what the model chooses to say inside
# respond_with_text's own text argument.
_SYSTEM_INSTRUCTIONS = (
    "## Your job in this conversation\n"
    "Answer questions about the user's own tasks, calendar, inbox, and life "
    "areas, using the rules below.\n\n"
    "Every response you give MUST be exactly one tool call — never plain text "
    "outside a tool call. For ordinary conversation, explanations, informational "
    "or hypothetical questions, negations, statements of past fact, humor, or a "
    "clarifying question, call the respond_with_text tool (kind=\"answer\" for "
    "the first group, kind=\"clarification\" when you need more information or "
    "the target of a write is ambiguous before you could safely propose it). "
    "Never guess a target or invent missing details merely to avoid asking — "
    "call respond_with_text with kind=\"clarification\" instead. This does not "
    "make you robotic: the text you put in respond_with_text's own text field "
    "is your normal, natural reply, in the user's own language and tone.\n\n"
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
    "- The task_id=N / event_id=N / life_area_id=N / mem_id=N annotations shown "
    "next to items in \"Current Data\" and \"What I remember about you\" below "
    "exist ONLY so you can target the correct item when calling a propose_*/"
    "respond_with_text tool — never show one of these raw identifiers, or any "
    "other internal database id, in the natural-language text you say to the "
    "user. Refer to items the way a person would: by their title or name (e.g. "
    "\"your 'Call Hussein' task\", not \"task_id=12\").\n"
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
    module don't need to import model_router directly.

    category (Checkpoint 5.1) carries ModelRouterError.category through
    unchanged — the same normalized ModelFailureCategory value, so Chat
    can build an honest degradation message without importing
    model_router itself. OrchestratorContractViolationError (below)
    sets this to "unknown_provider_error" itself, since a contract
    violation is a real response shape anomaly, not one of Anthropic's
    own documented exception/error-type cases.
    """

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class OrchestratorContractViolationError(OrchestratorError):
    """Checkpoint 3.23 — raised when the primary chat call's own
    tool_choice="any" contract was violated by the provider: zero tool
    calls, or more than one. The provider CALL itself succeeded (this
    is deliberately a SUBCLASS of OrchestratorError, not a sibling —
    chat_service's existing `except OrchestratorError` already catches
    it without any new code there, folding into the same, already-
    proven-safe ChatModelCallFailed path: no assistant message is ever
    persisted for a turn that never produced a real, contract-
    conforming reply, exactly like any other genuine provider/parsing
    failure). Expected to be exceptionally rare — the 3.22 inspection's
    own real-provider experiments observed zero violations across ~30
    forced tool_choice="any" trials — this exists purely as the
    "reinforce the provider guarantee with application validation"
    defensive backstop the 3.23 brief calls for, not a routine path.
    """

    def __init__(self, reason: str, tool_call_count: int):
        self.reason = reason
        self.tool_call_count = tool_call_count
        self.category = "unknown_provider_error"
        Exception.__init__(self, f"{reason} (tool_call_count={tool_call_count})")


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


# Checkpoint 3.23 — the primary chat call's own fixed policy: always
# require exactly one tool call, and disable parallel tool use so the
# provider itself cannot even attempt more than one. A plain dict,
# forwarded through Model Router verbatim (see that module's own
# _call_anthropic docstring for why it stays generic/BAZRA-agnostic).
# This is deliberately NOT parametrized per-caller — generate_reply
# has exactly one call site (chat_service.send_message) and this IS
# that call's contract now, not a per-request choice.
_PRIMARY_CHAT_TOOL_CHOICE = {"type": "any", "disable_parallel_tool_use": True}


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
    module owns the tool (actions_service, for propose_create_task;
    chat_service itself, for the new respond_with_text — see its own
    RespondWithTextArguments).

    Checkpoint 3.23: when tools are offered, this call now forces
    _PRIMARY_CHAT_TOOL_CHOICE — the model MUST return exactly one tool
    call (a real propose_*/get_weather action, or the safe
    respond_with_text escape hatch); a bare, tool-less text reply is no
    longer a possible response SHAPE at all. This function enforces
    that contract itself rather than trusting the provider alone (see
    OrchestratorContractViolationError) — zero or multiple tool calls
    both raise, never silently degrading to "pick the first" or
    "return bare text as if it were normal." This eliminates ONE
    specific failure class (the model omitting a required tool call
    entirely — Population C's "fresh write, text-only miss" as
    originally observed). It does NOT and cannot guarantee the model
    picks the RIGHT tool — see chat_service's own stale-proposal guard
    and the 3.23 close-out's own documented residual for the failure
    class this does not close.

    NOT a pure function: the underlying model call is a real side
    effect (network I/O, real cost, non-deterministic output). The
    history-as-argument design solves the circular-import and
    direct-DB-access concerns; it does not make this function pure.
    """
    messages = [{"role": turn.role, "content": turn.content} for turn in history]
    messages.append({"role": "user", "content": user_message})

    # tool_choice is omitted from the call entirely (not passed as an
    # explicit None) when no tools are offered — the same "backward-
    # compatible when omitted" shape model_router_service.complete
    # itself follows, so a caller/test that never offers tools sees a
    # byte-for-byte unchanged call.
    complete_kwargs = {
        "purpose": "chat_completion",
        "messages": messages,
        "system": _build_system_prompt(context, current_datetime_local),
        "tools": tools,
    }
    if tools:
        complete_kwargs["tool_choice"] = _PRIMARY_CHAT_TOOL_CHOICE

    # Checkpoint 5.3 — tier is NOT passed explicitly here; this purpose
    # resolves to the centralized "standard" default (the broadest,
    # most general-purpose task in this codebase: full history, full
    # Context Assembly, 9 offered tools) via model_router's own
    # _TIER_BY_PURPOSE — see that module for the full policy and why
    # every call site deliberately omits this parameter rather than
    # repeating the assignment here.
    try:
        response = model_router_service.complete(**complete_kwargs)
    except model_router_service.ModelRouterError as exc:
        raise OrchestratorError(exc.category) from exc

    # Checkpoint 3.23 — exactly-one-tool-call enforcement. Applied only
    # when tools were actually offered (tool_choice was only set in
    # that case above); a caller that never offers tools keeps the
    # pre-3.23 "any number of tool_uses from zero up, first one wins"
    # behavior, since no contract was ever requested of the provider
    # for that call. Reinforces the provider's own tool_choice="any"
    # guarantee with real application-side validation rather than
    # trusting it blindly — see OrchestratorContractViolationError's
    # own docstring for how rare this is expected to be in practice.
    if tools:
        call_count = len(response.tool_uses)
        if call_count == 0:
            raise OrchestratorContractViolationError("no_tool_call", call_count)
        if call_count > 1:
            raise OrchestratorContractViolationError("multiple_tool_calls", call_count)
        if response.stop_reason not in (None, "tool_use"):
            # Defensive cross-check only (Checkpoint 3.22/3.23) — never
            # gates correctness by itself, per the explicit instruction
            # that parsed tool_use blocks remain primary. A disagreement
            # here (a real tool_use was parsed, but the provider's own
            # stop_reason says something else) is logged for visibility,
            # not raised — the parsed block is still trusted.
            logger.warning(
                "orchestrator: stop_reason=%r disagrees with parsed tool_uses (count=%d)",
                response.stop_reason, call_count,
            )

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

    # Checkpoint 5.3 — tier omitted; resolves to the centralized
    # "standard" default (genuine grounded judgment over one fetched
    # factual result, e.g. "do I need a jacket" — real reasoning, not a
    # narrow structured classification) via model_router's own
    # _TIER_BY_PURPOSE.
    try:
        response = model_router_service.complete(
            purpose="tool_result_reasoning",
            messages=messages,
            system=_build_tool_result_system_prompt(),
            tools=None,
            correlation_id=correlation_id,
        )
    except model_router_service.ModelRouterError as exc:
        raise OrchestratorError(exc.category) from exc

    return response.text


# ==================================================
# Checkpoint 4.5d — Proactive narration (the HOW, not the WHAT/WHETHER)
# ==================================================

# Deliberately an ADDITIVE addendum to BAZRA_IDENTITY_INSTRUCTIONS
# (reused verbatim, never copied/paraphrased — same precedent as
# _build_tool_result_system_prompt), not a second personality
# definition. This instructs HOW to phrase the one already-selected
# topic handed in below — it never discusses WHETHER to speak or WHICH
# topic to pick; both of those were already decided, deterministically
# and with zero model involvement, by attention/app_opened.py before
# this call ever happens.
_PROACTIVE_NARRATION_INSTRUCTIONS = (
    "## Proactive opening you are writing right now\n"
    "BAZRA is opening a conversation with the user, unprompted, because exactly "
    "ONE attention topic below was already selected by a separate, deterministic "
    "system — not by you, and not something you can second-guess or replace. "
    "Your ONLY job is to narrate THIS ONE topic naturally, in one concise, warm, "
    "companion-like opening line (two short lines at most) — never something "
    "that reads like a notification/alert was emitted.\n\n"
    "Rules:\n"
    "- Use ONLY the facts given below under \"Selected attention topic\" — "
    "never invent a date, time, cause, consequence, or urgency beyond what's "
    "explicitly given.\n"
    "- Mention ONLY this one topic. Never add, hint at, or reference any OTHER "
    "task, event, inbox item, memory, or general summary of the user's day — "
    "not even a brief 'and also...' aside. One topic, nothing else.\n"
    "- Never state or imply that you (BAZRA) have already done, changed, "
    "completed, postponed, rescheduled, cancelled, or removed anything related "
    "to this topic — you have not; you are only noticing and mentioning it.\n"
    "- Describe the authoritative concern directly, from the facts given — "
    "never infer or imply that the user forgot it, still remembers it, was "
    "reminded about it before, previously discussed it, promised someone, "
    "feels guilty about it, is avoiding it, or anything else about their "
    "memory, mood, or intention beyond what the stored topic itself states. "
    "Prefer a direct statement (e.g. a natural equivalent of \"the task to "
    "call Hussein is still open and overdue\") over a presumptuous framing "
    "(e.g. \"do you still remember you were going to call Hussein?\" or "
    "\"haven't you forgotten this?\").\n"
    "- If you invite the user to continue, use a natural, warm, conversational "
    "opening (e.g. a natural equivalent of \"want to take a look?\" or \"shall "
    "we sort it out?\") — never operational or customer-service phrasing (e.g. "
    "never a literal equivalent of \"would you like to handle/process/deal "
    "with it now?\"), and never phrase it as asking permission to execute one "
    "specific write (e.g. never literally ask whether to postpone it, mark it "
    "done, delete it, or move it to another time) — no such action is pending "
    "or being offered right now. Vary this invitation's own wording naturally "
    "rather than always reaching for the same fixed phrase, and do not force "
    "every reply into ending with an invitation at all if a plain, direct "
    "statement already reads naturally on its own.\n"
    "- Never mention scores, thresholds, signal types, ranking, suppression, "
    "cooldowns, \"Attention Engine\"/\"Attention Selection\", internal IDs, or "
    "any other internal system terminology — speak only about the real-world "
    "topic itself, the way a person would mention it.\n"
    "- Default to natural, contemporary Egyptian Arabic, unless the topic's own "
    "title below is clearly written in English, in which case reply in natural "
    "English instead.\n"
)


def _build_proactive_narration_system_prompt() -> str:
    return f"{BAZRA_IDENTITY_INSTRUCTIONS}\n{_PROACTIVE_NARRATION_INSTRUCTIONS}"


def generate_app_opened_narration_text(signal_type: str, title: str, priority: str | None) -> str | None:
    """Checkpoint 4.5d — the ONLY place this checkpoint calls a model
    provider for narration itself (a separate, later call,
    verify_no_mutation_claim, independently certifies the result before
    narration/service.py ever returns it as MODEL text — see that
    module).

    Receives exactly three narrow, already-extracted scalar facts about
    the single already-selected Attention winner — never the candidate/
    Signal object itself, never its score/reason_codes/snapshot/
    source_id, never Context Assembly, Memory, chat history, or any
    other Task/Event/Inbox data. This is the full input contract; there
    is no larger object available here to accidentally leak more out
    of.

    tools=None, unconditionally — exactly like
    generate_tool_result_reply, this forecloses the model calling
    propose_create_task/propose_update_event/any other tool: there is
    nothing declared for it to call, so no ToolCallRequest can ever
    come out of a narration turn, structurally, not merely by prompt
    instruction.

    No correlation_id is threaded through — unlike
    generate_tool_result_reply's own continuation of an existing chat
    turn, a proactive narration is not a continuation of anything; a
    fresh one is generated by Model Router itself, matching every other
    first-call-in-a-workflow convention.
    """
    facts_lines = [f"signal_type: {signal_type}", f"title: {title}"]
    if priority is not None:
        facts_lines.append(f"priority: {priority}")
    user_message = "Selected attention topic:\n" + "\n".join(facts_lines)

    # Checkpoint 5.3 — tier omitted; resolves to the centralized
    # "lightweight" default (exactly three scalar facts in, one short
    # opening line out, no tools — as narrow/bounded as
    # claim_verification below, even though its CURRENT concrete model
    # is unchanged by this checkpoint) via model_router's own
    # _TIER_BY_PURPOSE.
    try:
        response = model_router_service.complete(
            purpose="proactive_narration",
            messages=[{"role": "user", "content": user_message}],
            system=_build_proactive_narration_system_prompt(),
            tools=None,
        )
    except model_router_service.ModelRouterError as exc:
        raise OrchestratorError(exc.category) from exc

    return response.text


class ClaimVerificationFailed(Exception):
    """Checkpoint 3.25 — raised for ANY contract violation of the
    mutation-claim verifier: a provider/parsing failure, zero or
    multiple tool calls, the wrong tool name, or a missing/non-boolean
    claims_bazra_mutation_completed field. Deliberately NOT a subclass
    of OrchestratorError — chat_service must handle this completely
    differently (fail closed to a deterministic reply, never surface
    as ChatModelCallFailed/502) from a genuine primary-chat-call
    failure, so the two exception hierarchies are kept separate to
    make conflating them a type error, not a silent bug.

    category (Checkpoint 5.1) is the normalized ModelFailureCategory
    when, and ONLY when, this was raised because the underlying
    model_router call itself failed (ModelRouterError) — None for every
    other raise site below (zero/multiple tool calls, wrong tool name,
    missing/non-boolean field), where the provider call succeeded and
    the failure is a business-contract violation, not a provider/
    infrastructure failure. This is the durable, in-memory distinction
    the 5.1 "verifier outcome vs verifier infrastructure failure"
    observability requirement calls for: category is not None if and
    only if verification could not even be attempted/completed due to
    the provider; category is None if and only if the provider call
    itself succeeded but the response violated this verifier's own
    output contract. Neither case is a successful negative
    certification — that path never raises at all (see
    verify_no_mutation_claim's own return statement).
    """

    def __init__(self, reason: str, category: str | None = None):
        self.reason = reason
        self.category = category
        super().__init__(reason)


# Checkpoint 3.25 — the independent mutation-claim verifier's own
# minimal system prompt. Deliberately NOT BAZRA_IDENTITY_INSTRUCTIONS —
# this call has nothing to do with BAZRA's own personality/voice; it is
# an external judgment ABOUT a candidate reply, not a reply of its own,
# and receives no Context Assembly/history/user-message at all (see
# verify_no_mutation_claim's own docstring for why — the 3.24
# inspection's own finding that this narrower question needs none of
# that to be answered reliably).
_CLAIM_VERIFICATION_SYSTEM = (
    "You verify a single candidate assistant reply that BAZRA (a personal "
    "assistant app) is considering showing to the user. Your only job: "
    "does this candidate reply assert, in the assistant's own voice, that "
    "BAZRA itself has ALREADY completed a specific state-changing action "
    "(added, removed, updated, saved, or forgotten something) during this "
    "turn? Answer true ONLY for a first-person claim that BAZRA just did "
    "it. Answer false for: an offer or conditional statement about a "
    "future action (\"I can\"/\"if you confirm\"/\"I will\"); a plain "
    "statement that nothing has been done yet; a report of something the "
    "user said they did; a report of an action by an external system "
    "(e.g. Google); a general description of what an action would do; a "
    "question about the user's own day or work; or discussion of a past "
    "event unrelated to BAZRA acting just now. Call the certify_claim "
    "tool exactly once."
)

_CERTIFY_CLAIM_TOOL = {
    "name": "certify_claim",
    "description": "Report whether the candidate reply claims BAZRA just completed a mutation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "claims_bazra_mutation_completed": {"type": "boolean"},
        },
        "required": ["claims_bazra_mutation_completed"],
    },
}

# Forces the SPECIFIC tool (not "any" — there is only ever one tool
# offered to this call anyway) and disables parallel tool use, so the
# provider cannot even attempt more than one certify_claim call.
_CERTIFY_CLAIM_TOOL_CHOICE = {"type": "tool", "name": "certify_claim", "disable_parallel_tool_use": True}


def verify_no_mutation_claim(candidate_text: str) -> bool:
    """Checkpoint 3.25 — the independent, narrowly-scoped verifier this
    checkpoint exists to add. Receives ONLY candidate_text — no user
    message, no history, no Context Assembly, no Memory, no Space data
    — deliberately narrowing both the classification problem (Question
    2: "does THIS reply claim a completed mutation", never Question 1:
    "did the user ask for one" — see the 3.24 inspection's own finding
    that Question 2 is the smaller, already empirically-validated trust
    problem) and the privacy exposure of this call. Uses a separate,
    cheap model (model_router_service's own purpose->model table, keyed
    on "claim_verification") via a forced, single, strict boolean tool
    call — never prose, never a second free-text field for the
    verifier to reason in, never a confidence score, never a
    replacement reply (the application, not the verifier, owns any
    replacement text — see chat_service's own fail-closed message).

    Returns the raw claims_bazra_mutation_completed boolean exactly as
    certified — literal, not inverted, so this function's own name
    describes what it checks, not what the caller should do about the
    result. Raises ClaimVerificationFailed for any contract violation
    (provider failure, zero/multiple tool calls, wrong tool name,
    missing/non-boolean field) — this function only reports facts
    about the verifier call itself; fail-closed POLICY (treating a
    raised failure the same as an explicit True) is the caller's own
    decision, made once, in chat_service.
    """
    # Checkpoint 5.3 — tier omitted; resolves to the centralized
    # "lightweight" default (a single forced boolean tool call over
    # just the candidate text — the narrowest, most bounded task in
    # this codebase) via model_router's own _TIER_BY_PURPOSE. This is
    # also the one purpose with an existing explicit MODEL override
    # (haiku) — tier and that override are independent: assigning
    # "lightweight" here never changes, and is never changed by,
    # _resolve_model's own purpose->model lookup (see that function
    # and _resolve_tier's own docstrings).
    try:
        response = model_router_service.complete(
            purpose="claim_verification",
            # "Candidate reply:\n" framing is load-bearing, not
            # decorative — a real-provider live-gate run during this
            # checkpoint's own implementation found that sending the
            # bare candidate_text as an unattributed user message let
            # the verifier misread a first-person unsafe claim (e.g.
            # "I removed the meeting.") as the USER reporting their own
            # past action — exactly the system prompt's own explicit
            # safe-exception clause — collapsing real accuracy from a
            # separately-verified 27/27 to as low as 5/10 on the
            # unsafe set. Explicitly labeling this as a candidate
            # reply under review (matching the exact framing verified
            # in the 3.24 inspection's own standalone experiment)
            # restored the expected reliability — re-confirmed by this
            # checkpoint's own live gate, not merely restored on paper.
            messages=[{"role": "user", "content": f"Candidate reply:\n{candidate_text}"}],
            system=_CLAIM_VERIFICATION_SYSTEM,
            tools=[_CERTIFY_CLAIM_TOOL],
            tool_choice=_CERTIFY_CLAIM_TOOL_CHOICE,
        )
    except model_router_service.ModelRouterError as exc:
        raise ClaimVerificationFailed("provider_call_failed", category=exc.category) from exc

    tool_uses = response.tool_uses
    if len(tool_uses) != 1:
        raise ClaimVerificationFailed(f"expected exactly one tool call, got {len(tool_uses)}")

    call = tool_uses[0]
    if call.name != "certify_claim":
        raise ClaimVerificationFailed(f"unexpected tool name {call.name!r}")

    claims_completed = call.input.get("claims_bazra_mutation_completed")
    if not isinstance(claims_completed, bool):
        raise ClaimVerificationFailed(
            f"missing/non-boolean claims_bazra_mutation_completed: {claims_completed!r}"
        )

    return claims_completed
