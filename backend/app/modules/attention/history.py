"""Checkpoint 4.4a/4.4b/4.4c-3 — Attention exposure persistence,
feedback, and ACTED_ON attribution.

The one place Attention state becomes durable. Signal (4.2) and
AttentionCandidate (4.3) both remain fully live-derived and
unpersisted; this module only ever records that a candidate was
ACTUALLY DELIVERED (record_exposure), records explicit user feedback
against one specific delivered exposure (record_snooze,
record_dismiss), reconstructs 4.3's SuppressionState read model from
that durable history (load_suppression_states), and — the one function
a domain service actually calls, attempt_acted_on_attribution — records
that a qualifying source mutation resolved the latest surfaced concern.

attempt_acted_on_attribution is deliberately FAIL-OPEN relative to the
domain mutation that calls it: DOMAIN TRUTH > ATTENTION ATTRIBUTION.
It isolates its own work in a Postgres SAVEPOINT (Session.begin_nested())
and swallows (logs, never re-raises) any exception from that work —
proven empirically (disposable, since-deleted experiment scripts run
against the real dev DB, not assumed from general SQLAlchemy
knowledge) that begin_nested() autoflushes pending outer changes
before establishing the savepoint, that a nested rollback leaves both
the Session and the outer object's already-flushed state fully intact,
and that the outer caller's own later db.commit() succeeds normally
afterward. It never wraps or swallows the caller's own final commit —
a genuine core DB/session failure there still propagates normally.

Deliberately NOT implemented here (a later checkpoint owns these):
- any surfacing consumer (APP_OPENED / Daily Brief delivery) that
  would actually call record_exposure in production.
- natural-language snooze-phrase parsing — every timestamp this module
  receives is already resolved by the caller.
- any append-only log of every individual re-snooze instruction: this
  table stores the LATEST snooze state per exposure, not a full
  transition history. If a future BAZRA Evolution System genuinely
  needs every historical re-snooze instruction, a separate append-only
  feedback-event table may be introduced then — not built now.

No Model Router/Anthropic/Orchestrator call, no ProposedAction
interaction, no proactive ChatMessage — this module is as
deterministic and persistence-boundary-local as actions_service's own
create_pending_action.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.attention import resolution, scoring
from app.modules.attention.models import AttentionExposure
from app.modules.attention.schemas import (
    SIGNAL_TYPE_ORDER,
    AttentionCandidate,
    AttentionSurface,
    EventMutationState,
    InboxMutationState,
    InvalidTimezoneError,
    SourceType,
    SuppressionState,
    TaskMutationState,
)
from app.modules.calendar import service as calendar_service
from app.modules.inbox import service as inbox_service
from app.modules.spaces.models import Space
from app.modules.tasks import service as tasks_service

logger = logging.getLogger(__name__)

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
    source's own scoped getter.

    record_snooze/record_dismiss also raise this for an exposure_id
    that does not exist, or does not belong to the supplied
    (space_id, user_id) — the same "nonexistent and wrong-tenant look
    identical" discipline, applied to feedback targeting."""


class InvalidSnoozeInstantError(Exception):
    """Raised when snoozed_until is not strictly greater than the
    caller-supplied authoritative `now` — a snoozed_until <= now could
    never actually suppress anything under Checkpoint 4.3's own locked
    SNOOZED gate (`now < snoozed_until`), so persisting it anyway would
    be silently-ineffective, misleading history. Raised BEFORE any
    database write; purely a mechanical comparison, never natural-
    language interpretation (out of scope here — see this module's own
    docstring)."""

    def __init__(self, snoozed_until: datetime, now: datetime):
        self.snoozed_until = snoozed_until
        self.now = now
        super().__init__(
            f"snoozed_until ({snoozed_until.isoformat()}) must be strictly after "
            f"now ({now.isoformat()})"
        )


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


def _require_valid_timezone(timezone_name: str) -> None:
    """The same ZoneInfo/IANA validation discipline service.py's own
    _resolve_zone already established for Signal generation (§4.2) —
    reused here, never re-implemented differently, since this
    timezone_name must be the SAME validated context that produced the
    signals being surfaced, not a separately-trusted string."""
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTimezoneError(timezone_name) from exc


def record_exposure(
    db: Session,
    space_id: int,
    user_id: int,
    candidate: AttentionCandidate,
    surface: AttentionSurface,
    surfaced_at: datetime,
    timezone_name: str,
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

    timezone_name (Checkpoint 4.4c-1) is REQUIRED — the exact validated
    IANA timezone context that gave the surfaced Signal its temporal
    meaning (e.g. what made a TASK_DUE_TODAY signal mean "today"). This
    is exposure-time provenance, never a permanent user preference —
    two exposures for the same source may legitimately carry different
    values. Never defaulted, never silently substituted with a server
    timezone, the current request's unrelated location, or
    datetime.now().astimezone() — the caller must supply the SAME
    timezone context used to generate the signals being surfaced.
    Validated via the same ZoneInfo/IANA discipline as Checkpoint 4.2's
    own signal generation; an invalid name raises InvalidTimezoneError
    BEFORE anything is persisted.

    Validates, before writing anything:
    - surface and signal_type are members of the locked value sets.
    - timezone_name resolves via zoneinfo.
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

    _require_valid_timezone(timezone_name)
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
        timezone_name=timezone_name,
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


def _get_owned_exposure(db: Session, space_id: int, user_id: int, exposure_id: int) -> AttentionExposure | None:
    """A nonexistent exposure_id and one that exists but belongs to a
    different space/user are indistinguishable from the caller's point
    of view — both simply return None here, exactly matching
    core/space_scoping.py's own established discipline."""
    return db.execute(
        select(AttentionExposure).where(
            AttentionExposure.id == exposure_id,
            AttentionExposure.space_id == space_id,
            AttentionExposure.user_id == user_id,
        )
    ).scalar_one_or_none()


def record_dismiss(
    db: Session, space_id: int, user_id: int, exposure_id: int, now: datetime, commit: bool = True
) -> AttentionExposure:
    """First accepted dismiss wins — a conditional UPDATE guarded on
    `dismissed_at IS NULL` takes a real Postgres row lock the instant it
    matches (the same confirm_and_execute/reject replay-guard
    convention actions_service already established), so a second,
    concurrent dismiss attempt against the SAME row blocks until this
    one resolves, then itself matches nothing (dismissed_at is no
    longer NULL) and falls through to the true-no-op path below.

    A duplicate dismiss changes NOTHING: dismissed_at is never
    rewritten, updated_at is never bumped, and no other column is
    touched — the existing row is read back and returned exactly as
    the FIRST accepted dismiss left it.

    Never mutates any of the immutable exposure-core columns (space_id/
    user_id/task_id/event_id/inbox_item_id/signal_type/surface/
    policy_version/score/reason_codes/exposure_snapshot/surfaced_at) —
    the UPDATE's own SET clause touches dismissed_at only.

    Raises AttentionOwnershipError if exposure_id does not exist, or
    exists but does not belong to (space_id, user_id) — never
    distinguishing the two cases to the caller.
    """
    stmt = (
        update(AttentionExposure)
        .where(
            AttentionExposure.id == exposure_id,
            AttentionExposure.space_id == space_id,
            AttentionExposure.user_id == user_id,
            AttentionExposure.dismissed_at.is_(None),
        )
        .values(dismissed_at=now)
        .returning(AttentionExposure)
    )
    exposure = db.execute(stmt).scalars().first()
    if exposure is not None:
        if commit:
            db.commit()
        return exposure

    existing = _get_owned_exposure(db, space_id, user_id, exposure_id)
    if existing is None:
        raise AttentionOwnershipError(
            f"No exposure with id={exposure_id} exists in space_id={space_id} for user_id={user_id}"
        )
    # Already dismissed — a true no-op; return it completely untouched,
    # never re-issuing the same write.
    return existing


def record_snooze(
    db: Session,
    space_id: int,
    user_id: int,
    exposure_id: int,
    snoozed_until: datetime,
    now: datetime,
    commit: bool = True,
) -> AttentionExposure:
    """Unlike dismiss, a later snooze CAN be a genuine new instruction
    (re-snooze) — so this is NOT first-write-wins. The single
    conditional UPDATE below uses `snoozed_until IS DISTINCT FROM
    :requested` (NULL-safe inequality) as its guard, which correctly
    covers all three real cases in ONE atomic statement:
      - existing snoozed_until IS NULL (first snooze) -> DISTINCT FROM
        is true -> matches -> sets the requested value.
      - existing snoozed_until differs from the requested value
        (intentional re-snooze, earlier OR later, both legitimate
        explicit instructions) -> matches -> overwrites.
      - existing snoozed_until EQUALS the requested value (exact
        duplicate request) -> DISTINCT FROM is false -> does NOT match
        -> zero rows touched -> falls through to the true-no-op path
        below, exactly like record_dismiss's own duplicate handling:
        no rewrite, no updated_at bump.

    Validates snoozed_until > now BEFORE touching the database at all
    (InvalidSnoozeInstantError, zero mutation) — mechanical comparison
    only, using the caller's own explicit `now`, never a hidden
    datetime.now().

    Never clears dismissed_at — snooze and dismiss coexist by design;
    Checkpoint 4.3's own already-locked gate precedence (SNOOZED before
    DISMISSED_UNCHANGED) alone decides which currently applies.

    Raises AttentionOwnershipError on a nonexistent or not-owned
    exposure_id, identically to record_dismiss.
    """
    if snoozed_until <= now:
        raise InvalidSnoozeInstantError(snoozed_until, now)

    stmt = (
        update(AttentionExposure)
        .where(
            AttentionExposure.id == exposure_id,
            AttentionExposure.space_id == space_id,
            AttentionExposure.user_id == user_id,
            AttentionExposure.snoozed_until.is_distinct_from(snoozed_until),
        )
        .values(snoozed_until=snoozed_until)
        .returning(AttentionExposure)
    )
    exposure = db.execute(stmt).scalars().first()
    if exposure is not None:
        if commit:
            db.commit()
        return exposure

    existing = _get_owned_exposure(db, space_id, user_id, exposure_id)
    if existing is None:
        raise AttentionOwnershipError(
            f"No exposure with id={exposure_id} exists in space_id={space_id} for user_id={user_id}"
        )
    # Exact-duplicate requested value — a true no-op; return it
    # completely untouched, never manufacturing a meaningless write.
    return existing


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


def _get_latest_exposure(
    db: Session, space_id: int, source_type: SourceType, source_id: int
) -> AttentionExposure | None:
    """The single-identity counterpart to load_suppression_states' own
    batched query — same ordering (surfaced_at DESC, id DESC), same
    "across all surfaces" scope. Returns the real AttentionExposure row
    directly (never a SuppressionState proxy): attribution needs
    signal_type, timezone_name, and acted_on_at, none of which
    SuppressionState carries. The latest exposure is authoritative for
    attribution eligibility regardless of its own current
    snoozed/dismissed state — ACTED_ON is historical attribution, never
    a suppression gate (never select an older exposure merely because
    the newest one happens to be snoozed/dismissed/already acted_on).
    """
    column = _SOURCE_COLUMN_BY_TYPE[source_type]
    return db.execute(
        select(AttentionExposure)
        .where(AttentionExposure.space_id == space_id, column == source_id)
        .order_by(AttentionExposure.surfaced_at.desc(), AttentionExposure.id.desc())
        .limit(1)
    ).scalars().first()


def get_latest_exposure_for_concern(
    db: Session, space_id: int, source_type: SourceType, source_id: int, signal_type: str, surface: AttentionSurface
) -> AttentionExposure | None:
    """Checkpoint 4.7 — the authoritative evidence behind the Same-Concern
    Repeat Gate (attention/app_opened.py's own same_concern_recently_surfaced):
    "the last time THIS surface proactively surfaced THIS EXACT signal_type
    for THIS source" — a narrower identity than both
    load_suppression_states' own (source_type, source_id)-only cooldown
    scope (which deliberately ignores signal_type/surface, since it is a
    cross-surface, cross-signal-type suppression) and _get_latest_exposure's
    own "across all surfaces" ACTED_ON-attribution scope. Returns the real
    row (never a derived summary) so the caller can compare its own
    exposure_snapshot against the current Signal's snapshot — the same
    snapshot-equality convention evaluate_gates' own DISMISSED_UNCHANGED
    gate already established, reused here rather than inventing a second
    "what counts as changed" rule. No new table, no new column — this is a
    read-only query over the exact same accepted AttentionExposure history.
    """
    column = _SOURCE_COLUMN_BY_TYPE[source_type]
    return db.execute(
        select(AttentionExposure)
        .where(
            AttentionExposure.space_id == space_id,
            AttentionExposure.surface == surface,
            AttentionExposure.signal_type == signal_type,
            column == source_id,
        )
        .order_by(AttentionExposure.surfaced_at.desc(), AttentionExposure.id.desc())
        .limit(1)
    ).scalars().first()


def attempt_acted_on_attribution(
    db: Session,
    space_id: int,
    source_type: SourceType,
    source_id: int,
    state: TaskMutationState | EventMutationState | InboxMutationState,
    now: datetime,
) -> None:
    """Checkpoint 4.4c-3 — the one function a domain service calls,
    immediately before its OWN existing db.commit(), after applying a
    QUALIFYING semantic mutation (the caller is responsible for only
    calling this when a qualifying field's VALUE actually changed —
    see tasks_service.update_task/delete_task,
    calendar_service.update_calendar_event/delete_calendar_event,
    inbox_service.mark_read/dismiss_item for where this is invoked).

    FAIL-OPEN BY DESIGN: DOMAIN TRUTH > ATTENTION ATTRIBUTION. This
    function NEVER raises. All of its own work — the latest-exposure
    lookup, the (already-pure, already-tested) resolution.evaluate_acted_on
    call, and the conditional acted_on_at write — runs inside a single
    Postgres SAVEPOINT (Session.begin_nested()). Any exception from
    that work rolls back ONLY the savepoint (proven, via real
    disposable experiments against the real dev DB — not assumed —
    to leave both the Session and the caller's own already-flushed
    domain-mutation state fully intact) and is logged, never re-raised.
    This function never wraps or touches the caller's own final
    db.commit() — a genuine core DB/session failure there still
    propagates normally, exactly as before this function existed.

    Normal, expected outcomes that are NOT failures and involve no
    exception/rollback at all: no exposure exists for this source; the
    latest exposure already has acted_on_at set; the evaluator returns
    NOT_RESOLVED or UNKNOWN. All four are plain early returns from
    inside the nested block, which still commits (releases) the
    savepoint normally — there is nothing to roll back for a correct,
    uneventful "nothing to attribute" outcome.

    Only the LATEST exposure for (space_id, source_type, source_id) is
    ever eligible — never an older one, and never influenced by the
    latest one's own snoozed/dismissed state (ACTED_ON is historical
    attribution, not a suppression gate).

    The acted_on_at write is first-write-wins
    (WHERE id=... AND acted_on_at IS NULL), the same idempotent
    conditional-UPDATE convention record_dismiss already established.

    IMPORTANT — flushes explicitly BEFORE opening the SAVEPOINT, outside
    this function's own try/except: begin_nested() autoflushes any
    pending changes anyway (proven empirically), and if some UNRELATED
    pending write elsewhere in the SAME transaction (made by the
    caller, or by something the caller itself already called) is
    invalid, that flush failure is a genuine CORE DOMAIN failure, not
    an Attention failure — it must propagate normally, never be
    miscategorized and swallowed by the except block below. Flushing
    explicitly first, outside the try, is exactly what keeps that
    failure outside this function's own fail-open boundary.
    """
    db.flush()
    try:
        with db.begin_nested():
            exposure = _get_latest_exposure(db, space_id, source_type, source_id)
            if exposure is None:
                return
            if exposure.acted_on_at is not None:
                return

            result = resolution.evaluate_acted_on(
                exposure.signal_type, state, now, exposure.timezone_name
            )
            if result != "RESOLVED":
                return

            db.execute(
                update(AttentionExposure)
                .where(AttentionExposure.id == exposure.id, AttentionExposure.acted_on_at.is_(None))
                .values(acted_on_at=now)
            )
    except Exception:
        logger.exception(
            "attention: acted_on attribution failed (space_id=%s, source_type=%s, source_id=%s)",
            space_id, source_type, source_id,
        )
