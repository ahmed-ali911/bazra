from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.actions.models import ProposedAction
from app.modules.actions.schemas import ConfirmResult
from app.modules.tasks import service as tasks_service
from app.modules.tasks.schemas import TaskCreate, TaskResponse

_DEFAULT_TTL_MINUTES = 10


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
    storage; TaskCreate parses ISO strings back into real datetimes on
    the way out, so this round-trips correctly.
    """
    if action_type == "create_task":
        try:
            validated = TaskCreate(**arguments)
        except ValidationError as exc:
            raise InvalidActionArgumentsError(action_type, str(exc)) from exc
        return validated.model_dump(mode="json")
    raise InvalidActionArgumentsError(action_type, f"unknown action_type {action_type!r}")


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
    execution — either everything commits together (Task created,
    proposal 'executed', executed_task_id set) or everything rolls back
    and the proposal is left exactly as it was.

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
        task_data = TaskCreate(**proposal.arguments)
        task = tasks_service.create_task(db, space_id, task_data, commit=False)
        proposal.status = "executed"
        proposal.executed_task_id = task.id
        db.commit()
        return ConfirmResult(outcome="executed", task=TaskResponse.model_validate(task))
    except Exception:
        db.rollback()
        return ConfirmResult(outcome="execution_failed")
