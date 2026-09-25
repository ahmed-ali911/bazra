from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.memory.models import Memory
from app.modules.memory.schemas import MemoryCreate

_DEFAULT_RETRIEVAL_LIMIT = 20


def create_memory(
    db: Session,
    space_id: int,
    user_id: int,
    source_chat_message_id: int,
    data: MemoryCreate,
    commit: bool = True,
) -> Memory:
    """commit=False lets this be created atomically together with the
    ProposedAction that describes it and, when data.supersedes_memory_id
    is set, together with that old memory's supersession — all in the
    single transaction actions_service.confirm_and_execute already owns.
    """
    memory = Memory(
        space_id=space_id,
        user_id=user_id,
        source_chat_message_id=source_chat_message_id,
        type=data.type,
        content=data.content,
        status="active",
    )
    db.add(memory)
    if commit:
        db.commit()
        db.refresh(memory)
    else:
        db.flush()
    return memory


def supersede_memory(db: Session, space_id: int, user_id: int, old_memory_id: int, new_memory_id: int) -> bool:
    """Flips old_memory_id -> superseded, pointing at new_memory_id.
    Re-verifies ownership AND that it's still active inside whatever
    transaction the caller is in — never trusts a prior read (the same
    "never trust a prior read" discipline already established for
    ProposedAction's row-locked confirm_and_execute). Returns whether a
    row actually matched, so a caller that explicitly asked to supersede
    something that's already gone can treat that as a real failure
    rather than silently succeeding at nothing.
    """
    result = db.execute(
        update(Memory)
        .where(
            Memory.id == old_memory_id,
            Memory.space_id == space_id,
            Memory.user_id == user_id,
            Memory.status == "active",
        )
        .values(status="superseded", superseded_by_id=new_memory_id)
        .returning(Memory.id)
    ).first()
    return result is not None


def forget_memory(db: Session, space_id: int, user_id: int, memory_id: int) -> Memory | None:
    """Same conditional-UPDATE-with-RETURNING shape as
    confirm_and_execute's own row-lock pattern — returns the now-
    forgotten row (so the caller can build a confirmation/result from
    it without a second query), or None if nothing matched. A memory
    already forgotten/superseded by the time this runs is NOT an error
    to the caller directly; actions_service.confirm_and_execute treats
    a None result as an execution failure (rolled back to pending,
    safely re-confirmable), the same precedented outcome as any other
    execution-time failure.
    """
    return db.execute(
        update(Memory)
        .where(
            Memory.id == memory_id,
            Memory.space_id == space_id,
            Memory.user_id == user_id,
            Memory.status == "active",
        )
        .values(status="forgotten")
        .returning(Memory)
    ).scalars().first()


def get_active_memory_for_user(db: Session, space_id: int, user_id: int, memory_id: int) -> Memory | None:
    """Used both to resolve a proposal-time reference (supersedes_memory_id
    / forget's memory_id) and to re-verify at confirm-time. A single
    query collapses three distinct real-world cases — the id doesn't
    exist at all, it belongs to another user/space, or it's no longer
    active — into one safe None, exactly as intended: the caller can't
    (and doesn't need to) tell those apart.
    """
    return db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.space_id == space_id,
            Memory.user_id == user_id,
            Memory.status == "active",
        )
    ).scalars().first()


def get_relevant_memories(
    db: Session, space_id: int, user_id: int, limit: int = _DEFAULT_RETRIEVAL_LIMIT
) -> tuple[list[Memory], int]:
    """Structured retrieval only (Checkpoint 3.4 architecture decision —
    no embeddings/pgvector): active memories, most recent first, capped.
    Returns (returned_rows, total_active_count) so the caller can
    honestly disclose truncation ("...and N more not shown") the same
    way chat/context.py's _format_section already does for Home data,
    rather than presenting a capped list as if it were complete.
    """
    total = db.execute(
        select(func.count()).select_from(Memory).where(
            Memory.space_id == space_id, Memory.user_id == user_id, Memory.status == "active"
        )
    ).scalar_one()
    rows = (
        db.execute(
            select(Memory)
            .where(Memory.space_id == space_id, Memory.user_id == user_id, Memory.status == "active")
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows), total
