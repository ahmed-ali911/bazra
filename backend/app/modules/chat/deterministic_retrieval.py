"""Checkpoint 5.2 — authoritative local retrieval with ZERO model calls.

This module owns exactly one thing: turning an already-recognized
DeterministicRetrievalIntent (see retrieval_intent.py — a pure, DB-free
text classifier) into a human-readable, deterministic reply string by
querying the SAME authoritative domain services every other part of
this app already uses (tasks_service.list_tasks,
calendar_service.list_events_starting_between,
inbox_service.list_items) — never ad-hoc SQL against another module's
table, never a new query policy invented here.

Deliberately returns a plain string (or None), not a persisted
ChatMessage — the exact same shape as chat/service.py's own
_handle_respond_with_text, and for the same reason: this module must
never import chat.service (which, in turn, is what imports and calls
this module), so creating a circular import. chat/service.py itself
calls record_assistant_message with whatever this returns.

Makes no model_router/orchestrator call whatsoever, imports neither,
and cannot create a ProposedAction, mutate any domain row, or trigger
Attention ACTED_ON/AttentionExposure — it only ever calls each domain's
existing LIST/read functions.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.calendar import service as calendar_service
from app.modules.calendar.models import CalendarEvent
from app.modules.chat.retrieval_intent import DeterministicRetrievalIntent, detect_deterministic_retrieval_intent
from app.modules.inbox import service as inbox_service
from app.modules.inbox.models import InboxItem
from app.modules.tasks import service as tasks_service
from app.modules.tasks.models import Task

# Checkpoint 5.2, section 17 — a conservative, fixed display cap,
# matching chat/context.py's own _MAX_ITEMS_PER_SECTION precedent (15)
# closely enough in spirit while staying intentionally smaller for a
# direct conversational answer (as opposed to a background system-
# prompt section a model then summarizes) — truncation is always
# disclosed, never silent.
_MAX_DISPLAYED = 10


def _ar_truncation_note(total: int, shown: int) -> str:
    return f" (دي أول {shown} من {total})" if shown < total else ""


def _en_truncation_note(total: int, shown: int) -> str:
    return f" (showing the first {shown} of {total})" if shown < total else ""


def _today_local_bounds(timezone_name: str) -> tuple[datetime, datetime]:
    """The exact same trusted-instant + client-supplied-IANA-zone
    mechanism chat/service.py's own _compute_current_datetime_local
    already uses (Checkpoint 3.14/3.15) — no new timezone policy. The
    server trusts only its own real current instant; the client is
    trusted only for which zone to render it in. timezone_name has
    already been validated by send_message before this is ever called
    (an invalid zone raises InvalidTimezoneError earlier in that same
    request, before any deterministic-retrieval or Orchestrator call is
    attempted)."""
    zone = ZoneInfo(timezone_name)
    local_now = datetime.now(timezone.utc).astimezone(zone)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    return today_start, tomorrow_start


def _format_task_bullet(task: Task, now: datetime, language: str) -> str:
    """No internal id, no raw status/priority enum value, no invented
    priority ordering — the one derived fact shown (overdue) is a
    plain, deterministic comparison against the authoritative due_at
    field, the same "state a fact, never a judgment" discipline
    narration/service.py's own fallback templates already follow."""
    is_overdue = task.due_at is not None and task.due_at < now
    if not is_overdue:
        return f"• {task.title}"
    return f"• {task.title} — متأخرة" if language == "ar" else f"• {task.title} — overdue"


def _render_tasks(db: Session, space_id: int, intent: DeterministicRetrievalIntent) -> str:
    now = datetime.now(timezone.utc)
    if intent.view == "overdue":
        tasks = tasks_service.list_open_tasks_due_before(db, space_id, now)
    else:
        tasks = tasks_service.list_tasks(db, space_id, status="open")

    if not tasks:
        if intent.view == "overdue":
            return "مفيش عندك مهام متأخرة حاليًا." if intent.language == "ar" else "You have no overdue tasks right now."
        return "مفيش عندك مهام مفتوحة حاليًا." if intent.language == "ar" else "You have no open tasks right now."

    total = len(tasks)
    shown = tasks[:_MAX_DISPLAYED]
    bullets = "\n".join(_format_task_bullet(t, now, intent.language) for t in shown)

    if intent.language == "ar":
        kind = "متأخرة" if intent.view == "overdue" else "مفتوحة"
        header = f"عندك {total} مهمة {kind}{_ar_truncation_note(total, len(shown))}:"
    else:
        kind = "overdue" if intent.view == "overdue" else "open"
        noun = "task" if total == 1 else "tasks"
        header = f"You have {total} {kind} {noun}{_en_truncation_note(total, len(shown))}:"
    return f"{header}\n{bullets}"


def _format_event_bullet(event: CalendarEvent, zone: ZoneInfo) -> str:
    local_start = event.starts_at.astimezone(zone)
    return f"• {local_start.strftime('%H:%M')} — {event.title}"


def _render_calendar(db: Session, space_id: int, intent: DeterministicRetrievalIntent, timezone_name: str) -> str:
    today_start, tomorrow_start = _today_local_bounds(timezone_name)
    # Events STARTING today, in the user's own zone — calendar_service's
    # own existing, public, already-tested query (reused unchanged from
    # Home's Coming Up section); never the broader Tasks+Events agenda
    # merge (build_agenda) — "مواعيد"/"calendar" means appointments,
    # and mixing in Task rows here would answer a question the user
    # didn't narrowly ask (see retrieval_intent.py's own module
    # docstring on the deliberately-excluded broader "what do I have
    # today" phrasing).
    events = calendar_service.list_events_starting_between(db, space_id, today_start, tomorrow_start)

    if not events:
        return "مفيش مواعيد عندك النهارده." if intent.language == "ar" else "You have no events today."

    zone = ZoneInfo(timezone_name)
    total = len(events)
    shown = events[:_MAX_DISPLAYED]
    bullets = "\n".join(_format_event_bullet(e, zone) for e in shown)

    if intent.language == "ar":
        header = f"عندك {total} موعد النهارده{_ar_truncation_note(total, len(shown))}:"
    else:
        noun = "event" if total == 1 else "events"
        header = f"You have {total} {noun} today{_en_truncation_note(total, len(shown))}:"
    return f"{header}\n{bullets}"


def _render_inbox(db: Session, space_id: int, intent: DeterministicRetrievalIntent) -> str:
    items: list[InboxItem] = inbox_service.list_items(db, space_id, unread=True)

    if not items:
        return "مفيش حاجة جديدة في الـInbox دلوقتي." if intent.language == "ar" else "Your inbox has nothing unread right now."

    total = len(items)
    shown = items[:_MAX_DISPLAYED]
    bullets = "\n".join(f"• {i.title}" for i in shown)

    if intent.language == "ar":
        header = f"عندك {total} حاجة جديدة في الـInbox{_ar_truncation_note(total, len(shown))}:"
    else:
        noun = "item" if total == 1 else "items"
        header = f"You have {total} unread {noun} in your inbox{_en_truncation_note(total, len(shown))}:"
    return f"{header}\n{bullets}"


def build_deterministic_retrieval_reply(
    db: Session, space_id: int, content: str, timezone_name: str
) -> str | None:
    """The single public entry point. Returns None for anything not
    recognized (the overwhelming majority of messages) — the caller
    (chat/service.py's send_message) falls through to the existing
    Context Assembly / Orchestrator / Model Router path exactly as
    before this checkpoint. Makes zero model_router/orchestrator calls
    on either branch; a database/domain query failure here propagates
    as an ordinary, unhandled exception — the SAME application-error
    behavior any other direct-REST domain query failure already has,
    never recast as a "model unavailable" failure (that normalized
    taxonomy, Checkpoint 5.1, is specific to provider/model failures
    and must not be reused for an unrelated local DB error).
    """
    intent = detect_deterministic_retrieval_intent(content)
    if intent is None:
        return None
    if intent.domain == "tasks":
        return _render_tasks(db, space_id, intent)
    if intent.domain == "calendar":
        return _render_calendar(db, space_id, intent, timezone_name)
    return _render_inbox(db, space_id, intent)
