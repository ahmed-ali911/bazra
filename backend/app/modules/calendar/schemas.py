from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class CalendarEventCreate(BaseModel):
    title: str
    description: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    life_area_id: int | None = None

    @model_validator(mode="after")
    def _validate_range(self) -> "CalendarEventCreate":
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("ends_at must be >= starts_at")
        return self


class ProposedCalendarEventCreate(CalendarEventCreate):
    """Checkpoint 3.15 — the model-facing argument shape for
    propose_create_event: identical fields to CalendarEventCreate
    (title, description, starts_at, ends_at, life_area_id), plus one
    additional requirement that applies ONLY to a Chat-originated
    proposal: starts_at, and ends_at when present, must be
    timezone-aware instants. The direct REST CalendarEventCreate is
    deliberately left untouched by this subclass — a naive datetime
    posted directly to POST /calendar/events remains exactly as
    permitted as before this checkpoint, since that's an unrelated,
    pre-existing API behavior this checkpoint must not change. Only the
    Chat/ProposedAction path gets the extra guarantee, because a
    model-supplied naive instant has no reliable way to be corrected
    before it's stored, and a wrong instant is a real created event a
    user must notice and undo manually — a materially different risk
    than a REST caller who controls their own request.
    """

    @model_validator(mode="after")
    def _validate_range(self) -> "ProposedCalendarEventCreate":
        """Overrides (not merely adds to) CalendarEventCreate's own
        same-named validator — deliberately: Pydantic v2 runs an
        inherited "after" validator BEFORE a subclass's own differently-
        named one, and the base class's plain `self.ends_at <
        self.starts_at` comparison raises a raw, unhandled TypeError
        (not a clean ValidationError) when either side is naive — e.g. a
        timezone-aware starts_at against a naive ends_at. Overriding the
        same method name replaces the parent's check entirely for this
        subclass only (CalendarEventCreate itself, and REST, are
        untouched), so the timezone-awareness check always runs first,
        and the range comparison below only ever runs once both sides
        are already confirmed timezone-aware.
        """
        if self.starts_at.tzinfo is None:
            raise ValueError("starts_at must be a timezone-aware instant (with an explicit UTC offset)")
        if self.ends_at is not None and self.ends_at.tzinfo is None:
            raise ValueError("ends_at must be a timezone-aware instant (with an explicit UTC offset)")
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("ends_at must be >= starts_at")
        return self


class CalendarEventUpdate(BaseModel):
    """All fields optional, applied with exclude_unset=True — same partial-
    update convention as TaskUpdate. ends_at >= starts_at is validated in
    the service layer against the MERGED result, not here, since a partial
    payload may only include one of the two fields.
    """

    title: str | None = None
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    life_area_id: int | None = None


class ProposedCalendarEventUpdate(CalendarEventUpdate):
    """Checkpoint 3.18 — the model-facing argument shape for
    propose_update_event: CalendarEventUpdate's own optional fields,
    plus the event_id being referenced, plus a timezone-awareness
    requirement scoped ONLY to whichever of starts_at/ends_at the model
    actually supplied — both remain fully optional here (unlike
    ProposedCalendarEventCreate's always-required starts_at), so
    model_fields_set (not the dumped value) decides what was actually
    named, the same exclude_unset-aware discipline
    ProposedTaskUpdate/ProposedCalendarEventCreate already established.

    Unlike CalendarEventUpdate, this class has no inherited same-named
    validator to override — the base class deliberately has no range
    check at all (see its own docstring: a partial payload may name
    only one of starts_at/ends_at, so "ends_at >= starts_at" can only
    be checked once merged with the EXISTING event, which happens in
    the proposal handler and confirm_and_execute, not here) — so there
    is no repeat of 3.15's validator-ordering hazard to guard against
    in this class.
    """

    event_id: int

    @model_validator(mode="after")
    def _validate_update(self) -> "ProposedCalendarEventUpdate":
        if not (self.model_fields_set - {"event_id"}):
            raise ValueError("at least one field to change must be provided")
        if "starts_at" in self.model_fields_set:
            if self.starts_at is None:
                raise ValueError("starts_at cannot be cleared — it is a required field")
            if self.starts_at.tzinfo is None:
                raise ValueError("starts_at must be a timezone-aware instant (with an explicit UTC offset)")
        if "ends_at" in self.model_fields_set and self.ends_at is not None and self.ends_at.tzinfo is None:
            raise ValueError("ends_at must be a timezone-aware instant (with an explicit UTC offset)")
        return self


class CalendarEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    life_area_id: int | None
    created_at: datetime
    updated_at: datetime


class AgendaItem(BaseModel):
    """The normalized shape for the agenda read-model — preserves source
    identity (source + the SOURCE entity's own id, never a synthetic
    combined id) so a future UI can route back to the correct owning
    endpoint. ends_at is always null for source="task" — tasks have no
    end time, only a due_at (mapped to starts_at here).
    """

    source: Literal["event", "task"]
    id: int
    title: str
    starts_at: datetime
    ends_at: datetime | None
    life_area_id: int | None
