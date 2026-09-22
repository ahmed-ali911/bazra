"""The space-isolation query convention. Every space-scoped service function
goes through scoped_query() and filters single-row lookups by space_id as
well as id — a row belonging to a different space is indistinguishable from
one that doesn't exist at all (404, never a leak, never a 403 that would
confirm the row exists somewhere the caller can't see).

Deliberately does not know anything about life_area_id — that's a
classification concept (LifeAreaScopedMixin), not an access-control
boundary, and must never be folded into this helper.
"""

from sqlalchemy import Select, select


def scoped_query(model: type, space_id: int) -> Select:
    return select(model).where(model.space_id == space_id)
