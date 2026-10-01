from datetime import datetime, timedelta, timezone

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.actions.models import ProposedAction
from app.modules.actions.schemas import ConfirmResult
from app.modules.calendar import service as calendar_service
from app.modules.calendar.schemas import (
    CalendarEventResponse,
    CalendarEventUpdate,
    ProposedCalendarEventCreate,
    ProposedCalendarEventDelete,
    ProposedCalendarEventUpdate,
)
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
    "update_event": ProposedCalendarEventUpdate,
    "delete_event": ProposedCalendarEventDelete,
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
    """Single authoritative transaction, covering the confirm-claim, the
    real domain execution, and the final executed-state transition —
    either everything commits together in ONE db.commit() (the domain
    effect for this row's action_type happens, proposal 'executed', the
    matching executed_task_id/executed_memory_id set) or everything
    rolls back and the proposal is left exactly as it was: genuinely
    'pending', never durably stuck at 'confirmed'.

    Checkpoint 3.H2 — this used to call several domain service functions
    with no way to defer their OWN internal commit, producing two
    separate durable commit boundaries per call (one inside the domain
    service, one here) for update_task/delete_task/create_event/
    update_event/delete_event. A real-Postgres inspection proved that
    created a narrow, genuinely reachable window: if THIS function's own
    second commit failed after the domain service's own first commit had
    already succeeded, the database would durably show the domain
    mutation as real while the ProposedAction was wedged at 'confirmed'
    forever — not 'pending' (so the normal "say yes again" retry could
    never reach it), not 'executed'. Every domain service called below
    now accepts commit=False (the exact same escape hatch
    tasks_service.create_task/memory_service.create_memory already
    established) and performs ONLY a db.flush() — this function is the
    ONLY place that ever commits the authoritative part of this
    transaction, exactly once, below.

    'confirmed' (the status this row holds throughout everything below)
    is therefore transient, in-transaction-only, by design — never
    observed durably outside this function except in the window between
    the claim UPDATE below and this function's own single commit/
    rollback. No external code, schema, test, or API response reads or
    exposes a durable 'confirmed' value (confirmed by repository-wide
    search during the 3.H1 inspection).

    The conditional UPDATE below (WHERE status='pending') is the replay
    guard: it takes a real Postgres row-level lock the moment it
    matches, so a concurrent second confirmation attempt against the
    same row BLOCKS until this transaction resolves, then re-evaluates
    against the now-committed state — 'executed' (matches nothing,
    can't re-execute) or 'pending' again on failure (see below). That
    lock, and the per-(space_id, user_id) Postgres advisory lock
    chat_service already holds for the whole turn, are now both held for
    this function's ENTIRE duration — the single-commit change also
    closes a previously-real gap where the advisory lock was released
    partway through a single logical confirmation (at the domain
    service's own old internal commit), before this function's own
    second commit had even run.

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

    Checkpoint 3.H2 — response-model construction (TaskResponse.
    model_validate and its siblings) is deliberately performed AFTER
    this function's own try/except has already exited successfully, not
    inside the return statement of a branch above the commit (which is
    where it used to live) — so a hypothetical presentation-layer
    exception can never be caught by the authoritative except block
    below and misreported as outcome="execution_failed" for an action
    that is, by that point, already durably executed. Nothing about the
    authoritative transaction's own success/failure depends on whether
    this later step succeeds.
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

    task = None
    event = None
    memory = None
    task_action = None
    event_action = None

    try:
        if proposal.action_type == "create_task":
            task_data = TaskCreate(**proposal.arguments)
            task = tasks_service.create_task(db, space_id, task_data, commit=False)
            proposal.status = "executed"
            proposal.executed_task_id = task.id
            task_action = "created"

        elif proposal.action_type == "update_task":
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
            task = tasks_service.update_task(db, space_id, update_data.task_id, task_update, commit=False)
            if task is None:
                raise RuntimeError(f"task_id {update_data.task_id} no longer exists")
            proposal.status = "executed"
            proposal.executed_task_id = task.id
            task_action = "updated"

        elif proposal.action_type == "delete_task":
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
            tasks_service.delete_task(db, space_id, delete_data.task_id, commit=False)
            proposal.status = "executed"
            proposal.executed_task_id = task.id
            task_action = "deleted"

        elif proposal.action_type == "create_event":
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
            event = calendar_service.create_calendar_event(db, space_id, event_data, commit=False)
            proposal.status = "executed"
            # No executed_event_id column exists on ProposedAction (adding
            # one would be a migration, out of this checkpoint's scope) —
            # the response's own event field below is the only record of
            # which CalendarEvent this proposal produced.
            event_action = "created"

        elif proposal.action_type == "update_event":
            # Checkpoint 3.18 — same "re-parse, don't just trust"
            # discipline as update_task: ProposedCalendarEventUpdate
            # reconstructs model_fields_set correctly from the sparse
            # (exclude_unset) stored dict, so re-dumping with
            # exclude_unset below and handing THAT to CalendarEventUpdate
            # produces a genuinely partial update, never a silent wipe of
            # untouched fields. life_area_id (if present) is revalidated
            # for the same reason as create_event's own branch above.
            # calendar_service.update_calendar_event itself performs the
            # authoritative merged-state temporal validation (raises
            # ValueError, caught by the shared except below) — no
            # duplicate check needed here. Per the approved event-drift
            # decision: the stored final patch is applied against
            # whatever the event's CURRENT state is (re-fetched fresh by
            # update_calendar_event itself), exactly like update_task's
            # own already-accepted "last write wins" behavior — no
            # optimistic concurrency/versioning.
            update_data = ProposedCalendarEventUpdate(**proposal.arguments)
            if update_data.life_area_id is not None:
                if life_areas_service.get_life_area(db, update_data.life_area_id) is None:
                    raise RuntimeError(f"life_area_id {update_data.life_area_id} no longer exists")
            event_update = CalendarEventUpdate(**update_data.model_dump(exclude={"event_id"}, exclude_unset=True))
            event = calendar_service.update_calendar_event(
                db, space_id, update_data.event_id, event_update, commit=False
            )
            if event is None:
                raise RuntimeError(f"event_id {update_data.event_id} no longer exists")
            proposal.status = "executed"
            event_action = "updated"

        elif proposal.action_type == "delete_event":
            # Fetched BEFORE delete_calendar_event runs, deliberately —
            # the exact same "fetch doubles as execution-time
            # revalidation" pattern as delete_task's own branch above:
            # delete_calendar_event sets archived_at and its own
            # internal get_calendar_event lookup filters archived_at IS
            # NULL, so an event_id fetched AFTER deletion would no
            # longer resolve. Event drift on OTHER fields (title/time/
            # life_area, changed by another path since the proposal was
            # created) is irrelevant here by design (Part 17 of the
            # approved design report) — deletion only cares about
            # identity/existence, never field values, so it proceeds
            # against whatever the event's current state is as long as
            # it's still active.
            delete_data = ProposedCalendarEventDelete(**proposal.arguments)
            event = calendar_service.get_calendar_event(db, space_id, delete_data.event_id)
            if event is None:
                raise RuntimeError(f"event_id {delete_data.event_id} no longer exists")
            calendar_service.delete_calendar_event(db, space_id, delete_data.event_id, commit=False)
            proposal.status = "executed"
            event_action = "deleted"

        elif proposal.action_type == "save_memory":
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

        elif proposal.action_type == "forget_memory":
            forget_data = MemoryForget(**proposal.arguments)
            forgotten_memory = memory_service.forget_memory(db, space_id, user_id, forget_data.memory_id)
            if forgotten_memory is None:
                raise RuntimeError(f"memory_id {forget_data.memory_id} is no longer active")
            proposal.status = "executed"
            proposal.executed_memory_id = forgotten_memory.id
            memory = forgotten_memory

        else:
            raise RuntimeError(f"unknown action_type {proposal.action_type!r}")

        # Checkpoint 3.H2 — response-model validation happens HERE,
        # deliberately still inside the try block, strictly BEFORE the
        # single authoritative commit below: it is pure/deterministic
        # (ConfigDict(from_attributes=True) reading already-flushed
        # Python attributes, no I/O, no further DB mutation), so
        # performing it now means a genuine validation problem is
        # honestly treated as part of the authoritative failure (full
        # rollback, correctly reported as execution_failed — nothing
        # was durably committed, so nothing is misreported), rather than
        # deferred to after a commit it could then falsely contradict.
        # This leaves NOTHING fallible between the commit below and the
        # function returning.
        result_task = TaskResponse.model_validate(task) if task is not None else None
        result_event = CalendarEventResponse.model_validate(event) if event is not None else None
        result_memory = MemoryResponse.model_validate(memory) if memory is not None else None

        # The ONE shared authoritative commit — covers the confirmed
        # claim, the domain mutation (flushed, never yet committed, by
        # every branch above), any Attention attribution (already
        # isolated in its own SAVEPOINT inside the domain call), and the
        # executed-state transition, all atomically.
        db.commit()
    except Exception:
        db.rollback()
        return ConfirmResult(outcome="execution_failed")

    # Nothing below can affect the authoritative truth established
    # above: the commit already succeeded, unconditionally, by the time
    # execution reaches here. A hypothetical failure constructing the
    # final ConfirmResult itself would propagate as a raw exception,
    # never be caught by the except block above, and never be
    # misreported as execution_failed for an action that is, by this
    # point, already durably executed.
    if result_task is not None:
        return ConfirmResult(outcome="executed", task=result_task, task_action=task_action)
    if result_event is not None:
        return ConfirmResult(outcome="executed", event=result_event, event_action=event_action)
    return ConfirmResult(outcome="executed", memory=result_memory)
