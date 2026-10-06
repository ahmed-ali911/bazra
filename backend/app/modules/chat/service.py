import json
import logging
import re
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.actions import service as actions_service
from app.modules.actions.schemas import ConfirmResult
from app.modules.calendar import service as calendar_service
from app.modules.calendar.schemas import CalendarEventResponse
from app.modules.chat import context as context_module
from app.modules.chat import deterministic_retrieval
from app.modules.chat.models import ChatMessage
from app.modules.chat.write_intent import WRITE_UNAVAILABLE_MESSAGE, detect_clear_write_intent
from app.modules.life_areas import service as life_areas_service
from app.modules.memory import service as memory_service
from app.modules.memory.models import Memory
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import HistoryTurn
from app.modules.tasks import service as tasks_service
from app.modules.weather import service as weather_service
from app.modules.weather.schemas import GetWeatherArguments, WeatherProviderError, WeatherResult

logger = logging.getLogger(__name__)

# Four independent bounds, per the explicit review requirement — none of
# these were defined before, and the conversation would otherwise grow
# unbounded:
_MAX_HISTORY_MESSAGES = 20  # most recent N messages considered at all
_MAX_HISTORY_CHARS = 4000  # a second, independent cap — whichever binds first wins
_MAX_INCOMING_MESSAGE_CHARS = 4000  # the NEW message, on top of history, not counted inside its budget
_MAX_ASSISTANT_MESSAGE_CHARS = 4000  # complementary, defense-in-depth: caps future history at the source

# Checkpoint 3.4: bounds on how many memories / how much memory text can
# enter one request — the same truncated-and-said-so discipline as
# chat/context.py's _format_section, not a silent cap.
_MAX_MEMORIES = 20
_MAX_MEMORY_CONTEXT_CHARS = 1500

# The tools ever offered to the model. Calling one produces nothing but a
# ToolCallRequest — an inert data structure. Domain fields mirror the
# relevant Pydantic schema exactly; actions_service re-validates against
# that same schema before ever storing a proposal, so these input_schemas
# are a hint to the model, not the enforcement boundary.
_PROPOSE_CREATE_TASK_TOOL = {
    "name": "propose_create_task",
    "description": (
        "Propose creating a new task. This does NOT create the task — it only "
        "records a proposal that the user must explicitly confirm (e.g. by "
        "replying 'yes') before anything is actually created."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The task's title."},
            "description": {"type": "string", "description": "Optional longer description."},
            "due_at": {"type": "string", "description": "Optional ISO 8601 due date/time."},
            "life_area_id": {
                "type": "integer",
                "description": (
                    "Optional id of an existing life area to assign the task to — only "
                    "use an id that actually appears in Current Data below."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "description": (
                    "Optional. Only set this when the user's own message actually indicates a "
                    "priority (e.g. they say 'high priority', 'urgent', 'low priority', 'whenever, "
                    "no rush'). Omit it entirely otherwise — never guess or infer a priority from "
                    "wording, due date, life area, or anything else; an omitted priority defaults "
                    "to normal."
                ),
            },
        },
        "required": ["title"],
    },
}

_PROPOSE_UPDATE_TASK_TOOL = {
    "name": "propose_update_task",
    "description": (
        "Propose changing an EXISTING task — marking it done or reopening it, "
        "rescheduling its due date, renaming it, editing its description, or "
        "reassigning its life area. This does NOT change it — it only records a "
        "proposal that the user must explicitly confirm before anything is "
        "actually changed. Only use a task_id that actually appears in Current "
        "Data below (shown as task_id=N next to each task) — never guess one. "
        "Only include the fields that are actually changing; never repeat fields "
        "that aren't changing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "The task_id of the existing task to change, exactly as shown in Current Data.",
            },
            "title": {"type": "string", "description": "New title, only if it's changing."},
            "description": {"type": "string", "description": "New description, only if it's changing."},
            "status": {
                "type": "string",
                "enum": ["open", "done"],
                "description": "Set to 'done' to mark complete, or 'open' to reopen — only if this is changing.",
            },
            "due_at": {"type": "string", "description": "New ISO 8601 due date/time, only if it's changing."},
            "life_area_id": {
                "type": "integer",
                "description": "New life area id, only if it's changing — must appear in Current Data below.",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "description": (
                    "New priority, only if the user's own message actually asks to change it (e.g. "
                    "'make it high priority', 'this isn't urgent anymore') — never guess or infer "
                    "one. Include this even to explicitly set it back to 'normal' if that's what the "
                    "user is asking for; omit it entirely if priority isn't part of this change."
                ),
            },
        },
        "required": ["task_id"],
    },
}

_PROPOSE_DELETE_TASK_TOOL = {
    "name": "propose_delete_task",
    "description": (
        "Propose removing an existing task from the user's tasks. This does NOT "
        "remove it — it only records a proposal that the user must explicitly "
        "confirm before anything is actually removed. Only use a task_id that "
        "actually appears in Current Data below (shown as task_id=N next to each "
        "task) — never guess one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "The task_id of the existing task to remove, exactly as shown in Current Data.",
            },
        },
        "required": ["task_id"],
    },
}

_PROPOSE_CREATE_EVENT_TOOL = {
    "name": "propose_create_event",
    "description": (
        "Propose creating a new CalendarEvent — something occurring at a scheduled "
        "time or time window (a meeting, appointment, or reserved block of time). "
        "Use propose_create_task instead for something the user needs to do or "
        "complete with no inherent scheduled time window. Never propose both a "
        "task and an event for the same single request. If the user clearly "
        "describes a duration-based event (a meeting, appointment, or scheduled "
        "session) but gives only a start time with no end time or duration, do "
        "NOT call this tool yet — ask a brief clarification question instead "
        "(e.g. \"What time does it finish?\"); only call this tool once you "
        "actually know when it ends, or once it's clear the user means a single "
        "point in time with no meaningful duration. This does NOT create the "
        "event — it only records a proposal that the user must explicitly "
        "confirm before anything is actually created."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The event's title."},
            "description": {"type": "string", "description": "Optional longer description."},
            "starts_at": {
                "type": "string",
                "description": (
                    "Required. A timezone-aware ISO 8601 instant with an explicit UTC "
                    "offset, e.g. 2026-09-28T11:00:00+03:00 — the offset must match the "
                    "user's own current UTC offset shown below, resolved from their stated "
                    "local time. Never a naive/offset-less string."
                ),
            },
            "ends_at": {
                "type": "string",
                "description": (
                    "Optional. Only include once you actually know when the event ends — "
                    "same timezone-aware ISO 8601 format with an explicit UTC offset as "
                    "starts_at, e.g. 2026-09-28T12:00:00+03:00. Omit entirely for a "
                    "genuine single point in time; never invent a duration."
                ),
            },
            "life_area_id": {
                "type": "integer",
                "description": (
                    "Optional id of an existing life area to assign the event to — only "
                    "use an id that actually appears in Current Data below."
                ),
            },
        },
        "required": ["title", "starts_at"],
    },
}

_PROPOSE_UPDATE_EVENT_TOOL = {
    "name": "propose_update_event",
    "description": (
        "Propose changing an EXISTING CalendarEvent — moving it to a new time, "
        "changing its duration, renaming it, editing its description, or "
        "reassigning its life area. Reference it by the event_id shown next to it "
        "in Current Data below (shown as event_id=N) — never guess an id, and "
        "never resolve one by matching a title yourself. If more than one event "
        "could plausibly match what the user described (e.g. two events with a "
        "similar title), ask which one they mean instead of picking one; never "
        "update more than one event for a single request. When moving a bounded "
        "event (one with both a start and an end) to a new time without the user "
        "asking to change its duration, preserve the existing duration — compute "
        "and include BOTH the new starts_at AND the new ends_at yourself from the "
        "event's own current values shown in Current Data (e.g. a 11:00-12:00 "
        "event moved to 2 PM becomes starts_at=14:00, ends_at=15:00); never send "
        "only one end of a move and leave the other implicit. For a genuine point "
        "event (no ends_at shown), moving it only changes starts_at. For a "
        "duration-only change ('make it 30 minutes longer', 'have it finish at "
        "3'), compute and send the resulting final ends_at. Only include the "
        "fields that are actually changing. This does NOT change it — it only "
        "records a proposal that the user must explicitly confirm before "
        "anything is actually changed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_id": {
                "type": "integer",
                "description": "The event_id of the existing event to change, exactly as shown in Current Data.",
            },
            "title": {"type": "string", "description": "New title, only if it's changing."},
            "description": {"type": "string", "description": "New description, only if it's changing."},
            "starts_at": {
                "type": "string",
                "description": (
                    "New timezone-aware ISO 8601 instant with an explicit UTC offset, only if "
                    "it's changing, e.g. 2026-09-28T14:00:00+03:00. Never a naive/offset-less "
                    "string."
                ),
            },
            "ends_at": {
                "type": "string",
                "description": (
                    "New timezone-aware ISO 8601 instant with an explicit UTC offset, only if "
                    "it's changing (including when moving a bounded event — see above). Same "
                    "format as starts_at."
                ),
            },
            "life_area_id": {
                "type": "integer",
                "description": "New life area id, only if it's changing — must appear in Current Data below.",
            },
        },
        "required": ["event_id"],
    },
}

_PROPOSE_DELETE_EVENT_TOOL = {
    "name": "propose_delete_event",
    "description": (
        "Propose removing an existing CalendarEvent from the user's BAZRA "
        "calendar — this is what 'cancel my meeting', 'delete the event', and "
        "'remove it from my calendar' all mean today: removing BAZRA's own "
        "local record of it. This does NOT contact anyone, send any "
        "invitation update, or change any external calendar — BAZRA has no "
        "such integration. Never say or imply that another person was "
        "notified, that a real-world meeting was cancelled with them, or that "
        "an external calendar (Google, Outlook, etc.) was changed — only that "
        "the event was removed from BAZRA. Reference the event by the "
        "event_id shown next to it in Current Data below (shown as "
        "event_id=N) — never guess an id, and never resolve one by matching a "
        "title yourself. If more than one event could plausibly match what "
        "the user described, ask which one they mean instead of picking one; "
        "never remove more than one event for a single request. This does "
        "NOT remove it — it only records a proposal that the user must "
        "explicitly confirm before anything is actually removed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_id": {
                "type": "integer",
                "description": "The event_id of the existing event to remove, exactly as shown in Current Data.",
            },
        },
        "required": ["event_id"],
    },
}

_PROPOSE_SAVE_MEMORY_TOOL = {
    "name": "propose_save_memory",
    "description": (
        "Propose saving a piece of durable memory about the user (a FACT, "
        "PREFERENCE, GOAL, or your own tentative INFERENCE) for future "
        "conversations. This does NOT save it — it only proposes it; the user "
        "must explicitly confirm before it becomes durable."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["FACT", "PREFERENCE", "GOAL", "INFERENCE"],
                "description": (
                    "FACT: the user directly asserted this. PREFERENCE: how the user "
                    "wants you to behave. GOAL: something the user is working toward. "
                    "INFERENCE: your own tentative interpretation, NOT something the "
                    "user asserted outright — never mislabel an inference as FACT."
                ),
            },
            "content": {"type": "string", "description": "The memory content, written plainly."},
            "supersedes_memory_id": {
                "type": "integer",
                "description": (
                    "Optional: set this to the mem_id shown in 'What I remember about "
                    "you' if this memory corrects/replaces an existing one you can see "
                    "there — the old one is retired, not left active alongside this one."
                ),
            },
        },
        "required": ["type", "content"],
    },
}

_PROPOSE_FORGET_MEMORY_TOOL = {
    "name": "propose_forget_memory",
    "description": (
        "Propose forgetting (deactivating) a specific stored memory, referenced "
        "by its mem_id from 'What I remember about you'. This does NOT forget it "
        "— it only proposes it; the user must explicitly confirm."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "The mem_id of the memory to forget, exactly as shown above.",
            },
        },
        "required": ["memory_id"],
    },
}

_GET_WEATHER_TOOL = {
    "name": "get_weather",
    "description": (
        "Get real, current weather information (temperature, condition, chance of rain) for a "
        "specific place and time period. This is a READ — it executes immediately and needs no "
        "confirmation, unlike the propose_* tools. Only call this when the user's OWN message "
        "explicitly names a location; if no location was given, ask the user which place they mean "
        "instead of calling this — never guess or default one. Only 'now', 'today', 'tonight', and "
        "'tomorrow' are supported — for anything further out, say plainly that it isn't available yet."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "The place name exactly as the user stated it (e.g. a city). Never guess or default this.",
            },
            "horizon": {
                "type": "string",
                "enum": ["now", "today", "tonight", "tomorrow"],
                "description": "Which time period the user is asking about.",
            },
            "response_mode": {
                "type": "string",
                "enum": ["factual", "reason"],
                "description": (
                    "'factual' if the user is asking to be told the weather itself (temperature, rain, "
                    "conditions) — you will receive a ready-made factual reply, verbatim. 'reason' if "
                    "the user is asking for judgment or a recommendation BASED ON the weather (e.g. what "
                    "to wear, whether to go out, whether it's good for an activity) — you will be given "
                    "the real weather facts and asked to answer the original question yourself, grounded "
                    "in them."
                ),
            },
        },
        "required": ["location", "horizon", "response_mode"],
    },
}

# Checkpoint 3.23 — the mandatory no-action escape hatch. Offered
# alongside every propose_*/get_weather tool with tool_choice forced
# to "any" (see orchestrator/service.py's _PRIMARY_CHAT_TOOL_CHOICE),
# this is what makes "any" SAFE rather than a forced guess: the 3.22
# inspection proved directly (real, unmocked provider calls) that
# tool_choice="any" over ONLY the action tools produces garbage/
# sentinel arguments on informational, hypothetical, negated, or
# ambiguous messages, but is 100% correct once a genuine, always-legal
# "just reply" tool exists in the same offered set. Carries no
# execution/completion/action_type/metadata field by construction —
# see RespondWithTextArguments — purely a structured carrier for
# natural-language text. Calling this NEVER creates a ProposedAction
# and NEVER mutates anything; chat_service treats its own `text`
# argument as exactly as untrusted as the old bare-text fallthrough it
# replaces (see _handle_respond_with_text and the 3.21 stale-proposal
# guard, both unchanged in what they distrust, only in where the text
# now arrives from).
_RESPOND_WITH_TEXT_TOOL = {
    "name": "respond_with_text",
    "description": (
        "Reply in natural language WITHOUT taking any action. Every response you give must be "
        "exactly one tool call — this is the tool to call whenever no propose_* action or "
        "get_weather read is actually being requested right now. Use kind=\"answer\" for ordinary "
        "conversation, explanations, informational or hypothetical questions, negations, "
        "statements of past fact, humor, or anything else that isn't a request to change data. "
        "Use kind=\"clarification\" when the user wants a supported write but the target is "
        "ambiguous (e.g. more than one matching task or event) or a required detail is missing — "
        "never guess an id or invent a missing detail merely to avoid asking."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["answer", "clarification"],
                "description": (
                    "'answer' for ordinary conversation/explanation/informational or hypothetical "
                    "questions/negation/past fact. 'clarification' when more information or a "
                    "disambiguated target is needed before a write could safely be proposed."
                ),
            },
            "text": {
                "type": "string",
                "description": "The natural-language reply to show the user, in their own language and tone. Must not be empty.",
            },
        },
        "required": ["kind", "text"],
    },
}

_TOOLS_OFFERED = [
    _PROPOSE_CREATE_TASK_TOOL, _PROPOSE_UPDATE_TASK_TOOL, _PROPOSE_DELETE_TASK_TOOL,
    _PROPOSE_CREATE_EVENT_TOOL, _PROPOSE_UPDATE_EVENT_TOOL, _PROPOSE_DELETE_EVENT_TOOL,
    _PROPOSE_SAVE_MEMORY_TOOL, _PROPOSE_FORGET_MEMORY_TOOL, _GET_WEATHER_TOOL,
    _RESPOND_WITH_TEXT_TOOL,
]

# Checkpoint 5.7H — Manual Gemini Test Mode offers ONLY this one tool,
# never the full _TOOLS_OFFERED catalog. respond_with_text can never
# create a ProposedAction and never mutates anything (see its own
# docstring above) — offering ONLY it means Gemini is structurally
# INCAPABLE of calling propose_create_task/propose_update_task/
# propose_delete_task/propose_create_event/propose_update_event/
# propose_delete_event/propose_save_memory/propose_forget_memory/
# get_weather at all this turn, regardless of what it might otherwise
# attempt — not a trust/prompt-level restriction, a structural one (the
# provider has no declared function to call). This is the deliberate,
# documented scope decision from this checkpoint's own brief, section 8
# ("acceptable to limit Gemini Test to non-mutating conversational
# generation"): the Gemini adapter's tool-calling translation has not
# yet been proven against this exact 9-tool/forced-exactly-one-call
# production contract, so this checkpoint does not improvise that
# proof — it removes the question entirely for the generation step,
# while leaving the EXISTING, unchanged claim-verifier (see
# _candidate_reply_is_safe_to_show) as the real safety net against any
# untrusted completion-claiming prose respond_with_text's own text
# might still contain, exactly as it already is for Claude today.
_GEMINI_TEST_TOOLS_OFFERED = [_RESPOND_WITH_TEXT_TOOL]

# Checkpoint 5.7H, section 12 — the backend-owned provider/model
# mapping; the frontend only ever submits ModelProviderOverride's own
# closed enum member name, never a provider/model string itself. Only
# one override is currently defined; "default" never appears as a key
# here at all — it is handled by passing provider=None/model=None to
# generate_reply (see send_message below), which is what keeps
# "default" byte-for-byte identical to pre-5.7H behavior rather than a
# parallel code path that merely RESOLVES to the same thing.
_MODEL_PROVIDER_OVERRIDE_MAP: dict[str, tuple[str, str]] = {
    "google_gemini_test": ("google_gemini", "gemini-3.1-flash-lite"),
}


class RespondWithTextArguments(BaseModel):
    """Checkpoint 3.23 — the pure shape-validator for respond_with_text,
    the same "validate before trusting" discipline as every domain
    action schema (e.g. TaskCreate for propose_create_task), even
    though this tool creates no ProposedAction and reaches no domain
    module at all. Deliberately minimal and closed: exactly `kind` and
    `text`, nothing else — no execution/completion field, no
    action_type, no free-form metadata dict, by design (see
    _RESPOND_WITH_TEXT_TOOL's own input_schema, which the model is
    already constrained to). A model-supplied `text` that is empty or
    all-whitespace is rejected here — the same "no valid structured
    data to trust" outcome InvalidActionArgumentsError represents for
    every other tool, handled identically by _handle_respond_with_text
    below (falls back to the deterministic _NO_REPLY_FALLBACK_MESSAGE,
    never to any other model-authored text).
    """

    kind: Literal["answer", "clarification"]
    text: str

    @field_validator("text")
    @classmethod
    def _text_must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        return value

# Used by _describe_pending_proposal to tell the model which tool to call
# again for a revision, regardless of which action_type is pending.
_ACTION_TYPE_TOOL_NAMES = {
    "create_task": "propose_create_task",
    "update_task": "propose_update_task",
    "delete_task": "propose_delete_task",
    "create_event": "propose_create_event",
    "update_event": "propose_update_event",
    "delete_event": "propose_delete_event",
    "save_memory": "propose_save_memory",
    "forget_memory": "propose_forget_memory",
}

# Deliberately narrow, closed sets — matched against the ENTIRE message
# (after normalization), not merely present somewhere within it. See
# _classify_narrow_yes_no's docstring for why, and README.md's Known
# Limitations section for the resulting "actually never mind" gap this
# narrowness leaves open.
_CONFIRM_PHRASES = frozenset({
    "yes", "y", "yeah", "yep", "yup", "sure", "confirm", "confirmed", "correct",
    "ok", "okay", "do it", "go ahead", "please do", "sounds good", "yes please",
    "نعم", "ايوه", "أيوه", "تمام", "اكيد", "أكيد", "اه", "آه", "ماشي",
})
_DECLINE_PHRASES = frozenset({
    "no", "n", "nope", "nah", "cancel", "never mind", "nevermind", "don't",
    "dont", "stop", "not now", "no thanks",
    "لا", "الغاء", "إلغاء", "خلاص", "مش دلوقتي",
})
_YES_NO_STRIP_CHARS = " \t\n.,!?؟"

_REJECTED_MESSAGE = "Okay, I won't create that."

# Checkpoint 3.19 — fixes a pre-existing defect (first flagged in 3.13's
# own close-out): a rejected update/delete/memory action was replied to
# with this SAME literal "I won't create that" regardless of what was
# actually rejected, which reads backwards for e.g. a rejected deletion
# ("I won't create" when the user just said no to a REMOVAL). Keyed on
# the pending proposal's own action_type — deterministic, 0 LLM, no new
# ProposedAction status, no schema change. Reuses _is_arabic, the SAME
# existing per-message language-selection mechanism every other
# confirmation renderer in this file already uses, not a new
# localization framework. _REJECTED_MESSAGE above remains the fallback
# for any future/unrecognized action_type, and is also
# _reply_for_confirm_result's own pre-existing (unreachable in
# practice — no code path ever constructs ConfirmResult(outcome=
# "rejected", ...); actions_service.reject() is a separate function
# whose only caller already renders directly from the table below)
# defensive default, left untouched.
_REJECTED_MESSAGE_EN_BY_ACTION_TYPE = {
    "create_task": "Okay, I won't create that.",
    "update_task": "Okay, I won't make that change.",
    "delete_task": "Okay, I won't remove that.",
    "create_event": "Okay, I won't add that.",
    "update_event": "Okay, I won't make that change.",
    "delete_event": "Okay, I won't remove that.",
    "save_memory": "Okay, I won't remember that.",
    "forget_memory": "Okay, I won't forget that.",
}
_REJECTED_MESSAGE_AR_BY_ACTION_TYPE = {
    "create_task": "تمام، مش هضيفها.",
    "update_task": "تمام، مش هغيرها.",
    "delete_task": "تمام، مش هشيلها.",
    "create_event": "تمام، مش هضيفه.",
    "update_event": "تمام، مش هغيره.",
    "delete_event": "تمام، مش هشيله.",
    "save_memory": "تمام، مش هفتكرها.",
    "forget_memory": "تمام، مش هنساها.",
}
_NOTHING_PENDING_MESSAGE = "I don't have a pending proposal to confirm right now."

# Checkpoint 3.21 — fixes a pre-existing defect flagged by the 3.20
# inspection: this single, generic string was used for EVERY action
# type's execution failure, so a failed delete_event confirmation used
# to say "Something went wrong while creating that task." Keyed on the
# row's own action_type, read by the caller BEFORE confirm_and_execute
# runs (the same pending.action_type send_message already has in
# scope) — the exact same deterministic, 0-LLM, no-new-status pattern
# as 3.19's _REJECTED_MESSAGE_*_BY_ACTION_TYPE tables. _EXECUTION_FAILED_MESSAGE
# remains the fallback for any future/unrecognized action_type.
_EXECUTION_FAILED_MESSAGE = "Something went wrong while creating that task — could you say yes again?"
_EXECUTION_FAILED_MESSAGE_EN_BY_ACTION_TYPE = {
    "create_task": "I couldn't add that just now. You can confirm again to retry.",
    "update_task": "I couldn't make that change just now. You can confirm again to retry.",
    "delete_task": "I couldn't remove that just now. You can confirm again to retry.",
    "create_event": "I couldn't add that just now. You can confirm again to retry.",
    "update_event": "I couldn't make that change just now. You can confirm again to retry.",
    "delete_event": "I couldn't remove that just now. You can confirm again to retry.",
    "save_memory": "I couldn't save that just now. You can confirm again to retry.",
    "forget_memory": "I couldn't forget that just now. You can confirm again to retry.",
}
_EXECUTION_FAILED_MESSAGE_AR_BY_ACTION_TYPE = {
    "create_task": "معرفتش أضيفها دلوقتي. تقدر تأكد تاني وهحاول تاني.",
    "update_task": "معرفتش أغيرها دلوقتي. تقدر تأكد تاني وهحاول تاني.",
    "delete_task": "معرفتش أشيلها دلوقتي. تقدر تأكد تاني وهحاول تاني.",
    "create_event": "معرفتش أضيفه دلوقتي. تقدر تأكد تاني وهحاول تاني.",
    "update_event": "معرفتش أغيره دلوقتي. تقدر تأكد تاني وهحاول تاني.",
    "delete_event": "معرفتش أشيله دلوقتي. تقدر تأكد تاني وهحاول تاني.",
    "save_memory": "معرفتش أحفظها دلوقتي. تقدر تأكد تاني وهحاول تاني.",
    "forget_memory": "معرفتش أنساها دلوقتي. تقدر تأكد تاني وهحاول تاني.",
}

# Checkpoint 3.21 — same discipline as the rejection-copy fix: a proposal
# tool call whose arguments/target failed deterministic validation
# never created a ProposedAction and never executed anything, so
# model_text accompanying that call (which the model may have written
# BEFORE knowing its own call would fail — e.g. "Sure, deleting that
# now!") is not authoritative and must never be preferred over this
# deterministic reply. MODEL TEXT IS NEVER EXECUTION EVIDENCE.
_INVALID_PROPOSAL_FALLBACK_MESSAGE = (
    "I couldn't prepare that change safely. Please clarify what you want changed."
)
_INVALID_PROPOSAL_FALLBACK_MESSAGE_AR = "مقدرتش أجهز التغيير ده بشكل آمن. وضّحلي تقصد إيه بالظبط."

# Checkpoint 3.21 — the deterministic reply for the narrow, structural
# "stale yes/no" population: see _reply_for_stale_proposal_text's own
# docstring below for exactly which turns reach this.
_STALE_PROPOSAL_NOT_EXECUTED_MESSAGE_EN = (
    "That change wasn't executed. If you still want it, I can prepare it again."
)
_STALE_PROPOSAL_NOT_EXECUTED_MESSAGE_AR = "التغيير ده ما اتنفذش. لو لسه عايزه، أقدر أجهزهولك تاني."

# Checkpoint 3.25 — the deterministic fail-closed reply used when the
# independent mutation-claim verifier either certifies that a
# respond_with_text candidate falsely claims a completed BAZRA
# mutation, or the verification itself could not be trusted (provider
# failure, malformed contract) — both collapse to this same reply; see
# _candidate_reply_is_safe_to_show's own docstring.
_UNVERIFIED_MUTATION_CLAIM_MESSAGE_EN = (
    "I haven't made that change. If you want, I can prepare it for confirmation."
)
_UNVERIFIED_MUTATION_CLAIM_MESSAGE_AR = "أنا ما عملتش التغيير ده. لو عايز، أقدر أجهزهولك عشان تأكده."

_NO_REPLY_FALLBACK_MESSAGE = "Sorry, I don't have a reply for that."

# Checkpoint 5.1 — honest, deterministic degradation wording for a
# primary-generation model-call failure (ChatModelCallFailed). NEVER
# persisted as a ChatMessage (see ChatModelCallFailed's own docstring
# for why: conversation history must contain actual conversation, not
# a transport/service error masquerading as BAZRA speech) — these
# strings only ever reach the user via the HTTP error detail's own
# "message" field, for the frontend to display in its existing
# transient error UI, exactly like _UNVERIFIED_MUTATION_CLAIM_MESSAGE_*
# reaches the user via a real persisted reply. Deliberately just TWO
# wordings, not one per ModelFailureCategory — distinguishing "try
# again shortly" (only honest for a category that might plausibly
# resolve itself) from "not available right now" (everything else,
# including categories this code cannot promise will resolve on retry)
# is the only distinction worth making to the user; see
# model_router/schemas.py's own ModelFailureCategory retryability
# grouping, mirrored by _TRANSIENT_FAILURE_CATEGORIES below.
_MODEL_DEGRADATION_TRANSIENT_MESSAGE_EN = (
    "I can't reach the model right now. Your message is saved — try again in a bit."
)
_MODEL_DEGRADATION_TRANSIENT_MESSAGE_AR = "مش قادر أوصل لمحرك الذكاء دلوقتي. رسالتك محفوظة، جرّب تاني بعد شوية."

_MODEL_DEGRADATION_UNAVAILABLE_MESSAGE_EN = (
    "The model isn't available right now. Your message is saved, but I couldn't finish a reply."
)
_MODEL_DEGRADATION_UNAVAILABLE_MESSAGE_AR = "محرك الذكاء مش متاح دلوقتي. رسالتك محفوظة، لكن مقدرتش أكمل الرد."

# rate_limited/timeout/connection/provider_unavailable are the only
# categories where "try again shortly" is an honest thing to say — see
# ModelFailureCategory's own "POTENTIALLY TRANSIENT" grouping. Every
# other category (authentication, billing_or_credits, invalid_request,
# model_unavailable, unparseable_response, unknown_provider_error) gets
# the plain "not available" wording, which promises nothing about
# whether retrying will help — never falsely reassuring for a
# non-retryable or genuinely unknown cause.
_TRANSIENT_FAILURE_CATEGORIES = frozenset({"rate_limited", "timeout", "connection", "provider_unavailable"})

_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿ]")

# Type labels for the context section shown to the model — INFERENCE is
# explicitly marked tentative in the label ITSELF, not just in the
# system prompt, so the distinction survives even if only this line is
# ever attended to.
_MEMORY_TYPE_CONTEXT_LABELS = {
    "FACT": "FACT",
    "PREFERENCE": "PREFERENCE",
    "GOAL": "GOAL",
    "INFERENCE": "INFERENCE (tentative, not confirmed as fact)",
}

# Bilingual confirmation labels, selected the same way _is_arabic already
# selects between the two create_task confirmation templates.
_MEMORY_TYPE_CONFIRM_LABELS_AR = {
    "FACT": "المعلومة دي",
    "PREFERENCE": "التفضيل ده",
    "GOAL": "الهدف ده",
    "INFERENCE": "الاستنتاج ده (مش مؤكد منك)",
}
_MEMORY_TYPE_CONFIRM_LABELS_EN = {
    "FACT": "this fact",
    "PREFERENCE": "this preference",
    "GOAL": "this goal",
    "INFERENCE": "this tentative inference (not confirmed by you)",
}

# Checkpoint 3.7: the 8 fixed condition buckets weather/service.py
# normalizes every Open-Meteo WMO code into, mapped to bilingual
# display phrasing — same convention as the memory-type labels above.
_WEATHER_CONDITION_LABELS_EN = {
    "clear": "clear",
    "partly_cloudy": "partly cloudy",
    "cloudy": "cloudy",
    "fog": "foggy",
    "drizzle": "light drizzle",
    "rain": "rain",
    "snow": "snow",
    "thunderstorm": "thunderstorms",
}
_WEATHER_CONDITION_LABELS_AR = {
    "clear": "صحو",
    "partly_cloudy": "غائم جزئيًا",
    "cloudy": "غائم",
    "fog": "شبورة",
    "drizzle": "رذاذ خفيف",
    "rain": "مطر",
    "snow": "تلج",
    "thunderstorm": "عواصف رعدية",
}

# CC BY 4.0 attribution — required by Open-Meteo's licence for the free
# endpoint this project uses; see weather/service.py's module docstring.
# Fixed/untranslated per the Checkpoint 3.7 decision (official wording,
# not a conversational phrase) — appended only when real weather data
# was actually displayed, never on an ask-for-location or failure reply.
_WEATHER_ATTRIBUTION_LINE = "Weather data by Open-Meteo.com (https://open-meteo.com/)"

_WEATHER_MISSING_LOCATION_FALLBACK_MESSAGE = "Which place would you like the weather for?"


class MessageTooLongError(Exception):
    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        super().__init__(f"Message too long (max {max_chars} characters)")


class ChatModelCallFailed(Exception):
    """category/degradation_message (Checkpoint 5.1) let router.py build
    an honest HTTP error detail without needing its own access to
    `content` (the triggering message, needed to pick Arabic/English
    wording) or to orchestrator_service's own exception types — both
    are computed once, here, where `content` is already in scope (see
    send_message's own raise site)."""

    def __init__(self, user_message_id: int, category: str, degradation_message: str):
        self.user_message_id = user_message_id
        self.category = category
        self.degradation_message = degradation_message
        super().__init__("Model call failed")


class InvalidTimezoneError(Exception):
    def __init__(self, timezone_name: str):
        self.timezone_name = timezone_name
        super().__init__(f"Invalid timezone: {timezone_name}")


# Checkpoint 3.9: an ordinary structured log line for each of the three
# EXISTING zero-LLM branches in send_message (write-intent decline,
# pending-proposal confirm, pending-proposal reject) — see
# docs/architecture/bazra-deterministic-routing.md. Deliberately just a
# log, never a new AiTrace row or a generated correlation_id: there is
# no model call here to correlate, and inventing one would misrepresent
# what happened. chat_message_id (the real, already-persisted user
# ChatMessage.id) is the natural, zero-new-generation identifier for
# "which turn was this" — not a fabricated correlation value.
def _log_deterministic_route(route: str, chat_message_id: int) -> None:
    logger.info("chat: deterministic_route=%s chat_message_id=%s", route, chat_message_id)


def _acquire_conversation_lock(db: Session, space_id: int, user_id: int) -> None:
    """Checkpoint 3.17 — a transaction-scoped Postgres advisory lock
    (pg_advisory_xact_lock), automatically released at this session's
    NEXT commit or rollback, no manual unlock call needed or possible
    to forget. No migration, no new table, no new column — this is the
    standard Postgres primitive for serializing otherwise-unrelated
    transactions against one logical key with zero schema footprint.

    Why this exists: the 3.17 inspection's own forced-interleaving
    experiment proved that a plain `ChatMessage.id BETWEEN` adjacency
    query is UNSAFE under real concurrent access to the same (space_id,
    user_id) — Postgres allocates a sequence-generated id at INSERT
    time, not at COMMIT time, so a concurrently-inserted row with a
    LOWER id can remain completely invisible to a reader for as long as
    its own transaction stays open, letting that reader wrongly
    conclude "nothing intervened" for a message that, once it lands,
    would have counted as an intervening turn. Acquiring this lock
    before appending ANY message for a given (space_id, user_id), and
    holding it through the adjacency decision that follows, forces two
    concurrent requests for the SAME conversation (two tabs, a
    double-submit) to be fully serialized exactly where it matters: the
    second can't even get an id until the first's entire critical
    section — lock, insert, adjacency check, commit — has completed.

    Acquired UNCONDITIONALLY for every message, not just ones that
    might be answering a pending proposal — a message whose own routing
    doesn't care about any proposal at all might still be the exact
    "intervening turn" a CONCURRENT request's own adjacency check
    depends on seeing correctly.
    """
    lock_key = f"{space_id}:{user_id}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})


def acquire_conversation_lock(db: Session, space_id: int, user_id: int) -> None:
    """Checkpoint 4.5e — a thin public re-export of the exact same
    (space_id, user_id) advisory-lock primitive this module already
    uses for its own conversation turns (see _acquire_conversation_lock
    above) — reused as-is, never a second lock namespace, by
    attention/surfacing.py's own finalization phase. A proactive
    APP_OPENED opening and an ordinary chat turn for the same
    (space_id, user_id) are, by design, serialized against the exact
    same key."""
    _acquire_conversation_lock(db, space_id, user_id)


def record_user_message(
    db: Session, space_id: int, user_id: int, content: str, commit: bool = True
) -> ChatMessage:
    """commit=False (Checkpoint 3.17) lets this message be flushed (its
    id populated) without ending the transaction — needed so the
    conversation advisory lock acquired just before this call (see
    _acquire_conversation_lock) stays held through the adjacency
    decision that follows, in the SAME transaction, rather than being
    released the instant this one insert commits."""
    message = ChatMessage(space_id=space_id, user_id=user_id, role="user", content=content)
    db.add(message)
    if commit:
        db.commit()
        db.refresh(message)
    else:
        db.flush()
    return message


def record_assistant_message(
    db: Session, space_id: int, user_id: int, content: str, commit: bool = True
) -> ChatMessage:
    """commit=False (Checkpoint 3.3) lets this message be created
    atomically together with the ProposedAction row it describes — the
    same commit=False escape hatch used by tasks.create_task and
    actions.create_pending_action, so send_message can flush this
    message (populating its id, needed as source_chat_message_id) and
    the new proposal together, then commit both in one transaction.
    """
    if len(content) > _MAX_ASSISTANT_MESSAGE_CHARS:
        content = content[:_MAX_ASSISTANT_MESSAGE_CHARS] + "… [truncated]"
    message = ChatMessage(space_id=space_id, user_id=user_id, role="assistant", content=content)
    db.add(message)
    if commit:
        db.commit()
        db.refresh(message)
    else:
        db.flush()
    return message


def list_recent_messages(
    db: Session, space_id: int, user_id: int, limit: int = _MAX_HISTORY_MESSAGES
) -> list[ChatMessage]:
    """Filters by BOTH space_id and user_id explicitly — defense in
    depth, not relying solely on whatever the Space->User ownership fix
    enforces elsewhere. Selects the most recent `limit` by created_at
    DESC, then reverses to chronological order for presentation —
    selection criterion and presentation order are different axes.
    """
    rows = (
        db.execute(
            scoped_query(ChatMessage, space_id)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def _trim_to_char_budget(messages: list[ChatMessage], max_chars: int) -> list[HistoryTurn]:
    """Selects from newest to oldest, stopping once the budget is
    reached. If even the single newest message alone exceeds the
    budget, it is truncated (kept, clipped to its most recent
    `remaining` characters) rather than excluded entirely — some recent
    context is judged more useful than none. This guarantees the
    combined length of everything returned is ALWAYS <= max_chars, with
    no exception case (the original design's "and kept" condition let an
    oversized single message through unclipped — fixed here).
    """
    total = 0
    kept: list[HistoryTurn] = []
    for message in reversed(messages):  # newest first
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(message.content) > remaining:
            kept.append(HistoryTurn(role=message.role, content=message.content[-remaining:]))
            break
        total += len(message.content)
        kept.append(HistoryTurn(role=message.role, content=message.content))
    return list(reversed(kept))


def _format_utc_offset(local_dt: datetime) -> str:
    """Numeric, colon-separated UTC offset (e.g. "+03:00", "-05:00"),
    computed fresh from the timezone-aware datetime's own utcoffset() —
    never a fixed value, so a DST-observing zone reports whichever
    offset is actually in effect for THIS instant, not a year-round
    constant. strftime's own "%z" produces "+0300" (no colon, and not
    every ISO 8601 consumer expects that shape) — this is deliberately
    hand-formatted instead.
    """
    offset = local_dt.utcoffset()
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _compute_current_datetime_local(timezone_name: str) -> str:
    """The server computes its own trustworthy current instant
    (datetime.now(timezone.utc)) and only trusts the CLIENT for which
    IANA zone to render it in — never for the instant itself. An
    unresolvable zone name is a 422, never a silent UTC fallback (a
    silent fallback would make the model confidently resolve "tomorrow"
    against the wrong day with no visible signal that anything was
    wrong).

    Checkpoint 3.15: also exposes the IANA timezone name and the
    numeric UTC offset EXPLICITLY, as their own labeled facts — the
    3.14 inspection found the model previously received only a local
    wall-clock string with an abbreviated zone code (e.g. "EEST"),
    with no explicit IANA name or numeric offset to reason from, which
    is exactly what a timezone-aware starts_at/ends_at needs to be
    produced reliably. The offset is computed fresh from THIS instant's
    own utcoffset() (see _format_utc_offset) — never a fixed value —
    so it is correct across a DST transition without any special-casing
    here; timezone_name is the caller's own already-resolved request
    parameter, never inferred or guessed from anything else.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTimezoneError(timezone_name) from exc
    local_now = datetime.now(timezone.utc).astimezone(zone)
    return (
        f"Current local datetime: {local_now.strftime('%A, %Y-%m-%d %H:%M')}\n"
        f"Timezone: {timezone_name}\n"
        f"UTC offset: {_format_utc_offset(local_now)}"
    )


def _classify_narrow_yes_no(content: str) -> str | None:
    """Deliberately NARROW: matches only when the ENTIRE message (after
    trimming whitespace/punctuation and lowercasing) is one of a small
    closed set of confirm/decline phrases — not merely contains one.
    Returns "yes", "no", or None (not a recognized bare confirm/decline
    — falls through to the Orchestrator branch instead).

    A message that mixes a decline into a longer sentence ("actually,
    never mind, let's talk about something else") intentionally does
    NOT match here — there's no reliable way to distinguish that from
    an ordinary sentence that happens to contain the word "no" without
    a much heavier classifier. This is the recorded "actually never
    mind" known limitation (see README.md): such a message instead
    reaches the Orchestrator with the pending proposal described in
    context, but nothing code-level forces the model to treat it as a
    cancellation, so the proposal may simply sit pending until it
    expires.
    """
    normalized = content.strip(_YES_NO_STRIP_CHARS).lower()
    if normalized in _CONFIRM_PHRASES:
        return "yes"
    if normalized in _DECLINE_PHRASES:
        return "no"
    return None


def _describe_pending_proposal(pending, adjacent: bool) -> str:
    """Checkpoint 3.17: `adjacent` (see is_still_conversationally_adjacent)
    picks between two truthful descriptions of the SAME underlying
    'pending' database status — never a new ProposedAction status, just
    different model-facing text for the same stored fact. When
    non-adjacent, the model must not claim the proposal was acted on
    (nothing happened to it — it is not expired, rejected, or deleted)
    and must be told the correct next step: propose it again, fresh.
    """
    tool_name = _ACTION_TYPE_TOOL_NAMES.get(pending.action_type, pending.action_type)
    if adjacent:
        return (
            f"A '{pending.action_type}' proposal is awaiting the user's yes/no "
            f"confirmation (proposed just now, expires {pending.expires_at.isoformat()}). "
            f"Proposed arguments: {pending.arguments}. If the user's message is a "
            f"revision request rather than a plain yes/no, call {tool_name} "
            f"again with the corrected arguments — this replaces the pending proposal "
            f"above rather than creating an additional one."
        )
    return (
        f"A '{pending.action_type}' proposal (arguments: {pending.arguments}) is still "
        f"stored, but other conversation has happened since it was proposed, so a bare "
        f"\"yes\"/\"no\" can no longer confirm or reject it automatically — it is NOT "
        f"expired, rejected, or removed; nothing has happened to it either way, and you "
        f"must never claim it was created, changed, or removed. If the user now clearly "
        f"wants to proceed with it, call {tool_name} again with the intended arguments — "
        f"this creates a fresh proposal the user can then confirm normally."
    )


# Checkpoint 3.11 — the authoritative negative counterpart to
# _describe_pending_proposal above. Injected whenever get_latest_pending
# returns None, for ANY reason (never proposed, confirmed, rejected,
# superseded, or expired — all collapse into the same simple, true-right-now
# fact, deterministically, with no new query beyond the existing `pending`
# lookup send_message already performs every turn). This directly closes the
# gap the 3.10 live-gate observation surfaced: an earlier assistant message
# that merely LOOKS like a proposal offer, still visible in raw conversation
# history, must never be mistaken for something currently confirmable. It
# does NOT ask the model to ignore, forget, or stop discussing that history —
# only to distinguish "not currently actionable" from "irrelevant."
_NO_ACTIVE_PROPOSAL_NOTE = (
    "No ProposedAction is currently pending confirmation. If an earlier "
    "assistant message in this conversation offered to create or change "
    "something and the user never explicitly confirmed it, that offer is no "
    "longer active — do not treat a new message as confirming it unless the "
    "user is clearly and specifically asking for it again, in which case "
    "propose it again as a fresh action. You may still refer to, explain, or "
    "discuss earlier parts of the conversation normally."
)


def _is_arabic(text: str) -> bool:
    """Deterministic, script-based, no model call — same style as
    write_intent.py's own bilingual pattern lists. Checked against the
    CURRENT triggering message only (not full history): the simplest,
    most direct signal of what language the user is writing in right
    now."""
    return bool(_ARABIC_SCRIPT_RE.search(text))


def _reply_for_model_unavailable(category: str, user_message: str) -> str:
    """Checkpoint 5.1 — the ONLY place this checkpoint decides the
    user-facing wording for a primary-generation model-call failure.
    Never persisted — see ChatModelCallFailed's own docstring and
    _MODEL_DEGRADATION_*'s own comment above for why. Picked the same
    way every other bilingual BAZRA-authored string in this module is
    (_is_arabic on the triggering message), so a user who has been
    writing in Arabic throughout never suddenly sees an English
    transport error."""
    is_transient = category in _TRANSIENT_FAILURE_CATEGORIES
    if _is_arabic(user_message):
        return _MODEL_DEGRADATION_TRANSIENT_MESSAGE_AR if is_transient else _MODEL_DEGRADATION_UNAVAILABLE_MESSAGE_AR
    return _MODEL_DEGRADATION_TRANSIENT_MESSAGE_EN if is_transient else _MODEL_DEGRADATION_UNAVAILABLE_MESSAGE_EN


def _format_due_at_local(due_at_iso: str, timezone_name: str) -> str:
    """due_at_iso is always a timezone-aware ISO string here (that's
    what TaskCreate.model_dump(mode="json") produces) — converted via
    real zoneinfo arithmetic to the SAME timezone this request already
    resolved current_datetime_local against, never trusting the
    embedded offset blindly. DD/MM/YYYY HH:MM, 24-hour: deterministic,
    no locale/strftime-month-name dependency, and matches the Home UI's
    own existing numeric date display convention.
    """
    local_dt = datetime.fromisoformat(due_at_iso).astimezone(ZoneInfo(timezone_name))
    return local_dt.strftime("%d/%m/%Y %H:%M")


def _render_create_task_confirmation(arguments: dict, timezone_name: str, user_message: str) -> str:
    """Checkpoint 3.3 fix: the confirmation shown to the user is
    rendered ONLY from these already-validated arguments — the exact
    same dict create_pending_action stores and confirm_and_execute
    later executes — never from the model's own free-form reply text.
    That text may omit or misstate details (observed in the first live
    verification run); this function can't, since it has no access to
    it at all.
    """
    title = arguments["title"]
    due_at = arguments.get("due_at")
    arabic = _is_arabic(user_message)

    # Checkpoint 4.1 — "normal" is the silent, invisible default on
    # CREATE (deliberately different from UPDATE — see
    # _render_update_task_confirmation's own comment for why an
    # explicit-normal UPDATE is NOT silent the same way): arguments.get
    # returns None when the model omitted priority entirely (exclude_unset
    # drops it from the stored/validated dict), and "normal" is excluded
    # here too even if the model explicitly named it, since a fresh
    # task's own priority defaulting to normal is unremarkable either way.
    priority = arguments.get("priority")
    priority_clause_en = ""
    priority_clause_ar = ""
    if priority == "high":
        priority_clause_en = " as high priority"
        priority_clause_ar = " بأولوية عالية"
    elif priority == "low":
        priority_clause_en = " as low priority"
        priority_clause_ar = " بأولوية منخفضة"

    if due_at:
        when = _format_due_at_local(due_at, timezone_name)
        if arabic:
            return f'هضيف مهمة "{title}"{priority_clause_ar} بتاريخ {when}. أأكدها؟'
        return f'I\'ll add the task "{title}"{priority_clause_en} for {when}. Shall I go ahead?'

    if arabic:
        return f'هضيف مهمة "{title}"{priority_clause_ar}. أأكدها؟'
    return f'I\'ll add the task "{title}"{priority_clause_en}. Shall I go ahead?'


def _render_update_task_confirmation(task_title: str, changes: dict, timezone_name: str, user_message: str) -> str:
    """Checkpoint 3.10 — same discipline as _render_create_task_confirmation:
    built ONLY from the already-validated `changes` (proposal.arguments
    minus task_id) and the task's own CURRENT title (re-fetched from the
    database, never restated by the model) — never from the model's own
    free-form reply text. `changes` only ever contains fields the model
    actually named (see actions_service.validate_arguments's
    exclude_unset note), so this never describes a change that isn't
    really being proposed.
    """
    arabic = _is_arabic(user_message)
    fragments_en: list[str] = []
    fragments_ar: list[str] = []

    if "status" in changes:
        if changes["status"] == "done":
            fragments_en.append("mark it as done")
            fragments_ar.append("أعلّمها إنها خلصت")
        else:
            fragments_en.append("reopen it")
            fragments_ar.append("أرجعها مفتوحة")
    if "due_at" in changes:
        if changes["due_at"] is None:
            fragments_en.append("clear its due date")
            fragments_ar.append("أشيل تاريخها")
        else:
            when = _format_due_at_local(changes["due_at"], timezone_name)
            fragments_en.append(f"reschedule it to {when}")
            fragments_ar.append(f"أغير ميعادها لـ {when}")
    if "priority" in changes:
        # Checkpoint 4.1 — deliberately UNLIKE create's own silent-normal
        # rule: an UPDATE explicitly naming priority="normal" is still a
        # real, visible change (e.g. "make it normal priority again"),
        # since "priority" only ever appears in `changes` at all when the
        # model explicitly proposed changing it — never merely because a
        # task happens to already be normal.
        _priority_labels_en = {"low": "low priority", "normal": "normal priority", "high": "high priority"}
        _priority_labels_ar = {"low": "أولوية منخفضة", "normal": "أولوية عادية", "high": "أولوية عالية"}
        fragments_en.append(f"set it to {_priority_labels_en[changes['priority']]}")
        fragments_ar.append(f"أخليها {_priority_labels_ar[changes['priority']]}")
    if "title" in changes:
        fragments_en.append(f'rename it to "{changes["title"]}"')
        fragments_ar.append(f'أغير اسمها لـ "{changes["title"]}"')
    if "description" in changes:
        fragments_en.append("update its description")
        fragments_ar.append("أعدل وصفها")
    if "life_area_id" in changes:
        fragments_en.append("move it to a different life area")
        fragments_ar.append("أنقلها لمجال حياة تاني")

    if arabic:
        joined = " و".join(fragments_ar)
        return f'هـ{joined} — "{task_title}". أأكدها؟'
    joined = " and ".join(fragments_en)
    return f'I\'ll {joined} — "{task_title}". Shall I go ahead?'


def _render_delete_task_confirmation(task_title: str, user_message: str) -> str:
    """Checkpoint 3.13 — same discipline as _render_update_task_confirmation:
    built ONLY from the task's own CURRENT title (re-fetched from the
    database via _require_existing_task, never restated by the model).

    Deliberately consequence-aware (Option B from the 3.13 design
    report, approved as product decision 4): BAZRA has no restore/
    unarchive surface anywhere in the app today (verified directly
    across backend, REST, frontend, and Chat before this checkpoint), so
    saying nothing about recoverability — or saying something that
    implies it exists — would be misleading. "Remove" is used
    throughout, never "delete" (overstates permanence the DB doesn't
    have) or "archive"/"restorable" (implies a recovery surface that
    doesn't exist) — see this checkpoint's design report, Parts E/F.
    """
    if _is_arabic(user_message):
        return f'هشيل "{task_title}" من مهامك — مفيش طريقة أرجعها دلوقتي في بذرة. أنفذ؟'
    return f'I can remove "{task_title}" from your tasks — there\'s no way to bring it back in BAZRA right now. Shall I go ahead?'


# Deterministic, locale-independent month names (Checkpoint 3.15) —
# NOT strftime's %B, which depends on the running system/container's
# locale data being installed and correctly configured; an explicit,
# fixed list guarantees the SAME confirmation text everywhere this code
# runs, exactly the same "no locale/strftime dependency" discipline
# _format_due_at_local already applies by avoiding month names entirely.
_MONTH_NAMES_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTH_NAMES_AR = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]


def _format_event_date_local(local_dt: datetime, arabic: bool) -> str:
    month_name = (_MONTH_NAMES_AR if arabic else _MONTH_NAMES_EN)[local_dt.month - 1]
    return f"{local_dt.day} {month_name}" if arabic else f"{month_name} {local_dt.day}"


def _format_event_time_local(local_dt: datetime, arabic: bool) -> str:
    """12-hour clock — deliberately different from _format_due_at_local's
    24-hour convention: this checkpoint's own approved confirmation
    example ("11:00 AM–12:00 PM" / natural Arabic) calls for a more
    conversational rendering than Task's existing numeric style, which
    this function does not touch or share.
    """
    hour = local_dt.hour % 12
    hour = 12 if hour == 0 else hour
    period = ("ص" if local_dt.hour < 12 else "م") if arabic else ("AM" if local_dt.hour < 12 else "PM")
    return f"{hour}:{local_dt.minute:02d} {period}"


def _render_create_event_confirmation(
    arguments: dict, timezone_name: str, user_message: str, life_area_name: str | None,
) -> str:
    """Checkpoint 3.15 — same discipline as every other _render_*_confirmation:
    built ONLY from these already-validated arguments (starts_at/ends_at
    are always timezone-aware ISO strings here, guaranteed by
    ProposedCalendarEventCreate's own validator) plus the Life Area's
    CURRENT name (resolved by the caller, never restated by the model) —
    never from the model's own free-form reply text. Converts to the
    SAME IANA timezone this request already resolved current_datetime_
    local against — never a hardcoded zone, never the raw UTC instant
    shown to the user.
    """
    title = arguments["title"]
    zone = ZoneInfo(timezone_name)
    starts_local = datetime.fromisoformat(arguments["starts_at"]).astimezone(zone)
    arabic = _is_arabic(user_message)

    date_str = _format_event_date_local(starts_local, arabic)
    start_time_str = _format_event_time_local(starts_local, arabic)

    ends_at_iso = arguments.get("ends_at")
    if ends_at_iso:
        ends_local = datetime.fromisoformat(ends_at_iso).astimezone(zone)
        end_time_str = _format_event_time_local(ends_local, arabic)
        time_str = f"{start_time_str}–{end_time_str}"
    else:
        time_str = start_time_str

    area_clause_en = f", under {life_area_name}" if life_area_name else ""
    area_clause_ar = f"، تحت {life_area_name}" if life_area_name else ""

    if arabic:
        return f'هضيف "{title}" يوم {date_str}، الساعة {time_str}{area_clause_ar}. أضيفها؟'
    return f'I can add "{title}" on {date_str}, {time_str}{area_clause_en}. Add it?'


def _render_update_event_confirmation(
    event, changes: dict, timezone_name: str, user_message: str, life_area_name: str | None,
) -> str:
    """Checkpoint 3.18 — same discipline as _render_update_task_confirmation:
    built ONLY from the event's own CURRENT state (re-fetched via
    _require_existing_event, never restated by the model) and the
    already-validated `changes` (proposal.arguments minus event_id) —
    never from the model's own free-form reply text. A time-related
    change (starts_at and/or ends_at) shows the old range and the new
    range so a model misunderstanding is visible before the write;
    other changes get a plain verb-phrase fragment, matching
    _render_update_task_confirmation's own combining style.
    """
    arabic = _is_arabic(user_message)
    zone = ZoneInfo(timezone_name)
    fragments_en: list[str] = []
    fragments_ar: list[str] = []

    if "starts_at" in changes:
        old_start_local = event.starts_at.astimezone(zone)
        old_range = _format_event_time_local(old_start_local, arabic)
        if event.ends_at is not None:
            old_range += f"–{_format_event_time_local(event.ends_at.astimezone(zone), arabic)}"

        new_start_local = datetime.fromisoformat(changes["starts_at"]).astimezone(zone)
        new_range = _format_event_time_local(new_start_local, arabic)
        new_ends_iso = changes.get("ends_at")
        if new_ends_iso:
            new_ends_local = datetime.fromisoformat(new_ends_iso).astimezone(zone)
            new_range += f"–{_format_event_time_local(new_ends_local, arabic)}"
        new_date_str = _format_event_date_local(new_start_local, arabic)

        if arabic:
            fragments_ar.append(f"أنقلها من {old_range} لـ {new_range} يوم {new_date_str}")
        else:
            fragments_en.append(f"move it from {old_range} to {new_range} on {new_date_str}")
    elif "ends_at" in changes:
        new_ends_iso = changes["ends_at"]
        if new_ends_iso:
            new_end_str = _format_event_time_local(datetime.fromisoformat(new_ends_iso).astimezone(zone), arabic)
            fragments_en.append(f"have it finish at {new_end_str}")
            fragments_ar.append(f"أخليها تخلص الساعة {new_end_str}")
        else:
            fragments_en.append("clear its end time")
            fragments_ar.append("أشيل ميعاد انتهاءها")

    if "title" in changes:
        fragments_en.append(f'rename it to "{changes["title"]}"')
        fragments_ar.append(f'أغير اسمها لـ "{changes["title"]}"')
    if "description" in changes:
        fragments_en.append("update its description")
        fragments_ar.append("أعدل وصفها")
    if "life_area_id" in changes:
        if life_area_name:
            fragments_en.append(f"assign it to {life_area_name}")
            fragments_ar.append(f"أحطها تحت {life_area_name}")
        else:
            fragments_en.append("move it to a different life area")
            fragments_ar.append("أنقلها لمجال حياة تاني")

    if arabic:
        joined = " و".join(fragments_ar)
        return f'هـ{joined} — "{event.title}". أنفذ؟'
    joined = " and ".join(fragments_en)
    return f'I can {joined} — "{event.title}". Make that change?'


def _render_delete_event_confirmation(event, timezone_name: str, user_message: str) -> str:
    """Checkpoint 3.19 — same discipline as _render_delete_task_confirmation:
    built ONLY from the event's own CURRENT state (re-fetched via
    _require_existing_event, never restated by the model). Names the
    event's local date/time so the user can catch a misidentification
    before confirming, and is deliberately consequence-aware — no
    restore/unarchive surface exists for CalendarEvent any more than it
    does for Task (verified directly, same as 3.13's own finding) —
    without ever implying this reaches beyond BAZRA's own local
    calendar (no attendee/external-calendar wording anywhere).
    """
    arabic = _is_arabic(user_message)
    zone = ZoneInfo(timezone_name)
    starts_local = event.starts_at.astimezone(zone)
    date_str = _format_event_date_local(starts_local, arabic)
    time_str = _format_event_time_local(starts_local, arabic)

    if arabic:
        return (
            f'أقدر أشيل "{event.title}" من تقويم بذرة — يوم {date_str} الساعة {time_str}، '
            f'ومفيش طريقة أرجعه دلوقتي. أشيله؟'
        )
    return (
        f'I can remove "{event.title}" from your BAZRA calendar — it\'s {date_str} at {time_str}, '
        f'and there\'s no way to bring it back right now. Remove it?'
    )


def _render_save_memory_confirmation(arguments: dict, user_message: str) -> str:
    """Same discipline as _render_create_task_confirmation: built ONLY
    from the validated arguments that will be stored/executed, never
    from model prose — and explicitly labels the memory's TYPE in the
    confirmation itself, not just internally in storage, so an
    INFERENCE is visibly tentative to the user before they confirm it,
    not just after."""
    labels = _MEMORY_TYPE_CONFIRM_LABELS_AR if _is_arabic(user_message) else _MEMORY_TYPE_CONFIRM_LABELS_EN
    label = labels[arguments["type"]]
    content = arguments["content"]
    if _is_arabic(user_message):
        return f'هحتفظ ب{label}: "{content}". أأكدها؟'
    return f'I\'ll remember {label}: "{content}". Shall I save it?'


def _render_forget_memory_confirmation(existing_content: str, user_message: str) -> str:
    """Built from the REAL, currently-active memory's own content
    (re-fetched from the database, not restated by the model) — the
    same "confirmation matches what will actually happen" guarantee as
    the other two renderers."""
    if _is_arabic(user_message):
        return f'هنسى إني فاكر إنك: "{existing_content}". تأكيد؟'
    return f'I\'ll forget that I have this on record: "{existing_content}". Confirm?'


def _render_weather_location_not_found(location: str, user_message: str) -> str:
    if _is_arabic(user_message):
        return f'معرفتش ألاقي مكان اسمه "{location}" — ممكن تتأكد من الاسم أو تضيف اسم الدولة؟'
    return f'I couldn\'t find a place called "{location}" — could you double-check the spelling or add a country?'


def _render_weather_unavailable(user_message: str) -> str:
    if _is_arabic(user_message):
        return "معرفتش أوصل لخدمة الطقس دلوقتي — جرب تاني بعد شوية."
    return "I can't reach the weather service right now — try again in a bit."


def _render_weather_reply(result: WeatherResult, user_message: str) -> str:
    """Deterministic, code-owned rendering (Checkpoint 3.7) — reads
    ONLY WeatherResult's own normalized fields, the same discipline as
    _render_create_task_confirmation. Facts only: no clothing/activity
    advice is ever composed here. Temperatures are rounded for display
    only; WeatherResult itself keeps the unrounded values.
    """
    arabic = _is_arabic(user_message)
    labels = _WEATHER_CONDITION_LABELS_AR if arabic else _WEATHER_CONDITION_LABELS_EN
    condition = labels[result.condition]

    if result.horizon == "now":
        temperature = round(result.temperature)
        feels_like = round(result.feels_like)
        if arabic:
            body = f"الجو في {result.resolved_location} دلوقتي: {condition}، {temperature}° (حرارة محسوسة {feels_like}°)."
        else:
            body = f"The weather in {result.resolved_location} right now: {condition}, {temperature}°C (feels like {feels_like}°C)."

    elif result.horizon in ("today", "tomorrow"):
        low, high = round(result.temperature_low), round(result.temperature_high)
        if arabic:
            period_label = "النهارده" if result.horizon == "today" else "بكرة"
            body = f"الجو {period_label} في {result.resolved_location}: {condition}، من {low}° لحد {high}°"
            if result.precipitation_probability is not None:
                body += f"، واحتمال مطر {round(result.precipitation_probability)}٪"
            body += "."
        else:
            period_label = "Today's" if result.horizon == "today" else "Tomorrow's"
            body = f"{period_label} weather in {result.resolved_location}: {condition}, {low}–{high}°C"
            if result.precipitation_probability is not None:
                body += f", with a {round(result.precipitation_probability)}% chance of rain"
            body += "."

    else:  # tonight
        low, high = round(result.temperature_low), round(result.temperature_high)
        window = f"{result.period_start:%H:%M}–{result.period_end:%H:%M}"
        if arabic:
            body = (
                f"الجو الليلة في {result.resolved_location} (من {result.period_start:%H:%M} "
                f"لحد {result.period_end:%H:%M}): {condition}، من {low}° لحد {high}°، "
                f"احتمال مطر {round(result.precipitation_probability)}٪."
            )
        else:
            body = (
                f"Tonight in {result.resolved_location} ({window} local): {condition}, "
                f"{low}–{high}°C, {round(result.precipitation_probability)}% chance of rain."
            )

    return _append_weather_attribution(body)


def _append_weather_attribution(text: str) -> str:
    """Shared by the factual renderer and the Checkpoint 3.8 reasoning
    path — code-owned and deterministic either way, never relying on
    the model to remember or restate licensing text (see
    weather/service.py's module docstring for the CC BY 4.0
    requirement this satisfies)."""
    return f"{text}\n{_WEATHER_ATTRIBUTION_LINE}"


def _weather_tool_result_payload(result: WeatherResult) -> str:
    """Checkpoint 3.8: the ONLY weather data the reasoning continuation
    ever sees — a small, explicit, JSON-serialized subset of
    WeatherResult's own already-normalized fields. Never the raw
    Open-Meteo payload, never Tasks/Calendar/Memory/history."""
    payload = {
        "source": "open-meteo",
        "resolved_location": result.resolved_location,
        "horizon": result.horizon,
        "period_start": result.period_start.isoformat(),
        "period_end": result.period_end.isoformat() if result.period_end else None,
        "timezone": result.timezone,
        "temperature_c": result.temperature,
        "temperature_low_c": result.temperature_low,
        "temperature_high_c": result.temperature_high,
        "feels_like_c": result.feels_like,
        "condition": result.condition,
        "precipitation_probability_percent": result.precipitation_probability,
    }
    return json.dumps(payload)


def _format_memory_line(memory: Memory) -> str:
    label = _MEMORY_TYPE_CONTEXT_LABELS.get(memory.type, memory.type)
    return f"{label}: {memory.content} (mem_id={memory.id})"


def _format_memory_context(memories: list[Memory], total_active_count: int) -> str:
    """Same truncated-and-said-so convention as chat/context.py's own
    _format_section — applies equally whether this is an ordinary turn
    or the user explicitly asking "what do you remember about me?": the
    model is never handed a capped list without being told it's capped.
    """
    lines = [_format_memory_line(m) for m in memories]
    text = "## What I remember about you\n" + ("\n".join(lines) if lines else "(nothing yet)")
    remaining = total_active_count - len(memories)
    if remaining > 0:
        text += f"\n... and {remaining} more not shown"
    if len(text) > _MAX_MEMORY_CONTEXT_CHARS:
        text = text[:_MAX_MEMORY_CONTEXT_CHARS] + "\n... (memory context truncated)"
    return text


def _reply_for_rejected_action(action_type: str, user_message: str) -> str:
    """Checkpoint 3.19 — see the tables' own docstring above for why
    this exists. Falls back to the old generic _REJECTED_MESSAGE for
    any action_type not in the table (structurally unreachable today,
    since every current action_type is listed, but keeps this safe
    against a future action_type being added to VALID_ACTION_TYPES
    without a matching entry here being remembered)."""
    table = _REJECTED_MESSAGE_AR_BY_ACTION_TYPE if _is_arabic(user_message) else _REJECTED_MESSAGE_EN_BY_ACTION_TYPE
    return table.get(action_type, _REJECTED_MESSAGE)


def _reply_for_execution_failed(action_type: str, user_message: str) -> str:
    """Checkpoint 3.21 — see _EXECUTION_FAILED_MESSAGE_*_BY_ACTION_TYPE's
    own comment above for why this exists. Falls back to the old
    generic _EXECUTION_FAILED_MESSAGE for any action_type not in the
    table (structurally unreachable today, same safety margin as
    _reply_for_rejected_action's own fallback)."""
    table = (
        _EXECUTION_FAILED_MESSAGE_AR_BY_ACTION_TYPE if _is_arabic(user_message)
        else _EXECUTION_FAILED_MESSAGE_EN_BY_ACTION_TYPE
    )
    return table.get(action_type, _EXECUTION_FAILED_MESSAGE)


def _reply_for_invalid_proposal(user_message: str) -> str:
    """Checkpoint 3.21 — see _INVALID_PROPOSAL_FALLBACK_MESSAGE's own
    comment above. Deliberately takes ONLY user_message, never
    model_text — the whole point is that the model's own accompanying
    text is not trusted here, regardless of what it says."""
    return _INVALID_PROPOSAL_FALLBACK_MESSAGE_AR if _is_arabic(user_message) else _INVALID_PROPOSAL_FALLBACK_MESSAGE


def _reply_for_stale_proposal_text(user_message: str) -> str:
    """Checkpoint 3.21 — MODEL TEXT IS NEVER EXECUTION EVIDENCE. Called
    only from send_message's own narrow, structural population: the
    CURRENT message was itself classified by the SAME deterministic
    _classify_narrow_yes_no already used for the adjacent dispatch as a
    bare yes/no, directed at a real, still-pending ProposedAction
    (`pending is not None`) that is no longer conversationally adjacent
    (Checkpoint 3.17's `adjacent`). This is never reached merely
    because a proposal happens to be pending — an unrelated read,
    weather question, or clarification-worthy new request all produce
    narrow_answer=None and never reach this function at all (see
    send_message's own call site).

    Nothing executed or rejected on THIS turn either way — that is
    exactly what non-adjacent means, and it is unconditionally true
    regardless of what the model's own (possibly completion-sounding —
    see the 3.20 inspection's live-gate finding) text said. This
    function therefore never inspects that text at all; it replaces it
    outright rather than trying to detect whether it happened to be
    honest this one time.
    """
    return (
        _STALE_PROPOSAL_NOT_EXECUTED_MESSAGE_AR if _is_arabic(user_message)
        else _STALE_PROPOSAL_NOT_EXECUTED_MESSAGE_EN
    )


def _handle_respond_with_text(arguments: dict) -> str | None:
    """Checkpoint 3.23 — the structural replacement for the old bare-
    text ("no tool call at all") fallthrough, now that tool_choice
    forces every primary chat turn to call exactly one tool (see
    orchestrator/service.py's _PRIMARY_CHAT_TOOL_CHOICE).

    Deliberately DOES NOT follow the _handle_*_proposal/_handle_get_weather
    shape (db/space_id/... in, ChatMessage persisted and returned) —
    unlike every one of those, respond_with_text's own reply is not
    unconditionally authoritative: send_message's existing 3.21
    stale-proposal guard, and (Checkpoint 3.25) the independent
    mutation-claim verifier, must still be able to override it before
    anything is persisted. Returning a plain candidate string, not a
    persisted ChatMessage, is what leaves both overrides possible.

    respond_with_text is UNTRUSTED MODEL PROSE — MODEL TEXT IS NEVER
    EXECUTION EVIDENCE. It creates no ProposedAction, touches no
    domain module, and its own schema (RespondWithTextArguments)
    structurally carries no execution/completion/action_type field at
    all; nothing about calling this tool is ever treated as evidence
    that anything happened.

    Checkpoint 3.25: returns None (rather than the deterministic
    _NO_REPLY_FALLBACK_MESSAGE string directly) on invalid arguments —
    None is send_message's own signal to skip the 3.25 claim verifier
    entirely for this turn, since _NO_REPLY_FALLBACK_MESSAGE is already
    a fixed, application-owned constant with nothing to verify (running
    the verifier on it would be pure wasted cost/latency). Matches
    every other handler's own "never trust accompanying model text on a
    validation failure" discipline (fixed in 3.21) — never falls back
    to any raw model text, from any source, on invalid arguments.
    """
    try:
        validated = RespondWithTextArguments(**arguments)
    except ValidationError:
        return None
    return validated.text


def _reply_for_unverified_mutation_claim(user_message: str) -> str:
    """Checkpoint 3.25 — see _UNVERIFIED_MUTATION_CLAIM_MESSAGE_*'s own
    comment above. Used identically whether the verifier explicitly
    certified a false completion claim or the verification itself
    could not be trusted (provider failure, malformed contract) — see
    _candidate_reply_is_safe_to_show, the sole caller of this
    function's sibling check."""
    return (
        _UNVERIFIED_MUTATION_CLAIM_MESSAGE_AR if _is_arabic(user_message)
        else _UNVERIFIED_MUTATION_CLAIM_MESSAGE_EN
    )


def _candidate_reply_is_safe_to_show(candidate_text: str) -> bool:
    """Checkpoint 3.25 — the fail-closed policy wrapper around
    orchestrator_service.verify_no_mutation_claim (the independent
    verifier itself). Returns True (safe to show verbatim) ONLY when
    the verifier call succeeded, returned a well-formed contract, AND
    explicitly certified claims_bazra_mutation_completed=False.

    Any other outcome — an explicit True certification, or
    ClaimVerificationFailed for any reason (provider failure, zero/
    multiple tool calls, wrong tool, missing/non-boolean field) —
    returns False here. This is the exact equivalence this checkpoint
    calls for: a verifier we can't trust is exactly as unsafe as one
    that flags the candidate true. Never raises; never retries.
    """
    try:
        claims_completed = orchestrator_service.verify_no_mutation_claim(candidate_text)
    except orchestrator_service.ClaimVerificationFailed:
        return False
    return not claims_completed


def _reply_for_confirm_result(result: ConfirmResult, action_type: str, user_message: str) -> str:
    if result.outcome == "executed":
        if result.task is not None:
            if result.task_action == "updated":
                return f'Done — I\'ve updated the task "{result.task.title}".'
            if result.task_action == "deleted":
                return f'Done — I\'ve removed the task "{result.task.title}".'
            return f'Done — I\'ve created the task "{result.task.title}".'
        if result.event is not None:
            if result.event_action == "updated":
                return f'Done — I\'ve updated "{result.event.title}".'
            if result.event_action == "deleted":
                return f'Done — I\'ve removed "{result.event.title}" from your BAZRA calendar.'
            return f'Done — I\'ve added "{result.event.title}" to your calendar.'
        if result.memory is not None:
            if result.memory.status == "forgotten":
                return "Done — I've forgotten that."
            return "Done — I'll remember that."
    if result.outcome == "nothing_pending":
        return _NOTHING_PENDING_MESSAGE
    if result.outcome == "execution_failed":
        return _reply_for_execution_failed(action_type, user_message)
    return _REJECTED_MESSAGE


def _require_active_memory(db: Session, space_id: int, user_id: int, action_type: str, memory_id: int) -> Memory:
    """Checkpoint 3.4: validate_arguments stays a pure, DB-free shape
    validator (correct for create_task and save_memory's own scalar
    fields), but forget_memory and save_memory's optional
    supersedes_memory_id both reference an EXISTING memory row — this
    is the DB lookup that resolves that reference, called right after
    the pure shape check succeeds. A None result (id doesn't exist, or
    belongs to another space/user, or is no longer active — one query
    collapses all three into a single safe case) is surfaced as the
    SAME InvalidActionArgumentsError the pure validator already raises
    for a malformed field: a memory_id that resolves to nothing real is
    exactly as invalid as a missing title. Raised HERE, before any
    proposal is created — the None value itself doesn't raise anything,
    this function does, acting on it.
    """
    memory = memory_service.get_active_memory_for_user(db, space_id, user_id, memory_id)
    if memory is None:
        raise actions_service.InvalidActionArgumentsError(
            action_type, f"memory_id {memory_id} is not an active memory you own"
        )
    return memory


def _require_existing_task(db: Session, space_id: int, action_type: str, task_id: int):
    """Checkpoint 3.10 — the same DB-lookup discipline as
    _require_active_memory: validate_arguments only checks that
    task_id is shaped like an int; this resolves whether it's a REAL
    task in this space, called right after that pure shape check
    succeeds. A None result (wrong id, wrong space) becomes the SAME
    InvalidActionArgumentsError, before any proposal is created.
    """
    task = tasks_service.get_task(db, space_id, task_id)
    if task is None:
        raise actions_service.InvalidActionArgumentsError(
            action_type, f"task_id {task_id} is not a task you own"
        )
    return task


def _handle_create_task_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("create_task", arguments)
    except actions_service.InvalidActionArgumentsError:
        # Validated before anything is persisted — nothing to roll
        # back. There's no valid structured data to render a
        # deterministic confirmation from here, so (only in this
        # error case) the model's own text is used as a fallback.
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    reply_text = _render_create_task_confirmation(validated, timezone_name, user_message_content)
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "create_task", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _handle_update_task_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("update_task", arguments)
        task = _require_existing_task(db, space_id, "update_task", validated["task_id"])
    except actions_service.InvalidActionArgumentsError:
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    changes = {k: v for k, v in validated.items() if k != "task_id"}
    reply_text = _render_update_task_confirmation(task.title, changes, timezone_name, user_message_content)
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "update_task", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _handle_delete_task_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("delete_task", arguments)
        task = _require_existing_task(db, space_id, "delete_task", validated["task_id"])
    except actions_service.InvalidActionArgumentsError:
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    reply_text = _render_delete_task_confirmation(task.title, user_message_content)
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "delete_task", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _require_existing_life_area(db: Session, action_type: str, life_area_id: int):
    """Checkpoint 3.15 — the same DB-lookup discipline as
    _require_existing_task, adapted for LifeArea's own model: LifeArea
    has no SpaceScopedMixin at all (see life_areas/models.py's own
    docstring — it's deliberately global, not per-space), so there is no
    per-space ownership check to perform, only a real-vs-nonexistent
    one — the same check the direct REST routers already perform via
    their own _validate_life_area. A None result becomes the SAME
    InvalidActionArgumentsError as every other invalid reference, before
    any proposal is created.
    """
    area = life_areas_service.get_life_area(db, life_area_id)
    if area is None:
        raise actions_service.InvalidActionArgumentsError(
            action_type, f"life_area_id {life_area_id} does not exist"
        )
    return area


def _handle_create_event_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("create_event", arguments)
        life_area_id = validated.get("life_area_id")
        life_area = (
            _require_existing_life_area(db, "create_event", life_area_id) if life_area_id is not None else None
        )
    except actions_service.InvalidActionArgumentsError:
        # Covers a missing title/starts_at, a naive starts_at/ends_at,
        # ends_at before starts_at, and an unknown life_area_id — all
        # collapse into the same "nothing valid to propose yet" fallback,
        # never a malformed pending ProposedAction.
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    reply_text = _render_create_event_confirmation(
        validated, timezone_name, user_message_content, life_area.name if life_area else None,
    )
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "create_event", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _require_existing_event(db: Session, space_id: int, action_type: str, event_id: int):
    """Checkpoint 3.18 — the same DB-lookup discipline as
    _require_existing_task: calendar_service.get_calendar_event is
    already space-scoped and already filters archived_at IS NULL, so a
    wrong id, wrong space, or archived event all collapse into the SAME
    None -> InvalidActionArgumentsError, before any proposal is created
    — an archived event can never be conversationally updated, exactly
    like it already can't be updated via direct REST.
    """
    event = calendar_service.get_calendar_event(db, space_id, event_id)
    if event is None:
        raise actions_service.InvalidActionArgumentsError(
            action_type, f"event_id {event_id} is not an event you own"
        )
    return event


def _validate_merged_event_temporal_range(event, changes: dict) -> None:
    """Checkpoint 3.18 — proposal-time merged-state validation: a
    partial `changes` dict may name only one of starts_at/ends_at (e.g.
    "starts_at=14:00 only"), so checking the supplied fields alone
    would miss that the event's own EXISTING ends_at=12:00 no longer
    follows a new starts_at=14:00. Merges changes onto the event's
    current values in plain Python and applies the exact same
    `ends_at < starts_at` check calendar_service.update_calendar_event
    itself performs against the merged ORM object at execution time —
    duplicated here deliberately (not refactored out of that
    REST-facing function) so this stays a proposal-time PREVIEW that
    never touches the real row. changes' values are JSON-round-tripped
    ISO strings (validate_arguments's own mode="json" dump) — parsed
    back to datetimes here for the comparison.
    """
    merged_starts_at = (
        datetime.fromisoformat(changes["starts_at"]) if "starts_at" in changes else event.starts_at
    )
    if "ends_at" in changes:
        merged_ends_at = datetime.fromisoformat(changes["ends_at"]) if changes["ends_at"] else None
    else:
        merged_ends_at = event.ends_at
    if merged_ends_at is not None and merged_ends_at < merged_starts_at:
        raise actions_service.InvalidActionArgumentsError(
            "update_event", "resulting ends_at must be >= starts_at"
        )


def _handle_update_event_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("update_event", arguments)
        event = _require_existing_event(db, space_id, "update_event", validated["event_id"])
        changes = {k: v for k, v in validated.items() if k != "event_id"}
        _validate_merged_event_temporal_range(event, changes)
        life_area_id = changes.get("life_area_id")
        life_area = (
            _require_existing_life_area(db, "update_event", life_area_id) if life_area_id is not None else None
        )
    except actions_service.InvalidActionArgumentsError:
        # Covers a missing/nonexistent/archived event_id, a naive or
        # merged-invalid starts_at/ends_at, an unknown life_area_id, and
        # a no-op proposal (no field actually changing) — all collapse
        # into the same "nothing valid to propose yet" fallback, never
        # a malformed pending ProposedAction.
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    reply_text = _render_update_event_confirmation(
        event, changes, timezone_name, user_message_content, life_area.name if life_area else None,
    )
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "update_event", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _handle_delete_event_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("delete_event", arguments)
        event = _require_existing_event(db, space_id, "delete_event", validated["event_id"])
    except actions_service.InvalidActionArgumentsError:
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    reply_text = _render_delete_event_confirmation(event, timezone_name, user_message_content)
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "delete_event", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _handle_save_memory_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("save_memory", arguments)
        supersedes_id = validated.get("supersedes_memory_id")
        if supersedes_id is not None:
            _require_active_memory(db, space_id, user_id, "save_memory", supersedes_id)
    except actions_service.InvalidActionArgumentsError:
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    reply_text = _render_save_memory_confirmation(validated, user_message_content)
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "save_memory", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _handle_forget_memory_proposal(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    try:
        validated = actions_service.validate_arguments("forget_memory", arguments)
        target = _require_active_memory(db, space_id, user_id, "forget_memory", validated["memory_id"])
    except actions_service.InvalidActionArgumentsError:
        return record_assistant_message(db, space_id, user_id, _reply_for_invalid_proposal(user_message_content))

    reply_text = _render_forget_memory_confirmation(target.content, user_message_content)
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "forget_memory", validated, commit=False,
    )
    db.commit()
    db.refresh(assistant_message)
    return assistant_message


def _handle_get_weather(
    db: Session, space_id: int, user_id: int, arguments: dict, model_text: str | None,
    timezone_name: str, user_message_content: str, tool_use_id: str, correlation_id: str,
) -> ChatMessage:
    """Checkpoint 3.7 — READ, not a write proposal: executes directly,
    in this same handler call, and NEVER calls
    actions_service.create_pending_action at any point — that omission
    IS the code-level enforcement of the read/write boundary (see
    docs/architecture/bazra-capability-routing.md), not a separate
    dispatch dictionary. Registered in the same _TOOL_HANDLERS dict as
    every write handler below.

    Checkpoint 3.8: response_mode branches AFTER a successful fetch —
    "factual" is byte-for-byte the 3.7 path (unaffected); "reason" adds
    exactly one more, terminal model call
    (orchestrator_service.generate_tool_result_reply, tools=None) that
    reasons ONLY from this fetch's own normalized WeatherResult. A
    location/geocoding/fetch failure is identical regardless of
    response_mode — the reasoning path never even begins without a
    real, already-fetched result to ground it in.
    """
    try:
        validated = GetWeatherArguments(**arguments)
    except ValidationError:
        return record_assistant_message(
            db, space_id, user_id, model_text or _WEATHER_MISSING_LOCATION_FALLBACK_MESSAGE
        )

    try:
        resolved = weather_service.geocode_location(validated.location)
    except WeatherProviderError:
        return record_assistant_message(db, space_id, user_id, _render_weather_unavailable(user_message_content))

    if resolved is None:
        return record_assistant_message(
            db, space_id, user_id,
            _render_weather_location_not_found(validated.location, user_message_content),
        )

    try:
        result = weather_service.fetch_weather(resolved, validated.horizon)
    except WeatherProviderError:
        return record_assistant_message(db, space_id, user_id, _render_weather_unavailable(user_message_content))

    if validated.response_mode == "reason":
        try:
            reasoning_text = orchestrator_service.generate_tool_result_reply(
                user_message=user_message_content,
                tool_use_id=tool_use_id,
                tool_name="get_weather",
                tool_arguments=arguments,
                assistant_text=model_text,
                tool_result_content=_weather_tool_result_payload(result),
                correlation_id=correlation_id,
            )
        except orchestrator_service.OrchestratorError:
            reasoning_text = None

        if reasoning_text:
            return record_assistant_message(db, space_id, user_id, _append_weather_attribution(reasoning_text))
        # Continuation failed or returned nothing usable — degrade to
        # the factual reply rather than a generic error: the facts
        # were genuinely fetched, only the interpretation step failed.
        # No retry, no third model call.

    reply_text = _render_weather_reply(result, user_message_content)
    return record_assistant_message(db, space_id, user_id, reply_text)


_TOOL_HANDLERS = {
    "propose_create_task": _handle_create_task_proposal,
    "propose_update_task": _handle_update_task_proposal,
    "propose_delete_task": _handle_delete_task_proposal,
    "propose_create_event": _handle_create_event_proposal,
    "propose_update_event": _handle_update_event_proposal,
    "propose_delete_event": _handle_delete_event_proposal,
    "propose_save_memory": _handle_save_memory_proposal,
    "propose_forget_memory": _handle_forget_memory_proposal,
    "get_weather": _handle_get_weather,
}


def send_message(
    db: Session,
    space_id: int,
    user_id: int,
    content: str,
    tomorrow_start: datetime,
    window_end: datetime,
    timezone_name: str,
    model_provider_override: str = "default",
) -> tuple[ChatMessage, ChatMessage]:
    """The user's own message is ALWAYS persisted immediately, before
    anything else is attempted — they genuinely sent it. An assistant
    message is persisted ONLY on success (either the deterministic
    unavailability reply, or a real model reply) — never a synthetic
    "error" turn on model failure, which would pollute future context
    with a fake conversational turn that never actually happened.

    Routing, in order:
    1. Message too long / timezone invalid -> reject before persisting anything.
    2. A clear write-intent phrase (delete/edit/mark-done/etc, or a
       create-something-other-than-a-task) -> deterministic decline,
       exactly as before 3.3. Unaffected by any pending proposal.
    3. A pending proposal exists, is still conversationally adjacent
       (Checkpoint 3.17 — see is_still_conversationally_adjacent), AND
       the message is a narrow bare yes/no -> deterministic
       confirm/reject, zero model calls, regardless of the pending
       proposal's action_type. A pending proposal that is NOT adjacent
       (something else was said since its own confirmation prompt) is
       never deterministically confirmed or rejected — it falls through
       to step 4 like any other message, still fully intact and still
       'pending' in the database, describable but not bare-yes/no-
       actionable.
    4. (Checkpoint 5.2) A narrow, zero-LLM retrieval phrase (see
       deterministic_retrieval.build_deterministic_retrieval_reply) ->
       authoritative local Task/Calendar/Inbox query, deterministic
       presentation, persisted as a normal assistant ChatMessage, zero
       model/model_router calls. Ambiguous, judgmental, mutation-
       shaped, or simply unrecognized messages are NOT matched here —
       they fall through to step 5 unaffected, exactly as before this
       checkpoint. A pending proposal (if any) is left completely
       untouched by this branch — neither confirmed, rejected, nor
       described — it remains exactly as 'pending' as it was before
       this message arrived.
    5. Everything else -> the Orchestrator, with propose_create_task/
       propose_save_memory/propose_forget_memory/.../get_weather and
       (Checkpoint 3.23) respond_with_text ALL offered, and any pending
       proposal (of whichever type) folded into context, plus
       (Checkpoint 3.4) the user's own active memories. As of 3.23 the
       model MUST return exactly one tool call for this turn (see
       orchestrator_service.generate_reply's own _PRIMARY_CHAT_TOOL_CHOICE)
       — a bare, tool-less text reply is no longer a possible response
       shape at all; zero or multiple tool calls raise
       OrchestratorContractViolationError, caught by the same
       `except OrchestratorError` below as any other provider failure.
       A propose_*/get_weather call creates/revises a pending proposal
       or executes a read exactly as before; a respond_with_text call
       is chat_service's OWN, untrusted, no-action reply — checked, in
       order: (a) (Checkpoint 3.21, unchanged in what it distrusts) if
       the current message was itself a bare yes/no aimed at a real
       pending proposal that just failed step 3's own adjacency check
       (narrow_answer is not None, pending is not None, not adjacent),
       that population never gets respond_with_text's own text at all
       — short-circuited BEFORE it is even extracted, since nothing
       about DB state depends on it; (b) otherwise (Checkpoint 3.25)
       the candidate text is validated and passed through an
       independent mutation-claim verifier (orchestrator_service.
       verify_no_mutation_claim, a separate cheap-model call) that
       answers only "does this text claim BAZRA already completed a
       mutation" — never user intent. A certified-unsafe candidate, an
       invalid one, or a verifier that itself failed all fail closed to
       the same deterministic reply; MODEL TEXT IS NEVER EXECUTION
       EVIDENCE either way. See _reply_for_stale_proposal_text's and
       _candidate_reply_is_safe_to_show's own docstrings.

    model_provider_override (Checkpoint 5.7H, Manual Gemini Test Mode):
    "default" (the parameter's own default) means EVERY branch above
    and below behaves exactly as it did before this checkpoint — this
    value is only ever consulted in step 5, nowhere else, so write-
    intent-decline/confirm/reject/deterministic-retrieval (steps 1-4)
    are completely unaffected by it regardless of its value (Checkpoint
    5.2's own deterministic-local-first guarantee is structurally
    preserved: it already runs, and already returns, BEFORE this
    parameter is ever read). "google_gemini_test" changes exactly one
    thing in step 5: generate_reply is called with an explicit
    provider/model (Gemini) and a single-tool offer restricted to
    respond_with_text only (see _GEMINI_TEST_TOOLS_OFFERED's own
    docstring for why) — Context Assembly, conversation history, the
    system prompt, and the claim verifier are ALL identical to the
    default path; the only experimental difference is which provider
    generates the reply text. A Gemini failure here raises
    OrchestratorError exactly like an Anthropic failure would (caught
    below, unchanged) — honest degradation, never a silent fallback to
    Anthropic.

    Checkpoint 3.17: a transaction-scoped conversation advisory lock
    (_acquire_conversation_lock) is held from just before the incoming
    message is flushed through the adjacency decision above — released
    by whichever commit happens first (the write-intent-decline path,
    the deterministic confirm/reject path, or the explicit db.commit()
    below when falling through) — so no concurrent request for the
    SAME (space_id, user_id) can insert a message whose existence would
    change that decision while it's being made. See the lock helper's
    own docstring for why a plain id-range query alone was proven
    unsafe.
    """
    if len(content) > _MAX_INCOMING_MESSAGE_CHARS:
        raise MessageTooLongError(_MAX_INCOMING_MESSAGE_CHARS)

    # Validate before persisting anything — an invalid timezone must
    # never leave a lone user message with no reply.
    current_datetime_local = _compute_current_datetime_local(timezone_name)

    _acquire_conversation_lock(db, space_id, user_id)
    user_message = record_user_message(db, space_id, user_id, content, commit=False)

    if detect_clear_write_intent(content):
        # Deterministic path — no model call, no cost, no ai_traces row,
        # since nothing was attempted. Commits (and so releases the
        # conversation lock) together with the user message above.
        assistant_message = record_assistant_message(db, space_id, user_id, WRITE_UNAVAILABLE_MESSAGE, commit=False)
        db.commit()
        _log_deterministic_route("write_intent_decline", user_message.id)
        return user_message, assistant_message

    pending = actions_service.get_latest_pending(db, space_id, user_id)
    adjacent = (
        actions_service.is_still_conversationally_adjacent(db, space_id, user_id, pending, user_message.id)
        if pending is not None else False
    )
    narrow_answer = _classify_narrow_yes_no(content) if pending is not None else None

    if narrow_answer == "yes" and adjacent:
        action_type = pending.action_type
        result = actions_service.confirm_and_execute(db, space_id, user_id)
        assistant_message = record_assistant_message(
            db, space_id, user_id, _reply_for_confirm_result(result, action_type, content)
        )
        _log_deterministic_route("proposal_confirm", user_message.id)
        return user_message, assistant_message

    if narrow_answer == "no" and adjacent:
        rejected_reply = _reply_for_rejected_action(pending.action_type, content)
        actions_service.reject(db, space_id, user_id)
        assistant_message = record_assistant_message(db, space_id, user_id, rejected_reply)
        _log_deterministic_route("proposal_reject", user_message.id)
        return user_message, assistant_message

    # Falling through: either an ordinary message, or a bare yes/no
    # that no longer applies (nothing really pending, or a real pending
    # proposal that is no longer conversationally adjacent). The
    # adjacency conclusion above is already final — commit now (this
    # also persists the user message and releases the conversation
    # lock) before the potentially slow Orchestrator call below, which
    # needs no lock at all.
    db.commit()

    # Checkpoint 5.2 — narrow, zero-LLM retrieval recognition. Runs
    # AFTER every authority-sensitive branch above (write-intent
    # decline, confirm, reject — none of which this can ever steal,
    # since they already returned above) and BEFORE Context Assembly/
    # the Orchestrator, exactly the brief's own required ordering. None
    # for the overwhelming majority of messages — anything ambiguous,
    # judgmental, mutation-shaped, or simply unrecognized falls straight
    # through to the existing model path below, completely unaffected.
    # Zero provider/model_router calls on this branch — see
    # deterministic_retrieval.py's own module docstring.
    deterministic_reply = deterministic_retrieval.build_deterministic_retrieval_reply(
        db, space_id, content, timezone_name
    )
    if deterministic_reply is not None:
        assistant_message = record_assistant_message(db, space_id, user_id, deterministic_reply)
        _log_deterministic_route("retrieval", user_message.id)
        return user_message, assistant_message

    context = context_module.gather_context(db, space_id, tomorrow_start, window_end)
    memories, total_active_memories = memory_service.get_relevant_memories(db, space_id, user_id, limit=_MAX_MEMORIES)
    context = f"{context}\n\n{_format_memory_context(memories, total_active_memories)}"
    if pending is not None:
        context = f"{context}\n\n## Pending proposal awaiting confirmation\n{_describe_pending_proposal(pending, adjacent)}"
    else:
        context = f"{context}\n\n## Current action state\n{_NO_ACTIVE_PROPOSAL_NOTE}"
    history_rows = list_recent_messages(db, space_id, user_id)
    history = _trim_to_char_budget(history_rows, _MAX_HISTORY_CHARS)

    # Checkpoint 5.7H — "default" (the overwhelming majority of calls,
    # and the only possibility before this checkpoint) passes
    # provider=None/model=None, which generate_reply's own docstring
    # guarantees is byte-for-byte its pre-5.7H call shape. Only the one
    # explicit, closed override resolves to anything else, and even
    # then changes ONLY the tools offered (respond_with_text alone) and
    # the provider/model generate_reply is told to use — context,
    # history, and the system prompt above are completely unaffected by
    # this branch.
    override_provider_model = _MODEL_PROVIDER_OVERRIDE_MAP.get(model_provider_override)
    tools_for_turn = _TOOLS_OFFERED if override_provider_model is None else _GEMINI_TEST_TOOLS_OFFERED
    override_provider, override_model = override_provider_model if override_provider_model is not None else (None, None)

    try:
        result = orchestrator_service.generate_reply(
            history=history,
            context=context,
            user_message=content,
            current_datetime_local=current_datetime_local,
            tools=tools_for_turn,
            provider=override_provider,
            model=override_model,
        )
    except orchestrator_service.OrchestratorError as exc:
        raise ChatModelCallFailed(
            user_message.id, exc.category, _reply_for_model_unavailable(exc.category, content)
        ) from exc

    # Checkpoint 3.23 — result.tool_call is now guaranteed non-None on
    # a successful return from generate_reply (it raises
    # OrchestratorContractViolationError otherwise, caught above like
    # any other OrchestratorError) — the `is not None` check stays as
    # explicit defense-in-depth, not because it can currently be False.
    tool_call = result.tool_call
    handler = _TOOL_HANDLERS.get(tool_call.tool_name) if tool_call is not None else None

    if handler is not None:
        assistant_message = handler(
            db, space_id, user_id, tool_call.arguments, result.text, timezone_name, content,
            tool_call.tool_use_id, result.correlation_id,
        )
        return user_message, assistant_message

    # Checkpoint 3.21 — MODEL TEXT IS NEVER EXECUTION EVIDENCE. Checked
    # FIRST, before _handle_respond_with_text or the 3.25 claim verifier
    # even run (Checkpoint 3.25's own deliberate ordering decision):
    # this condition depends ONLY on already-known DB/message state
    # (narrow_answer/pending/adjacent), never on the candidate text
    # itself, so when it holds, NOTHING about whichever tool fired or
    # what its text says can be authoritative for this turn — there is
    # nothing left to extract or verify. Short-circuiting here means 0
    # extra Haiku calls for this branch (see the 3.25 close-out's own
    # cost accounting), not merely fewer lines of code. If the CURRENT
    # message was itself a bare yes/no directed at a real, still-
    # pending proposal that reached here only because it was no longer
    # conversationally adjacent (3.17), no tool's own text is ever
    # trusted to honestly report that — it may (and, per the 3.19/3.20
    # live-gate findings, sometimes did) claim the stale action
    # completed anyway. narrow_answer is None for every ordinary read,
    # weather question, or ambiguity-clarification turn, so none of
    # those ever reach this override (see _classify_narrow_yes_no).
    if narrow_answer is not None and pending is not None and not adjacent:
        assistant_message = record_assistant_message(db, space_id, user_id, _reply_for_stale_proposal_text(content))
        return user_message, assistant_message

    # Checkpoint 3.23 — respond_with_text (the structural no-action
    # escape hatch that replaces the old bare-text fallthrough), or
    # defensively, any tool name this dispatch doesn't recognize.
    # _handle_respond_with_text returns a plain CANDIDATE string (or
    # None on invalid arguments), not a persisted message — see its own
    # docstring for why this handler alone doesn't follow the "persist
    # and return ChatMessage" shape every other handler uses.
    model_candidate = _handle_respond_with_text(tool_call.arguments if tool_call is not None else {})

    if model_candidate is None:
        # Invalid respond_with_text arguments — already the fixed,
        # application-owned _NO_REPLY_FALLBACK_MESSAGE constant, with
        # nothing model-authored left to verify (Checkpoint 3.25: the
        # claim verifier never runs on this branch — see
        # _handle_respond_with_text's own docstring).
        candidate_reply = _NO_REPLY_FALLBACK_MESSAGE
    elif _candidate_reply_is_safe_to_show(model_candidate):
        candidate_reply = model_candidate
    else:
        # Checkpoint 3.25 — the independent mutation-claim verifier
        # either certified that this candidate falsely claims a
        # completed BAZRA mutation, or the verification itself could
        # not be trusted (provider failure, malformed contract) — both
        # fail closed identically to this same deterministic reply,
        # never to the candidate text. This is the fix for the
        # residual the 3.20-3.24 checkpoints tracked as "Population C,
        # part 2": a fresh write with no pending proposal, misrouted to
        # respond_with_text, whose own text falsely claims completion.
        candidate_reply = _reply_for_unverified_mutation_claim(content)

    assistant_message = record_assistant_message(db, space_id, user_id, candidate_reply)
    return user_message, assistant_message
