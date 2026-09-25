# BAZRA Capability & Tool Routing

**This is a standing architecture principle, not an ADR.** Like
`bazra-identity-independence.md`, it locks no current code decision
against specific code — the current implementation (`_TOOLS_OFFERED` +
`_TOOL_HANDLERS` in `chat/service.py`, `ProposedAction` in `actions/`)
already satisfies everything below without changing at all. This note
exists so the reasoning, and the boundary it draws, are on record before
the next capability is proposed — not to justify building anything now.

## The taxonomy

Every request BAZRA handles falls into one of five conceptual
categories. This taxonomy is deliberately **not** an enum, a class
hierarchy, or a database field — nothing in the current codebase
consumes it as a value; it exists only to keep ownership boundaries
consistent as new capabilities are added.

1. **Model Knowledge** — general knowledge the model can answer
   directly, with no BAZRA-owned data or side effect involved (e.g.
   "what is compound interest?").
2. **Context/App Data** — BAZRA's own grounded state (Tasks, Calendar,
   LifeAreas, Inbox, Memory), gathered unconditionally on every turn by
   `chat/context.py` and `memory_service`, never fetched on model
   request. Calendar in particular is already folded in here via
   `home_service.build_home_summary` → `calendar_service` — it was
   never a separate tool and does not need to become one.
3. **Local Capability** — deterministic computation with no external
   network call. No example exists in the codebase today.
4. **External Read Tool** — a live call to an external provider
   (weather, search, maps, ...). No example exists in the codebase
   today.
5. **Write Action** — a domain mutation, which must always be proposed
   and confirmed. `create_task`, `save_memory`, `forget_memory` are the
   only members today.

## Read gaps: check Context Assembly before reaching for a tool

An apparent missing read capability is often a Context Assembly gap,
not a routing gap. Concrete evidence from the 3.6 review: a user asking
about a task's *description* currently gets nothing, because
`chat/context.py`'s `_format_task_line` only surfaces `title` and
`due_at`. The correct fix for that is widening the context formatter —
a one-line change, zero new tool, zero additional model round trip —
not a `get_task_details` tool. Before proposing a new Local Capability
or External Read Tool, first check whether the same data could simply
be added to (or made less truncated within) the existing bounded
Context Assembly. Reach for a tool only once the answer is genuinely
no — the data isn't something BAZRA already holds and could just
surface.

## Read vs. write execution

**Invariant, unconditional:** every write, regardless of how many read
capabilities exist alongside it, continues through the existing
pipeline — `validate_arguments` → deterministic, argument-rendered
confirmation → `create_pending_action` (`ProposedAction`, `pending`) →
explicit user confirmation → `confirm_and_execute`. A read capability
being implemented as a "tool" the model can call is not, by itself, a
reason to route it through `ProposedAction` — that pipeline exists for
mutation and irreversibility, not for the mechanical fact of being
tool-shaped. Conversely, **read-only does not automatically mean
privacy-insensitive**: a read that transmits user context to an
external provider (an External Read Tool) raises a real data-boundary
question that a purely local read does not. That boundary is not
designed here — it has no current member to design it against — but
must be resolved concretely at the point the first External Read Tool
is actually proposed, not assumed away because "it's just a read."

## The unresolved decision gate for the first read capability

When the first real read capability is built, its result must reach
the model's final reply by one of two paths, and this checkpoint
deliberately leaves the choice open rather than deciding it in the
abstract:

- **A. Deterministic, code-rendered response.** The tool's result is
  formatted directly into the reply by code — the same pattern
  `_render_create_task_confirmation` already uses for writes. Zero
  additional model calls; the reply is templated rather than freely
  composed.
- **B. Structured `tool_result` returned to the model** for a second
  completion. More natural language, but costs a second real provider
  call — which the project's standing "zero additional LLM calls
  without demonstrated need" discipline does not grant by default, and
  which (per this project's live-verification convention) would need
  its own explicit approval to exercise for real.

Choose between A and B against the actual requirements of that first
capability — not here, in the abstract, with nothing concrete to weigh
it against.

## Tool exposure

All tools are exposed on every turn today (`_TOOLS_OFFERED`), regardless
of read/write — there is currently no cost or correctness problem this
would need to solve. Read and write tools may continue sharing one flat
`name`/`description`/`input_schema` representation; nothing today
distinguishes them structurally, and nothing currently needs to.

## When to revisit tool infrastructure

Not on a tool count alone. Revisit `_TOOLS_OFFERED` /
`_TOOL_HANDLERS`, and only then consider whether a registry, dynamic
tool filtering, or per-tool exposure rules are actually justified, when
any of the following becomes concretely true:

- a second module besides `actions`/`memory` needs to contribute tools,
  and static wiring in `chat/service.py` becomes genuinely awkward to
  maintain by hand;
- schema token cost or tool count becomes materially significant
  against the context budget, evidenced by measurement, not estimate;
- tools become contextual or mutually exclusive by app state (none are
  today);
- measured maintenance, token, or latency evidence — not a round
  number — justifies filtering.

A number such as "8–10 tools" is a heuristic for when to *look*, not a
threshold that triggers a change on its own.

## What this note does not do

It does not add a `CapabilityRegistry`, a taxonomy enum, a permission
matrix, provenance tracking, or any new runtime code. Nothing in the
current codebase (through Checkpoint 3.6) has been found to conflict
with the boundaries above, and this note requests no change to it.
