from datetime import datetime, timedelta, timezone

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.actions.models import ProposedAction
from app.modules.actions.schemas import ConfirmResult
from app.modules.calendar import service as calendar_service
from app.modules.calendar.schemas import CalendarEventResponse, ProposedCalendarEventCreate
from app.modules.chat.models import ChatMessage
from app.modules.life_areas import service as life_areas_service
from app.modules.memory import service as memory_service
from app.modules.memory.schemas import MemoryCreate, MemoryForget, MemoryResponse
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import ProposedTaskDelete, ProposedTaskUpdate, TaskCreate, TaskResponse, TaskUpdate

_DEFAULT_TTL_MINUTES = 10

# One Pydantic schema per action_type — the single source of truth for
# what "valid arguments" means for each, shared between validate_arguments
# below and confirm_and_execute's own re-parse at execution time.
_ACTION_ARGUMENT_SCHEMAS: dict[str, type[PydanticBaseModel]] = {
    "create_task": TaskCreate,
    "save_memory": MemoryCreate,
    "forget_memory": MemoryForget,
    "update_task": ProposedTaskUpdate,
    "delete_task": ProposedTaskDelete,
    "create_event": ProposedCalendarEventCreate,
}


class InvalidActionArgumentsError(Exception):
    """Raised when model-generated arguments don't validate against the
    relevant domain schema — no ProposedAction row is ever created in
    this case; the caller (Chat) responds honestly rather than storing
    (or later executing) a malformed proposal."""

    def __init__(self, action_type: str, detail: str):
        self.action_type = action_type
        self.detail = detail
        super().__init__(f"Invalid arguments for {action_type}: {detail}")


def validate_arguments(action_type: str, arguments: dict) -> dict:
    """Public (Checkpoint 3.3 addendum): chat_service calls this
    directly, before creating any row, so the deterministic proposal
    confirmation it shows the user can be rendered from the SAME
    validated data that create_pending_action below will then store —
    never from the model's own free-form reply text. Returns a plain,
    JSON-safe dict (datetimes as ISO strings) suitable for JSONB
    storage; the matching schema parses that representation back on the
    way out, so this round-trips correctly.

    Checkpoint 3.4 note: this stays a PURE, DB-free shape validator —
    correct for create_task and save_memory's own scalar fields, but
    NOT sufficient on its own for forget_memory or save_memory's
    optional supersedes_memory_id, both of which reference an EXISTING
    memory row. Resolving those references (does this id exist, is it
    active, is it owned by this space/user) requires a database lookup
    and lives in chat_service, right after this shape check succeeds —
    see chat/service.py's _require_active_memory.

    Checkpoint 3.10 note: exclude_unset=True on the dump below is
    load-bearing, not cosmetic — for create_task/save_memory it is a
    no-op (an omitted optional field and an explicit null already mean
    the same thing for a brand-new row), but for update_task it is what
    keeps a PARTIAL update partial through the JSONB round-trip:
    without it, every optional field the model didn't mention would be
    dumped as an explicit null and, reconstructed at confirm time,
    would be indistinguishable from "the model explicitly asked to
    clear this field" — silently wiping title/description/due_at/
    life_area_id on every update_task confirmation that didn't repeat
    them all. See tasks/schemas.py's ProposedTaskUpdate and this
    module's confirm_and_execute update_task branch.
    """
    schema = _ACTION_ARGUMENT_SCHEMAS.get(action_type)
    if schema is None:
        raise InvalidActionArgumentsError(action_type, f"unknown action_type {action_type!r}")
    try:
        validated = schema(**arguments)
    except ValidationError as exc:
        raise InvalidActionArgumentsError(action_type, str(exc)) from exc
    return validated.model_dump(mode="json", exclude_unset=True)


def _supersede_existing_pending(db: Session, space_id: int, user_id: int) -> None:
    """Enforces the invariant directly, rather than handling a race: at
    most one 'pending' row can exist per (space_id, user_id) at a time.
    Any existing pending row is superseded before a new one (fresh or
    revised) is created, so "the most recent pending proposal" is
    always unambiguous by construction.
    """
    db.execute(
        update(ProposedAction)
        .where(
            ProposedAction.space_id == space_id,
            ProposedAction.user_id == user_id,
            ProposedAction.status == "pending",
        )
        .values(status="superseded")
    )


def create_pending_action(
    db: Session,
    space_id: int,
    user_id: int,
    source_chat_message_id: int,
    action_type: str,
    arguments: dict,
    ttl_minutes: int = _DEFAULT_TTL_MINUTES,
    commit: bool = True,
) -> ProposedAction:
    """commit=False lets the caller (chat_service.send_message) create
    this row atomically together with the assistant ChatMessage that
    describes it — the same Checkpoint 2.4 Inbox-atomicity pattern,
    applied here so a proposal-describing message can never exist
    without its corresponding row, or vice versa.
    """
    validated_arguments = validate_arguments(action_type, arguments)  # raises BEFORE any row is touched

    _supersede_existing_pending(db, space_id, user_id)

    proposal = ProposedAction(
        space_id=space_id,
        user_id=user_id,
        source_chat_message_id=source_chat_message_id,
        action_type=action_type,
        arguments=validated_arguments,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )
    db.add(proposal)
    if commit:
        db.commit()
        db.refresh(proposal)
    else:
        db.flush()
    return proposal


def get_latest_pending(db: Session, space_id: int, user_id: int) -> ProposedAction | None:
    return db.execute(
        select(ProposedAction)
        .where(
            ProposedAction.space_id == space_id,
            ProposedAction.user_id == user_id,
            ProposedAction.status == "pending",
            ProposedAction.expires_at > func.now(),
        )
        .order_by(ProposedAction.id.desc())
    ).scalars().first()


def is_still_conversationally_adjacent(
    db: Session, space_id: int, user_id: int, pending: ProposedAction, current_user_message_id: int,
) -> bool:
    """Checkpoint 3.17 — the deterministic eligibility guard for bare
    yes/no dispatch: a pending proposal may only be confirmed/rejected
    without a model call when NOTHING has been said in this
    conversation since its own confirmation prompt.

    Uses `pending.source_chat_message_id` exactly as it already is —
    every _handle_*_proposal call site (create_task, update_task,
    delete_task, create_event, save_memory, forget_memory) has always
    passed the ASSISTANT's own confirmation-prompt ChatMessage id there,
    never the user's triggering message. No new column: this linkage
    already existed under this name.

    Adjacent means: no ChatMessage row (of any role, on any topic)
    exists strictly between that confirmation prompt and the CURRENT
    incoming message. A plain range query answering this is safe here
    ONLY because the caller (chat/service.py's send_message) holds this
    (space_id, user_id)'s conversation advisory lock for the caller's
    entire critical section — see _acquire_conversation_lock's own
    docstring for the forced-interleaving experiment that proved this
    same query UNSAFE without that lock: a concurrently-inserted, lower-
    id row can stay invisible to a reader for as long as its own
    transaction remains open, producing a false "adjacent" conclusion
    for a message that, moments later, would correctly count as
    intervening. This function does not (and structurally cannot)
    acquire that lock itself — it trusts the caller already holds it.
    """
    intervening_count = db.execute(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.space_id == space_id,
            ChatMessage.user_id == user_id,
            ChatMessage.id > pending.source_chat_message_id,
            ChatMessage.id < current_user_message_id,
        )
    ).scalar_one()
    return intervening_count == 0


def reject(db: Session, space_id: int, user_id: int) -> bool:
    """Returns whether a pending proposal was actually rejected (False
    if there was nothing pending/it had already expired) — the same
    "nothing pending" case the caller handles honestly rather than
    silently."""
    result = db.execute(
        update(ProposedAction)
        .where(
            ProposedAction.space_id == space_id,
            ProposedAction.user_id == user_id,
            ProposedAction.status == "pending",
            ProposedAction.expires_at > func.now(),
        )
        .values(status="rejected")
        .returning(ProposedAction.id)
    ).first()
    db.commit()
    return result is not None


def confirm_and_execute(db: Session, space_id: int, user_id: int) -> ConfirmResult:
    """Single transaction, covering both the confirm-claim and the real
    execution — either everything commits together (the domain effect
    for this row's action_type happens, proposal 'executed', the
    matching executed_task_id/executed_memory_id set) or everything
    rolls back and the proposal is left exactly as it was.

    The conditional UPDATE below (WHERE status='pending') is the replay
    guard: it takes a real Postgres row-level lock the moment it
    matches, so a concurrent second confirmation attempt against the
    same row BLOCKS until this transaction resolves, then re-evaluates
    against the now-committed state — 'executed' (matches nothing,
    can't re-execute) or 'pending' again on failure (see below).

    Deliberately no 'failed' terminal status: on an execution exception,
    this transaction rolls back in full, INCLUDING the confirmed
    transition — the proposal reverts to genuinely 'pending', safely
    re-confirmable. This is the smaller design: the retry mechanism is
    just "say yes again", not a separate state machine. A useful,
    non-bespoke side effect: a second confirmation that was blocked
    waiting behind a failing first attempt will see 'pending' once the
    first rolls back, and can itself succeed — a natural retry, not
    built specially.

    Checkpoint 3.4: dispatches on the row's OWN action_type, read after
    the row-lock is already won — not on any assumption from proposal
    time about which type would be pending. A forget_memory or
    save_memory-with-supersedes whose target has gone inactive between
    proposal and confirmation (memory_service returns False) is treated
    exactly like any other execution failure: raised, caught below,
    rolled back to 'pending' — no bespoke handling needed for that race.
    """
    stmt = (
        update(ProposedAction)
        .where(
            ProposedAction.space_id == space_id,
            ProposedAction.user_id == user_id,
            ProposedAction.status == "pending",
            ProposedAction.expires_at > func.now(),
        )
        .values(status="confirmed")
        .returning(ProposedAction)
    )
    proposal = db.execute(stmt).scalars().first()

    if proposal is None:
        return ConfirmResult(outcome="nothing_pending")

    try:
        if proposal.action_type == "create_task":
            task_data = TaskCreate(**proposal.arguments)
            task = tasks_service.create_task(db, space_id, task_data, commit=False)
            proposal.status = "executed"
            proposal.executed_task_id = task.id
            db.commit()
            return ConfirmResult(outcome="executed", task=TaskResponse.model_validate(task), task_action="created")

        if proposal.action_type == "update_task":
            # ProposedTaskUpdate(**proposal.arguments) reconstructs
            # model_fields_set correctly from the sparse (exclude_unset)
            # stored dict — only fields the model actually named are
            # "set", so re-dumping with exclude_unset below and handing
            # THAT to TaskUpdate produces a genuinely partial update,
            # never a silent wipe of untouched fields. completed_at is
            # never in this data at all (ProposedTaskUpdate has no such
            # field) — tasks_service.update_task derives it itself, the
            # exact same server-side-only rule the direct REST path uses.
            update_data = ProposedTaskUpdate(**proposal.arguments)
            task_update = TaskUpdate(**update_data.model_dump(exclude={"task_id"}, exclude_unset=True))
            task = tasks_service.update_task(db, space_id, update_data.task_id, task_update)
            if task is None:
                raise RuntimeError(f"task_id {update_data.task_id} no longer exists")
            proposal.status = "executed"
            proposal.executed_task_id = task.id
            db.commit()
            return ConfirmResult(outcome="executed", task=TaskResponse.model_validate(task), task_action="updated")

        if proposal.action_type == "delete_task":
            # Fetched BEFORE delete_task runs, deliberately: delete_task
            # sets archived_at and its own internal get_task lookup
            # filters archived_at IS NULL, so a task_id fetched AFTER
            # deletion would no longer resolve. This fetch doubles as
            # the execution-time revalidation — task
            # archived/removed/reassigned to another space between
            # proposal and confirmation all collapse into the same
            # "no longer exists" None here, exactly like update_task's
            # own None check above.
            delete_data = ProposedTaskDelete(**proposal.arguments)
            task = tasks_service.get_task(db, space_id, delete_data.task_id)
            if task is None:
                raise RuntimeError(f"task_id {delete_data.task_id} no longer exists")
            tasks_service.delete_task(db, space_id, delete_data.task_id)
            proposal.status = "executed"
            proposal.executed_task_id = task.id
            db.commit()
            return ConfirmResult(outcome="executed", task=TaskResponse.model_validate(task), task_action="deleted")

        if proposal.action_type == "create_event":
            # Re-parsed through the SAME ProposedCalendarEventCreate
            # schema used at proposal time (not the plain
            # CalendarEventCreate) — timezone-awareness and range are
            # revalidated here too, not merely trusted from proposal
            # time, the same "re-parse, don't just trust" discipline as
            # every other branch above. life_area_id (if present) is the
            # only meaningful execution-time race for a brand-new row:
            # there is no existing target that could have been archived
            # or reassigned, only a referenced Life Area that could have
            # been deleted between proposal and confirmation.
            event_data = ProposedCalendarEventCreate(**proposal.arguments)
            if event_data.life_area_id is not None:
                if life_areas_service.get_life_area(db, event_data.life_area_id) is None:
                    raise RuntimeError(f"life_area_id {event_data.life_area_id} no longer exists")
            event = calendar_service.create_calendar_event(db, space_id, event_data)
            proposal.status = "executed"
            # No executed_event_id column exists on ProposedAction (adding
            # one would be a migration, out of this checkpoint's scope) —
            # ConfirmResult.event below is the only record of which
            # CalendarEvent this proposal produced.
            db.commit()
            return ConfirmResult(outcome="executed", event=CalendarEventResponse.model_validate(event))

        if proposal.action_type == "save_memory":
            memory_data = MemoryCreate(**proposal.arguments)
            memory = memory_service.create_memory(
                db, space_id, user_id, proposal.source_chat_message_id, memory_data, commit=False
            )
            if memory_data.supersedes_memory_id is not None:
                superseded = memory_service.supersede_memory(
                    db, space_id, user_id, memory_data.supersedes_memory_id, memory.id
                )
                if not superseded:
                    raise RuntimeError(
                        f"supersedes_memory_id {memory_data.supersedes_memory_id} is no longer active"
                    )
            proposal.status = "executed"
            proposal.executed_memory_id = memory.id
            db.commit()
            return ConfirmResult(outcome="executed", memory=MemoryResponse.model_validate(memory))

        if proposal.action_type == "forget_memory":
            forget_data = MemoryForget(**proposal.arguments)
            forgotten_memory = memory_service.forget_memory(db, space_id, user_id, forget_data.memory_id)
            if forgotten_memory is None:
                raise RuntimeError(f"memory_id {forget_data.memory_id} is no longer active")
            proposal.status = "executed"
            proposal.executed_memory_id = forgotten_memory.id
            db.commit()
            return ConfirmResult(outcome="executed", memory=MemoryResponse.model_validate(forgotten_memory))

        raise RuntimeError(f"unknown action_type {proposal.action_type!r}")
    except Exception:
        db.rollback()
        return ConfirmResult(outcome="execution_failed")
