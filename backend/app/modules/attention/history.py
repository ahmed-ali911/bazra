"""Checkpoint 4.4a — Attention exposure persistence.

The one place Attention state becomes durable. Signal (4.2) and
AttentionCandidate (4.3) both remain fully live-derived and
unpersisted; this module only ever records that a candidate was
ACTUALLY DELIVERED (record_exposure) and reconstructs 4.3's
SuppressionState read model from that durable history
(load_suppression_states).

Deliberately NOT implemented here (later checkpoints own these):
- snooze/dismiss mutation APIs (4.4b)
- the deterministic ACTED_ON evaluator (4.4c)
- any surfacing consumer (APP_OPENED / Daily Brief delivery) that
  would actually call record_exposure in production.

No Model Router/Anthropic/Orchestrator call, no ProposedAction
interaction, no proactive ChatMessage — this module is as
deterministic and persistence-boundary-local as actions_service's own
create_pending_action.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.attention import scoring
from app.modules.attention.models import AttentionExposure
from app.modules.attention.schemas import (
    SIGNAL_TYPE_ORDER,
    AttentionCandidate,
    AttentionSurface,
    SourceType,
    SuppressionState,
)
from app.modules.calendar import service as calendar_service
from app.modules.inbox import service as inbox_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service

_VALID_SURFACES: frozenset[str] = frozenset(("app_opened", "daily_brief"))

_SOURCE_COLUMN_BY_TYPE: dict[SourceType, object] = {
    "task": AttentionExposure.task_id,
    "calendar_event": AttentionExposure.event_id,
    "inbox_item": AttentionExposure.inbox_item_id,
}
_SOURCE_ATTR_BY_TYPE: dict[SourceType, str] = {
    "task": "task_id",
    "calendar_event": "event_id",
    "inbox_item": "inbox_item_id",
}


class AttentionExposureError(Exception):
    """Raised when record_exposure is asked to persist structurally
    invalid data (an unsupported surface or signal_type) — a defensive
    boundary check, since the AttentionCandidate scoring.score_signal
    already validates is trusted but this is the actual persistence
    boundary and must never silently write an unrecognized value
    (§I: "Validate persisted enums/value sets at the application
    boundary")."""


class AttentionOwnershipError(AttentionExposureError):
    """Raised when the caller-supplied (space_id, user_id, source)
    combination cannot be verified: the space does not belong to this
    user, or the referenced Task/CalendarEvent/InboxItem does not exist
    in this space. Never trust a caller-provided source id merely
    because it exists somewhere — always re-verify through this
    source's own scoped getter."""


def _require_space_owned_by_user(db: Session, space_id: int, user_id: int) -> None:
    space = db.get(Space, space_id)
    if space is None or space.user_id != user_id:
        raise AttentionOwnershipError(f"space_id={space_id} is not owned by user_id={user_id}")


def _require_source_exists_in_space(db: Session, space_id: int, source_type: SourceType, source_id: int) -> None:
    """Re-verifies the referenced source row through its OWN module's
    scoped getter (never a bare id check) — exactly the same
    "a row belonging to a different space is indistinguishable from one
    that doesn't exist" discipline core/space_scoping.py's own docstring
    establishes. A row that doesn't exist in THIS space (whether it
    belongs to another space or not at all) raises the same error
    either way — never a signal distinguishing the two.
    """
    if source_type == "task":
        found = tasks_service.get_task(db, space_id, source_id) is not None
    elif source_type == "calendar_event":
        found = calendar_service.get_calendar_event(db, space_id, source_id) is not None
    elif source_type == "inbox_item":
        found = inbox_service.get_item(db, space_id, source_id) is not None
    else:
        raise AttentionExposureError(f"Unsupported source_type: {source_type!r}")

    if not found:
        raise AttentionOwnershipError(
            f"No {source_type} with id={source_id} exists in space_id={space_id}"
        )


def record_exposure(
    db: Session,
    space_id: int,
    user_id: int,
    candidate: AttentionCandidate,
    surface: AttentionSurface,
    surfaced_at: datetime,
    commit: bool = True,
) -> AttentionExposure:
    """Creates exactly ONE durable exposure row for a candidate that was
    ACTUALLY DELIVERED to the user — never call this for a candidate
    that was merely scored/ranked (selection != delivery). The caller
    (a future surfacing consumer) is the only party that knows a real
    delivery happened; this function trusts that decision but verifies
    everything else about the request.

    surfaced_at is an explicit, caller-supplied delivery instant — this
    function never substitutes scoring.rank_for_surface's own `now` or
    any internally-computed timestamp for it. The caller (the actual
    surfacing consumer) is the only party that knows the real instant
    delivery happened; the candidate's own scoring was computed at some
    earlier moment, which is not the same fact and must never be
    conflated with it.

    Validates, before writing anything:
    - surface and signal_type are members of the locked value sets.
    - space_id is actually owned by user_id (Space.user_id match).
    - the Signal's own source (Task/CalendarEvent/InboxItem) actually
      exists in THIS space, via that source's own scoped getter — never
      trusting the source_id merely because it was present on the
      Signal.

    reason_codes/exposure_snapshot/score/signal_type/policy_version are
    taken ENTIRELY from the already-validated `candidate` — this
    function never accepts or merges in any separately-supplied JSON for
    these fields (§J: never arbitrary client JSON).
    """
    if surface not in _VALID_SURFACES:
        raise AttentionExposureError(f"Unsupported surface: {surface!r}")

    signal = candidate.signal
    if signal.signal_type not in SIGNAL_TYPE_ORDER:
        raise AttentionExposureError(f"Unsupported signal_type: {signal.signal_type!r}")
    if signal.source_type not in _SOURCE_COLUMN_BY_TYPE:
        raise AttentionExposureError(f"Unsupported source_type: {signal.source_type!r}")

    _require_space_owned_by_user(db, space_id, user_id)
    _require_source_exists_in_space(db, space_id, signal.source_type, signal.source_id)

    exposure = AttentionExposure(
        space_id=space_id,
        user_id=user_id,
        task_id=signal.source_id if signal.source_type == "task" else None,
        event_id=signal.source_id if signal.source_type == "calendar_event" else None,
        inbox_item_id=signal.source_id if signal.source_type == "inbox_item" else None,
        signal_type=signal.signal_type,
        surface=surface,
        policy_version=scoring.POLICY_VERSION,
        score=candidate.score,
        reason_codes=[
            {"code": component.code, "value": component.value, "score_delta": component.score_delta}
            for component in candidate.reason_codes
        ],
        exposure_snapshot=signal.snapshot,
        surfaced_at=surfaced_at,
    )
    db.add(exposure)
    if commit:
        db.commit()
        db.refresh(exposure)
    else:
        db.flush()
    return exposure


def load_suppression_states(
    db: Session, space_id: int, identities: Iterable[tuple[SourceType, int]]
) -> dict[tuple[SourceType, int], SuppressionState]:
    """Batch-constructs 4.3's SuppressionState for every (source_type,
    source_id) identity in `identities`, from durable history — NOT one
    query per identity (bounded at one query per distinct source_type
    present, never per identity, so a full-space ranking pass costs at
    most 3 queries regardless of how many signals it covers).

    For each identity, uses the SINGLE latest exposure row — ORDER BY
    surfaced_at DESC, id DESC — across ALL surfaces (cooldown is
    global, per 4.3's own already-shipped SuppressionState shape,
    which has exactly one surfaced_at field, not one per surface).

    An identity with no exposure history at all is simply absent from
    the returned dict — rank_for_surface's own feedback_by_source.get(
    key) already treats a missing key as "no feedback", exactly
    matching a fully-eligible SuppressionState.

    Never reads updated_at for anything — only the explicit semantic
    columns (surfaced_at/snoozed_until/dismissed_at/acted_on_at) ever
    feed into the constructed SuppressionState.
    """
    ids_by_type: dict[SourceType, list[int]] = defaultdict(list)
    for source_type, source_id in identities:
        ids_by_type[source_type].append(source_id)

    states: dict[tuple[SourceType, int], SuppressionState] = {}
    for source_type, ids in ids_by_type.items():
        column = _SOURCE_COLUMN_BY_TYPE[source_type]
        attr = _SOURCE_ATTR_BY_TYPE[source_type]
        query = (
            select(AttentionExposure)
            .where(AttentionExposure.space_id == space_id, column.in_(ids))
            .distinct(column)
            .order_by(column, AttentionExposure.surfaced_at.desc(), AttentionExposure.id.desc())
        )
        rows = db.execute(query).scalars().all()
        for row in rows:
            source_id = getattr(row, attr)
            states[(source_type, source_id)] = SuppressionState(
                surfaced_at=row.surfaced_at,
                snoozed_until=row.snoozed_until,
                dismissed_at=row.dismissed_at,
                dismissed_snapshot=row.exposure_snapshot if row.dismissed_at is not None else None,
                acted_on_at=row.acted_on_at,
            )
    return states
