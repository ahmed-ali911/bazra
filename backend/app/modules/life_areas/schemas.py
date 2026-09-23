from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LifeAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class LifeAreaCreate(BaseModel):
    name: str


class LifeAreaUpdate(BaseModel):
    """Rename only — slug is always re-derived server-side from name,
    never client-supplied."""

    name: str


class LifeAreaSummary(BaseModel):
    id: int
    name: str
    slug: str
    open_task_count: int
    most_urgent_due_at: datetime | None


class UnassignedSummary(BaseModel):
    """Unassigned is not a Life Area — it has no id and no slug, so it
    gets its own schema rather than forcing LifeAreaSummary's shape
    (which requires a non-nullable id) to also cover a case that has no
    id to give it. Kept minimal on purpose: nothing else about
    "Unassigned" is meaningful the way a real LifeArea's name/slug are.
    """

    open_task_count: int
    most_urgent_due_at: datetime | None


class MyWorldSummary(BaseModel):
    life_areas: list[LifeAreaSummary]
    unassigned: UnassignedSummary
