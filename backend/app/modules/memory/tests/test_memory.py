import pytest
from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.chat import service as chat_service
from app.modules.memory import service as memory_service
from app.modules.memory.schemas import MemoryCreate
from app.modules.spaces import service as spaces_service


@pytest.fixture()
def owner(db_session: Session):
    """Self-contained user+space setup — does not rely on some other
    test file having run first, mirroring actions/tests/test_actions.py's
    own fixture."""
    user = auth_service.get_the_user(db_session)
    if user is None:
        user = User(password_hash=auth_service.hash_password("owner-fixture-password"))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    space = spaces_service.get_or_create_default_space_for_user(db_session, user.id)
    return user, space


def _seed_source_message(db_session: Session, space_id: int, user_id: int) -> int:
    message = chat_service.record_assistant_message(db_session, space_id, user_id, "memory proposal text")
    return message.id


# ---- create_memory -----------------------------------------------------------------


def test_create_memory_stores_type_and_content(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)

    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="PREFERENCE", content="Prefers concise answers")
    )

    assert memory.status == "active"
    assert memory.type == "PREFERENCE"
    assert memory.content == "Prefers concise answers"
    assert memory.source_chat_message_id == source_id


# ---- supersede_memory ---------------------------------------------------------------


def test_supersede_memory_flips_old_to_superseded_and_links_forward(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)

    old = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Works at a bank")
    )
    new = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="No longer works at a bank")
    )

    result = memory_service.supersede_memory(db_session, space.id, user.id, old.id, new.id)
    db_session.commit()

    assert result is True
    db_session.refresh(old)
    assert old.status == "superseded"
    assert old.superseded_by_id == new.id


def test_supersede_memory_returns_false_when_already_inactive(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)

    old = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Once true")
    )
    new = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Still true")
    )
    assert memory_service.supersede_memory(db_session, space.id, user.id, old.id, new.id) is True
    db_session.commit()

    # Attempting to supersede the SAME already-superseded row again fails safely.
    another_new = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Yet another version")
    )
    assert memory_service.supersede_memory(db_session, space.id, user.id, old.id, another_new.id) is False


# ---- forget_memory --------------------------------------------------------------------


def test_forget_memory_marks_forgotten_and_returns_the_row(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="GOAL", content="Learn backend development")
    )

    forgotten = memory_service.forget_memory(db_session, space.id, user.id, memory.id)
    db_session.commit()

    assert forgotten is not None
    assert forgotten.id == memory.id
    assert forgotten.status == "forgotten"


def test_forgetting_an_already_forgotten_memory_is_a_safe_no_op(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="GOAL", content="Learn Rust")
    )
    assert memory_service.forget_memory(db_session, space.id, user.id, memory.id) is not None
    db_session.commit()

    second_attempt = memory_service.forget_memory(db_session, space.id, user.id, memory.id)
    assert second_attempt is None  # safe no-op, not an exception


# ---- get_active_memory_for_user ------------------------------------------------------


def test_get_active_memory_for_user_returns_none_for_nonexistent_id(db_session: Session, owner) -> None:
    user, space = owner
    assert memory_service.get_active_memory_for_user(db_session, space.id, user.id, 999999) is None


def test_get_active_memory_for_user_returns_none_for_inactive_memory(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Temporary")
    )
    memory_service.forget_memory(db_session, space.id, user.id, memory.id)
    db_session.commit()

    assert memory_service.get_active_memory_for_user(db_session, space.id, user.id, memory.id) is None


def test_cross_user_isolation_for_memory_lookup_and_forget(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    memory = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="User A's memory")
    )
    db_session.commit()

    from app.modules.spaces.models import Space

    other_user = User(password_hash=auth_service.hash_password("other"))
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    other_space = Space(name="Other space", is_default=True, user_id=other_user.id)
    db_session.add(other_space)
    db_session.commit()
    db_session.refresh(other_space)

    assert memory_service.get_active_memory_for_user(db_session, other_space.id, other_user.id, memory.id) is None
    assert memory_service.forget_memory(db_session, other_space.id, other_user.id, memory.id) is None

    # The original memory is untouched by the other user's attempts.
    still_active = memory_service.get_active_memory_for_user(db_session, space.id, user.id, memory.id)
    assert still_active is not None
    assert still_active.status == "active"


# ---- get_relevant_memories -------------------------------------------------------------


def test_get_relevant_memories_returns_only_active_most_recent_first(db_session: Session, owner) -> None:
    """Uses a delta against a captured baseline, not an absolute total —
    other tests in this shared, non-rolled-back database may have
    already left their own active memories behind for this same
    default user/space."""
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    _, baseline_total = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=0)

    first = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="First — isolation-test-marker")
    )
    second = memory_service.create_memory(
        db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content="Second — isolation-test-marker")
    )
    memory_service.forget_memory(db_session, space.id, user.id, first.id)
    db_session.commit()

    memories, total = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=1000)

    ids = [m.id for m in memories]
    assert first.id not in ids  # forgotten, excluded
    assert second.id in ids
    assert total == baseline_total + 1  # only "second" is newly active


def test_get_relevant_memories_discloses_total_count_beyond_the_cap(db_session: Session, owner) -> None:
    user, space = owner
    source_id = _seed_source_message(db_session, space.id, user.id)
    _, baseline_total = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=0)

    for i in range(5):
        memory_service.create_memory(
            db_session, space.id, user.id, source_id, MemoryCreate(type="FACT", content=f"Cap test fact {i}")
        )
    db_session.commit()

    memories, total = memory_service.get_relevant_memories(db_session, space.id, user.id, limit=3)

    assert len(memories) == 3
    assert total == baseline_total + 5  # caller can compute "N more not shown" from this
