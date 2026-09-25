import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.space_scoping import scoped_query
from app.modules.actions import service as actions_service
from app.modules.actions.schemas import ConfirmResult
from app.modules.chat import context as context_module
from app.modules.chat.models import ChatMessage
from app.modules.chat.write_intent import WRITE_UNAVAILABLE_MESSAGE, detect_clear_write_intent
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import HistoryTurn

# Four independent bounds, per the explicit review requirement — none of
# these were defined before, and the conversation would otherwise grow
# unbounded:
_MAX_HISTORY_MESSAGES = 20  # most recent N messages considered at all
_MAX_HISTORY_CHARS = 4000  # a second, independent cap — whichever binds first wins
_MAX_INCOMING_MESSAGE_CHARS = 4000  # the NEW message, on top of history, not counted inside its budget
_MAX_ASSISTANT_MESSAGE_CHARS = 4000  # complementary, defense-in-depth: caps future history at the source

# The only tool ever offered to the model (Checkpoint 3.3). Calling it
# produces nothing but a ToolCallRequest — an inert data structure. The
# domain fields mirror tasks.schemas.TaskCreate exactly; actions_service
# re-validates against that same schema before ever storing a proposal,
# so this input_schema is a hint to the model, not the enforcement
# boundary.
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
    return (
        f"A '{pending.action_type}' proposal is awaiting the user's yes/no "
        f"confirmation (proposed just now, expires {pending.expires_at.isoformat()}). "
        f"Proposed arguments: {pending.arguments}. If the user's message is a "
        f"revision request rather than a plain yes/no, call propose_create_task "
        f"again with the corrected arguments — this replaces the pending proposal "
        f"above rather than creating an additional one."
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


def _reply_for_confirm_result(result: ConfirmResult) -> str:
    if result.outcome == "executed":
        return f'Done — I\'ve created the task "{result.task.title}".'
    if result.outcome == "nothing_pending":
        return _NOTHING_PENDING_MESSAGE
    if result.outcome == "execution_failed":
        return _EXECUTION_FAILED_MESSAGE
    return _REJECTED_MESSAGE


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

    Checkpoint 3.3 routing, in order:
    1. Message too long / timezone invalid -> reject before persisting anything.
    2. A clear write-intent phrase (delete/edit/mark-done/etc, or a
       create-something-other-than-a-task) -> deterministic decline,
       exactly as before 3.3. Unaffected by any pending proposal.
    3. A pending proposal exists AND the message is a narrow bare
       yes/no -> deterministic confirm/reject, zero model calls.
    4. Everything else -> the Orchestrator, with propose_create_task
       offered and any pending proposal folded into context. A tool
       call creates/revises a pending proposal (atomically with the
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
        return user_message, assistant_message

    pending = actions_service.get_latest_pending(db, space_id, user_id)
    narrow_answer = _classify_narrow_yes_no(content) if pending is not None else None

    if narrow_answer == "yes":
        result = actions_service.confirm_and_execute(db, space_id, user_id)
        assistant_message = record_assistant_message(db, space_id, user_id, _reply_for_confirm_result(result))
        return user_message, assistant_message

    if narrow_answer == "no":
        actions_service.reject(db, space_id, user_id)
        assistant_message = record_assistant_message(db, space_id, user_id, _REJECTED_MESSAGE)
        return user_message, assistant_message

    context = context_module.gather_context(db, space_id, tomorrow_start, window_end)
    if pending is not None:
        context = f"{context}\n\n## Pending proposal awaiting confirmation\n{_describe_pending_proposal(pending)}"
    history_rows = list_recent_messages(db, space_id, user_id)
    history = _trim_to_char_budget(history_rows, _MAX_HISTORY_CHARS)

    try:
        result = orchestrator_service.generate_reply(
            history=history,
            context=context,
            user_message=content,
            current_datetime_local=current_datetime_local,
            tools=[_PROPOSE_CREATE_TASK_TOOL],
        )
    except orchestrator_service.OrchestratorError as exc:
        raise ChatModelCallFailed(user_message.id) from exc

    if result.tool_call is not None and result.tool_call.tool_name == "propose_create_task":
        try:
            validated_arguments = actions_service.validate_arguments("create_task", result.tool_call.arguments)
        except actions_service.InvalidActionArgumentsError:
            # Validated before anything is persisted — nothing to roll
            # back. There's no valid structured data to render a
            # deterministic confirmation from here, so (only in this
            # error case) the model's own text is used as a fallback.
            assistant_message = record_assistant_message(
                db, space_id, user_id, result.text or _INVALID_PROPOSAL_FALLBACK_MESSAGE
            )
            return user_message, assistant_message

        reply_text = _render_create_task_confirmation(validated_arguments, timezone_name, content)
        assistant_message = record_assistant_message(db, space_id, user_id, reply_text, commit=False)
        actions_service.create_pending_action(
            db, space_id, user_id, assistant_message.id, "create_task",
            validated_arguments, commit=False,
        )
        db.commit()
        db.refresh(assistant_message)
        return user_message, assistant_message

    # Ordinary answer — no tool call. Any pending proposal is left untouched.
    assistant_message = record_assistant_message(db, space_id, user_id, result.text or _NO_REPLY_FALLBACK_MESSAGE)
    return user_message, assistant_message
