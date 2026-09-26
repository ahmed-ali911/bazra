import json
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.actions import service as actions_service
from app.modules.actions.schemas import ConfirmResult
from app.modules.chat import context as context_module
from app.modules.chat.models import ChatMessage
from app.modules.chat.write_intent import WRITE_UNAVAILABLE_MESSAGE, detect_clear_write_intent
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

_TOOLS_OFFERED = [
    _PROPOSE_CREATE_TASK_TOOL, _PROPOSE_UPDATE_TASK_TOOL, _PROPOSE_DELETE_TASK_TOOL,
    _PROPOSE_SAVE_MEMORY_TOOL, _PROPOSE_FORGET_MEMORY_TOOL, _GET_WEATHER_TOOL,
]

# Used by _describe_pending_proposal to tell the model which tool to call
# again for a revision, regardless of which action_type is pending.
_ACTION_TYPE_TOOL_NAMES = {
    "create_task": "propose_create_task",
    "update_task": "propose_update_task",
    "delete_task": "propose_delete_task",
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
_NOTHING_PENDING_MESSAGE = "I don't have a pending proposal to confirm right now."
_EXECUTION_FAILED_MESSAGE = "Something went wrong while creating that task — could you say yes again?"
_INVALID_PROPOSAL_FALLBACK_MESSAGE = (
    "I tried to put together a task proposal but couldn't — could you rephrase what you'd like to create?"
)
_NO_REPLY_FALLBACK_MESSAGE = "Sorry, I don't have a reply for that."

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
    def __init__(self, user_message_id: int):
        self.user_message_id = user_message_id
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


def record_user_message(db: Session, space_id: int, user_id: int, content: str) -> ChatMessage:
    message = ChatMessage(space_id=space_id, user_id=user_id, role="user", content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
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


def _compute_current_datetime_local(timezone_name: str) -> str:
    """The server computes its own trustworthy current instant
    (datetime.now(timezone.utc)) and only trusts the CLIENT for which
    IANA zone to render it in — never for the instant itself. An
    unresolvable zone name is a 422, never a silent UTC fallback (a
    silent fallback would make the model confidently resolve "tomorrow"
    against the wrong day with no visible signal that anything was
    wrong).
    """
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTimezoneError(timezone_name) from exc
    local_now = datetime.now(timezone.utc).astimezone(zone)
    return local_now.strftime("%A, %Y-%m-%d %H:%M %Z")


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


def _describe_pending_proposal(pending) -> str:
    tool_name = _ACTION_TYPE_TOOL_NAMES.get(pending.action_type, pending.action_type)
    return (
        f"A '{pending.action_type}' proposal is awaiting the user's yes/no "
        f"confirmation (proposed just now, expires {pending.expires_at.isoformat()}). "
        f"Proposed arguments: {pending.arguments}. If the user's message is a "
        f"revision request rather than a plain yes/no, call {tool_name} "
        f"again with the corrected arguments — this replaces the pending proposal "
        f"above rather than creating an additional one."
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

    if due_at:
        when = _format_due_at_local(due_at, timezone_name)
        if arabic:
            return f'هضيف مهمة "{title}" بتاريخ {when}. أأكدها؟'
        return f'I\'ll add the task "{title}" for {when}. Shall I go ahead?'

    if arabic:
        return f'هضيف مهمة "{title}". أأكدها؟'
    return f'I\'ll add the task "{title}". Shall I go ahead?'


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


def _reply_for_confirm_result(result: ConfirmResult) -> str:
    if result.outcome == "executed":
        if result.task is not None:
            if result.task_action == "updated":
                return f'Done — I\'ve updated the task "{result.task.title}".'
            if result.task_action == "deleted":
                return f'Done — I\'ve removed the task "{result.task.title}".'
            return f'Done — I\'ve created the task "{result.task.title}".'
        if result.memory is not None:
            if result.memory.status == "forgotten":
                return "Done — I've forgotten that."
            return "Done — I'll remember that."
    if result.outcome == "nothing_pending":
        return _NOTHING_PENDING_MESSAGE
    if result.outcome == "execution_failed":
        return _EXECUTION_FAILED_MESSAGE
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
        return record_assistant_message(db, space_id, user_id, model_text or _INVALID_PROPOSAL_FALLBACK_MESSAGE)

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
        return record_assistant_message(db, space_id, user_id, model_text or _INVALID_PROPOSAL_FALLBACK_MESSAGE)

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
        return record_assistant_message(db, space_id, user_id, model_text or _INVALID_PROPOSAL_FALLBACK_MESSAGE)

    reply_text = _render_delete_task_confirmation(task.title, user_message_content)
    assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
    actions_service.create_pending_action(
        db, space_id, user_id, assistant_message.id, "delete_task", validated, commit=False,
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
        return record_assistant_message(db, space_id, user_id, model_text or _INVALID_PROPOSAL_FALLBACK_MESSAGE)

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
        return record_assistant_message(db, space_id, user_id, model_text or _INVALID_PROPOSAL_FALLBACK_MESSAGE)

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
    3. A pending proposal exists AND the message is a narrow bare
       yes/no -> deterministic confirm/reject, zero model calls,
       regardless of the pending proposal's action_type.
    4. Everything else -> the Orchestrator, with propose_create_task/
       propose_save_memory/propose_forget_memory all offered and any
       pending proposal (of whichever type) folded into context, plus
       (Checkpoint 3.4) the user's own active memories. A tool call
       creates/revises a pending proposal (atomically with the
       assistant message describing it); no tool call is an ordinary
       answer that leaves any pending proposal untouched.
    """
    if len(content) > _MAX_INCOMING_MESSAGE_CHARS:
        raise MessageTooLongError(_MAX_INCOMING_MESSAGE_CHARS)

    # Validate before persisting anything — an invalid timezone must
    # never leave a lone user message with no reply.
    current_datetime_local = _compute_current_datetime_local(timezone_name)

    user_message = record_user_message(db, space_id, user_id, content)

    if detect_clear_write_intent(content):
        # Deterministic path — no model call, no cost, no ai_traces row,
        # since nothing was attempted.
        assistant_message = record_assistant_message(db, space_id, user_id, WRITE_UNAVAILABLE_MESSAGE)
        _log_deterministic_route("write_intent_decline", user_message.id)
        return user_message, assistant_message

    pending = actions_service.get_latest_pending(db, space_id, user_id)
    narrow_answer = _classify_narrow_yes_no(content) if pending is not None else None

    if narrow_answer == "yes":
        result = actions_service.confirm_and_execute(db, space_id, user_id)
        assistant_message = record_assistant_message(db, space_id, user_id, _reply_for_confirm_result(result))
        _log_deterministic_route("proposal_confirm", user_message.id)
        return user_message, assistant_message

    if narrow_answer == "no":
        actions_service.reject(db, space_id, user_id)
        assistant_message = record_assistant_message(db, space_id, user_id, _REJECTED_MESSAGE)
        _log_deterministic_route("proposal_reject", user_message.id)
        return user_message, assistant_message

    context = context_module.gather_context(db, space_id, tomorrow_start, window_end)
    memories, total_active_memories = memory_service.get_relevant_memories(db, space_id, user_id, limit=_MAX_MEMORIES)
    context = f"{context}\n\n{_format_memory_context(memories, total_active_memories)}"
    if pending is not None:
        context = f"{context}\n\n## Pending proposal awaiting confirmation\n{_describe_pending_proposal(pending)}"
    else:
        context = f"{context}\n\n## Current action state\n{_NO_ACTIVE_PROPOSAL_NOTE}"
    history_rows = list_recent_messages(db, space_id, user_id)
    history = _trim_to_char_budget(history_rows, _MAX_HISTORY_CHARS)

    try:
        result = orchestrator_service.generate_reply(
            history=history,
            context=context,
            user_message=content,
            current_datetime_local=current_datetime_local,
            tools=_TOOLS_OFFERED,
        )
    except orchestrator_service.OrchestratorError as exc:
        raise ChatModelCallFailed(user_message.id) from exc

    if result.tool_call is not None:
        handler = _TOOL_HANDLERS.get(result.tool_call.tool_name)
        if handler is not None:
            assistant_message = handler(
                db, space_id, user_id, result.tool_call.arguments, result.text, timezone_name, content,
                result.tool_call.tool_use_id, result.correlation_id,
            )
            return user_message, assistant_message

    # Ordinary answer — no tool call (or an unrecognized one, which
    # should never happen since only the three tools above are ever
    # offered). Any pending proposal is left untouched.
    assistant_message = record_assistant_message(db, space_id, user_id, result.text or _NO_REPLY_FALLBACK_MESSAGE)
    return user_message, assistant_message
