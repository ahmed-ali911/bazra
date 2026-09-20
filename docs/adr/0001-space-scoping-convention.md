# ADR 0001: Space scoping via explicit mixin, not a universal base model field

## Status
Accepted

## Context

BAZRA's architecture review locked "Spaces must be respected as a security
boundary from day one" — every space-owned entity needs a `space_id` and
query-time filtering, because retrofitting isolation after real data exists
is expensive and bug-prone.

However, not every future entity is space-owned. Some are genuinely global
(e.g. system configuration, the single user record itself, personality
config that may apply across all of a user's spaces). Putting `space_id` on
a single universal `BaseModel` that every table inherits from would force
every future table — global or not — to carry a `space_id`, either leaving
it meaninglessly null on global entities or inventing a fake "global" space
row to satisfy a NOT NULL constraint. Both are worse than just not having
the column on entities that don't need it.

## Decision

Define two explicit base conventions in `backend/app/core/`:

- `BaseModel` — plain SQLAlchemy declarative base with common columns
  (`id`, `created_at`, `updated_at`). No `space_id`.
- `SpaceScopedMixin` — adds `space_id` (indexed, foreign key to the future
  `spaces` table once it exists) plus a documented expectation that any
  query against a space-scoped table filters by `space_id`.

A domain model that belongs to a space inherits `(BaseModel, SpaceScopedMixin)`
explicitly. A genuinely global entity inherits only `BaseModel`. This makes
the space-scoping choice a visible, reviewed decision at each model's
definition — not an accident of a shared base class — while still making it
easy (one mixin) to do correctly.

No `spaces` table is created in Checkpoint 1 — there is no space-owned
domain entity yet to reference it. The mixin and convention exist now so
that Checkpoint 2 onward, the first real domain table (whichever module
arrives first) applies it correctly from its own first migration rather
than being retrofitted.

## Consequences

- Every future PR/commit that adds a domain model must explicitly choose
  `SpaceScopedMixin` or not — this is a deliberate small amount of friction
  in exchange for never silently forgetting space isolation.
- Query-layer enforcement (e.g. a repository/service helper that always
  filters by the current `space_id` for scoped models) is a Checkpoint 2+
  concern once real modules and a real "current space" concept exist — this
  ADR only locks the schema-level convention, not the enforcement mechanism.
- If a genuinely global entity later turns out to need space-scoping after
  all, adding the mixin is a normal migration, not an architectural
  reversal — this convention was chosen specifically to make that direction
  of change cheap.
