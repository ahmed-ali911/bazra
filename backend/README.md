# backend/

FastAPI application (Checkpoints 2–6: skeleton, testing foundation, auth).

## Structure

- `app/main.py` — app factory (`create_app()`), mounts `api_router` under `/api/v1`
- `app/config.py` — `Settings` (Pydantic), reads `.env`
- `app/logging.py` — basic logging setup
- `app/database.py` — SQLAlchemy engine, session, `get_db()` dependency
- `app/core/base.py` — `BaseModel` / `SpaceScopedMixin` convention, see
  `docs/adr/0001-space-scoping-convention.md`
- `app/core/deps.py` — `get_current_user`: the reusable session-auth dependency
  every protected route depends on; raises 401, never falls through to 404
- `app/api/v1/` — versioned routes (`health.py`, plus `auth`'s router)
- `app/modules/auth/` — single-user session-cookie auth: `User`/`UserSession`
  models, login/logout/me routes, `seed.py` (see below)
- `migrations/` — Alembic, wired to `Settings.database_url` and `Base.metadata`.
  `env.py` imports every module's `models.py` explicitly — SQLAlchemy only
  registers a table once its module has actually been imported, so a new
  module needs a line added there before `--autogenerate` will see its tables.
- `conftest.py` — at the backend root (not `tests/`), so its fixtures
  (`db_session`, `test_engine`, `client`) are visible to every module's own
  `tests/` directory, not just the top-level `tests/`. Drops and recreates a
  real `bazra_test` database per test session and runs every migration
  against it — no mocking.

## Auth

Single-user, server-side session cookie (not JWT) — see
`docs/adr/` for the broader architecture and Section 9.1 of the brief for
why. There's no registration flow; set the one user's password with:

```bash
docker compose exec backend python -m app.modules.auth.seed
```

Safe to run again any time you want to change the password — it updates the
existing `User` row rather than creating a second one.

Endpoints (all under `/api/v1`):
- `POST /auth/login` — `{"password": "..."}`, sets the `bazra_session` cookie
  on success, 401 on wrong password
- `POST /auth/logout` — invalidates the session server-side, clears the cookie
- `GET /auth/me` — `{"authenticated": true}` if the cookie is valid, 401
  otherwise; the template every future protected route copies

## Known Limitations & Future Work

### Checkpoint 3.2

- **Arabic write-intent detection is a UX/routing heuristic, not a
  data-safety boundary.** `app/modules/chat/write_intent.py`'s regex-based
  `detect_clear_write_intent` was executed (not just read) against 22 real
  Egyptian Arabic phrasings and produced 5 false positives and 5 false
  negatives — it has no negation, tense, or question-vs-command
  understanding, and the Arabic patterns match on bare substrings (no
  `\b`-equivalent word boundary), which is fragile. This is NOT a
  data-safety risk: tracing every actual import and call site from
  `chat/` and `orchestrator/` confirms no write-capable domain function
  is ever called outside the single, explicitly-confirmed path added in
  Checkpoint 3.3 (see below) — a detector error can only affect
  routing/UX and model cost, never mutate Tasks, CalendarEvents,
  LifeAreas, or InboxItems directly. Left unpatched deliberately, pending
  native-speaker review. (Checkpoint 3.3 narrowed the CREATE patterns to
  no longer match task-creation phrasing at all — see below — but the
  same substring/negation weaknesses remain for the delete/edit/mark-done
  patterns this heuristic still gates.)

- ~~Future: write-enabled Chat must revisit intent architecture before any
  domain mutation capability is exposed.~~ **Resolved in Checkpoint
  3.3**: task creation is now a real (if narrow) capability, gated by an
  explicit user confirmation step rather than a policy/permissions layer
  — see below.

- ~~Future: current date/time/timezone should be supplied as runtime
  context, not guessed by the model.~~ **Resolved in Checkpoint 3.3**:
  the client sends its resolved IANA timezone name; the server computes
  its own trustworthy current instant and folds the local date/time into
  the system prompt as a `## Current date/time` fact.

### Checkpoint 3.3

- **A cancellation embedded in a longer message ("actually, never mind,
  let's talk about something else") is not recognized as a decline.**
  `chat/service.py`'s `_classify_narrow_yes_no` only recognizes a message
  when, after trimming whitespace/punctuation, its ENTIRE content is one
  of a small closed set of bare confirm/decline phrases ("yes", "no",
  "cancel", "نعم", "لا", ...) — deliberately narrow, to avoid
  misclassifying an ordinary sentence that merely contains one of these
  words as a confirmation or cancellation. A decline phrase mixed into a
  longer sentence therefore falls through to the Orchestrator instead:
  the pending proposal is still described in its context, but nothing
  code-level forces the model to treat the message as a cancellation, so
  the proposal may simply be left pending until it expires on its own
  (10 minutes by default — see `actions/service.py`'s
  `_DEFAULT_TTL_MINUTES`). No incorrect task is ever created by this gap
  — the failure mode is an unwanted pending proposal sitting inert until
  expiry, not a false mutation. Left unpatched deliberately for this
  checkpoint; a real intent classifier (or a dedicated
  cancel/reject tool offered alongside `propose_create_task`) would be
  the natural fix.

- **Only one pending proposal at a time, per (space, user).** Creating a
  new proposal (fresh or a revision) always supersedes any existing
  pending one first (`actions/service.py`'s `_supersede_existing_pending`)
  — there is no multi-proposal queue. This is a deliberate scope
  limitation, not an oversight. (Checkpoint 3.4 kept this invariant
  GLOBAL rather than scoping it per action_type — see below.)

### Checkpoint 3.4

BAZRA's first long-term Memory capability: explicit, user-confirmed
memories (`FACT` | `PREFERENCE` | `GOAL` | `INFERENCE`) that persist
across conversations and are retrieved into later context, so the user
never has to repeat a stated preference. Shipped by generalizing the
`actions` module rather than building a parallel proposal/confirm
system — `ProposedAction.action_type` grew to include `save_memory` and
`forget_memory`, reusing the same row-locked, single-transaction
`confirm_and_execute` (and its real two-thread concurrency guarantee)
already proven for task creation in 3.3. Both new tools
(`propose_save_memory`, `propose_forget_memory`) are offered on every
turn alongside `propose_create_task`, at zero additional model-call
cost. The proposal-facing confirmation is rendered deterministically
from validated arguments, the same 3.3 UX-fix discipline, never from
model prose. Retrieval is structured (recency-ordered, capped, with
explicit truncation disclosure) — no embeddings/pgvector, a deliberate
choice: nothing in this design demonstrates a real need that semantic
search solves and structured retrieval can't, at this app's realistic memory
volume (explicit-only creation keeps growth naturally slow). Correction
is a `save_memory` proposal with an optional `supersedes_memory_id`,
not a separate action type; forgetting is a normal propose/confirm
round trip like everything else, never an unconfirmed direct write.

Live-verified end-to-end against the real provider with exactly 2 real
calls (propose→confirm→active, then reference→propose-forget→confirm→
forgotten) — both the classification (PREFERENCE, preserving both
stated semantic facts) and the harder claim (exact `mem_id` reference
resolution against live retrieved context) were confirmed by direct
pre-confirmation database inspection, not inferred from the reply text.

- **A contradicted memory outside the retrieval window can result in
  two active, contradictory memories.** `save_memory`'s
  `supersedes_memory_id` lets the model retire an old memory it can
  currently SEE in the "What I remember about you" context section —
  but retrieval is bounded (§8 of the architecture review), so a
  correction whose target fell outside that window has no way to be
  linked, and both rows stay active. The system prompt instructs the
  model to surface a contradiction it CAN see rather than silently
  picking one side, but that only covers memories retrieved together in
  the same turn — this residual gap is the honestly-documented
  trade-off of choosing structured/bounded retrieval over embeddings
  for this checkpoint, in the same spirit as 3.3's "actually never
  mind" limitation. Left unpatched deliberately.

- Deferred (from the 3.4 architecture review, no current consumer or no
  demonstrated need — each is a trivial additive migration/change
  later if a real need appears): automatic/autonomous memory
  extraction from every turn; embeddings/pgvector; a numeric
  `confidence` score; a `source` provenance enum; `life_area_id` on
  Memory; `last_used_at`; a `SHARED_HISTORY`/`IDENTITY_HISTORY` memory
  type (BAZRA's own identity/relationship narrative is a different
  concept from facts/preferences/goals ABOUT the user — see
  `docs/architecture/bazra-identity-independence.md`); per-action-type
  scoping of the at-most-one-pending-proposal invariant; a dedicated
  deterministic "list every memory, unbounded" path for the
  memory-inspection question; full version-history/undo beyond the
  single `superseded_by_id` pointer; personality learning; a Learning
  Log or memory-management UI; knowledge-base/RAG; model
  training/fine-tuning.

### Checkpoint 3.5

BAZRA's identity/personality foundation: `app/modules/orchestrator/identity.py`
holds static, code-owned instruction text (no DB table, no persisted or
runtime-mutable state) composed first into the system prompt by
`_build_system_prompt`, ahead of the existing operational rules — the direct
operationalization of `docs/architecture/bazra-identity-independence.md`'s
standing principle that BAZRA's identity must not be owned by whichever
model happens to be reasoning underneath it. Covers: identifying as BAZRA
itself (not "BAZRA's assistant"), never naming or confirming/denying the
underlying provider/model while still answering "who made you" honestly
(Ahmed), Egyptian Arabic/English mirroring with English technical terms as
the default baseline, register (casual/focused-work/serious) read entirely
by the model's own judgment within the same reply rather than a deterministic
classifier — `write_intent.py`'s own false-positive/negative history on a
much narrower detection task was direct evidence against building one here —
and an explicit ban on claiming consciousness/subjective feelings, framing
warmth and personality as communication style rather than an inner
experience. A Memory `PREFERENCE` may refine tone/behavior but is explicitly
subordinate to identity, truthfulness, factual accuracy, safety/permission
rules, and grounded application state — the same precedence sentence is
asserted verbatim in both `identity.py` and its test. Zero additional model
calls, zero new DB tables/fields, one-pass (no rewrite/validator step, since
no rewrite step exists to protect against).

Live-verified with exactly 2 real provider calls, the second gated on the
first passing (a fresh provider-identity-pressure question, then a
consciousness/feelings-honesty question) — both judged PASS against the
semantic invariant (BAZRA maintains its own identity and does not
affirmatively claim to be the underlying provider), not a keyword/string
check, since the architecture deliberately has no provider-name output
scrubber to test against.

- Deferred (no current consumer or no demonstrated need): a persisted
  `PersonalityProfile`; autonomous personality learning from conversation;
  a humor-learning engine; a deterministic/stored mode or register system;
  a provider-name output scrubber; a two-pass Brain → Personality-rewrite →
  Validator pipeline; voice personality/custom voice; a Personality
  Sandbox/UI; a richer mood/emotion engine; automatic inference of
  personality preferences from conversation (as opposed to the existing
  explicit, user-confirmed Memory `PREFERENCE` path).

### Checkpoint 3.7

BAZRA's first External Read Tool (see `docs/architecture/bazra-capability-routing.md`
for the taxonomy this operationalizes): `get_weather`, grounded factual
weather only — no lifestyle/advice reasoning. Provider: Open-Meteo's
free Forecast + Geocoding APIs (`app/modules/weather/`), chosen for
needing no API key on their non-commercial-use tier. Flow:
`geocode_location` (free-text place name → resolved name/coordinates/
IANA timezone, `count=1`, no disambiguation UI) → `fetch_weather`
(minimum-necessary request shape per horizon — never requests
`current`+`daily`+`hourly` together, and omits `forecast_days` entirely
for `now`, which has no consumer for it). Supported horizons: `now`,
`today`, `tonight`, `tomorrow` — deliberately no weekly/general hourly
forecast. All period semantics (including "tonight") are computed
against the **resolved location's own** IANA timezone via `zoneinfo`,
never the server's or requester's — "weather tonight in Cairo" is
evaluated against Cairo's clock regardless of where the request comes
from. "Tonight" excludes already-passed hours (window starts at the
later of 18:00 local or the current moment) and aggregates the selected
hourly slice deterministically: min/max temperature, max precipitation
probability, and the highest-priority condition present (thunderstorm >
snow > rain > drizzle > fog > cloudy > partly_cloudy > clear) — data
normalization, not advice. Read-only: executes directly, in the tool's
own handler, and never calls `actions_service.create_pending_action` —
enforced by simply never calling it, not by a second dispatch
dictionary (`get_weather` shares the same `_TOOL_HANDLERS` dict as
every write tool). Location is required in the tool schema and must
come from the user's own message — the model is instructed to ask
rather than guess/default/infer one from timezone, language, Memory, or
history. Rendering is fully deterministic and bilingual (Arabic/
English), reading only the normalized `WeatherResult`'s own fields —
structurally incapable of advice, since no advice field exists on that
contract at all. Every successful reply appends the required CC BY 4.0
attribution line ("Weather data by Open-Meteo.com"); never appended
when no real data was displayed (missing-location or failure replies).
Zero additional LLM calls — the existing single conversation call
already carries this tool-selection decision, same as every other tool.
No new DB table, no migration, no persistence, no cache; `AiTrace`
remains model-call-only, and weather calls get a separate,
payload-free structured log line instead.

**Licensing (REQUIRED, revisit before any commercial deployment):** the
free Open-Meteo endpoints used here require no API key but are
documented as non-commercial use only. If BAZRA's deployment model ever
becomes commercial, this integration must be explicitly revisited (a
paid subscription + API key + a different host, or a different
provider) — never assumed to silently remain valid.

Live-verified with 3 gates: a direct real-provider adapter call (no
model involved), a real LLM turn with an explicit location ("weather in
Cairo right now" → correct tool call, grounded code-rendered reply
matching the real fetched data, exactly one `AiTrace` row, zero new
`ProposedAction` rows), and a real LLM turn with no location (the model
asked for one in plain text, made no tool call, zero provider requests).
All three passed on the first attempt.

- Deferred (no current consumer or no demonstrated need): lifestyle/
  advice reasoning (jacket/umbrella/activity recommendations, or
  weather + Calendar/Task combined reasoning) — requires Model Router's
  currently-unbuilt tool-result continuation (preserving `tool_use.id`
  and structured multi-block message content, deliberately not built in
  this checkpoint); weekly forecast; a general hourly-forecast feature;
  a saved/default location (would go through the existing explicit
  Memory `PREFERENCE` path, never a silent default); browser/device
  geolocation; caching; a capability-execution telemetry table; a
  weather-specific API key/config entry (not needed for Open-Meteo's
  free tier); geocoding disambiguation UI.

### Checkpoint 3.8

Grounded tool-result reasoning — the general architecture 3.7 deferred
for questions requiring interpretation of a live tool result (e.g.
"do I need a jacket tonight?"), proven with weather as its first real
consumer. `get_weather` gains a required `response_mode: factual |
reason` argument, decided by the SAME initial model call (no second
classifier, no keyword routing) — the same pattern already proven by
`save_memory.type`. `factual` is byte-for-byte the 3.7 path. `reason`
adds exactly one more, **terminal** model call
(`orchestrator_service.generate_tool_result_reply`, `tools=None`
unconditionally) that reasons only from the fetch's own normalized
`WeatherResult` — never the raw Open-Meteo payload, never Tasks/
Calendar/Memory/earlier history, and deliberately no current date/time
(the result's own `period_start`/`period_end`/`timezone` are sufficient
temporal grounding). `tools=None` is a structural guarantee, not a
prompt convention: with no tool declared, the provider cannot emit a
`tool_use` block, which forecloses any further tool call, write
attempt, or loop by construction. A failed continuation degrades to the
existing factual renderer's output — honest, since the facts were
really fetched — with no retry and no third call. Attribution remains
code-appended either way.

Required a small Model Router evolution: `ToolUseBlock` gains `id`
(previously discarded); a message's `content` may now be a list of
`TextBlock`/`ToolUseBlock`/`ToolResultBlock` alongside the existing
plain-string shape, serialized to Anthropic's literal wire format only
inside `model_router/service.py` — nothing outside that module ever
constructs a raw provider-shaped dict. Also added `AiTrace.correlation_id`
(nullable String, one additive migration) — an opaque, server-generated
grouping id (`secrets.token_hex`, the same primitive `auth/service.py`
already uses for session tokens), assigned to every call and explicitly
reused across a `reason` turn's two calls so both rows are provably
linked, without any span/parent hierarchy or separate correlation table.

Live-verified with 3 gates, all passing on the first attempt: factual
weather (still exactly 1 call), interpretive weather ("do I need a
jacket tonight in Cairo?" — exactly 2 calls sharing one
`correlation_id`, a grounded real recommendation, zero `ProposedAction`
rows), and an existing write proposal (still exactly 1 call, a
`ProposedAction` created, no continuation attempted) — proving 3.3's
semantics are untouched.

- Deferred (no current consumer or no demonstrated need): `response_mode`
  on any capability beyond weather; combining tool-result reasoning with
  Tasks/Calendar/Memory context in the same continuation; multiple/
  parallel tool calls; resending `tools` on a continuation (deliberately
  never done); an agent loop or recursive tool execution; a second
  fact-checking model call; a distributed-tracing span/parent hierarchy
  or correlation table.

### Checkpoint 3.9

Deterministic/zero-LLM routing foundation & observability — see
`docs/architecture/bazra-deterministic-routing.md`. Repository
inspection found the routing foundation itself already existed,
incrementally, since Checkpoints 3.3–3.4: `chat/service.py::send_message`
already short-circuits before ever reaching the Orchestrator for three
cases — a clear write-intent decline, a bare confirm ("yes") against a
pending `ProposedAction`, and a bare reject ("no") against one — none
of which ever call `model_router_service.complete()`, so none can
produce an `AiTrace` row or a `correlation_id`. No new deterministic
route was added this checkpoint (a candidate — treating a bare yes/no
as deterministic even with **no** pending proposal — was evaluated and
explicitly rejected: without a pending row to give it a concrete
referent, the same phrase is genuinely conversational again, not safe
structural evidence).

What shipped: the architecture doc distinguishing zero-LLM *execution*
from strong structural evidence (preferred) from zero-LLM
natural-language *understanding* via fragile pattern-matching
(deliberately avoided); a small, honest observability addition — one
`logger.info` call at each of the three existing branches, using the
real, already-persisted `ChatMessage.id` as the turn identifier, never
a fabricated `AiTrace` row or `correlation_id` (there is no model call
to correlate on these paths); and tests that explicitly assert the "0
calls" guarantee by count (mirroring the existing 1-call/2-call
assertion style), plus a regression guard proving a bare yes/no with no
pending proposal still reaches the model, and new explicit Arabic
coverage for the confirm/reject routes (no prior test exercised an
Arabic bare phrase against a real pending proposal).

Live-verified: a real chat-created `ProposedAction` (1 real model call)
confirmed via a bare "yes" produced the correct executed `Task`, zero
new `AiTrace` rows for the confirmation turn, and the expected
`deterministic_route=proposal_confirm chat_message_id=<real id>` log
line.

- Deferred (no current consumer or no demonstrated need): a
  turn/workflow-level correlation concept spanning both model and
  non-model operations (explicitly left open for a future checkpoint,
  not decided either way here); UI-originated structured commands (no
  such request shape exists today — would need frontend work); any
  natural-language recognition of read intent ("show me what matters
  today") as a deterministic route — the underlying query is
  deterministic, but recognizing that the sentence means that query is
  not.

### Checkpoint 3.10

Task write-capability breadth: `propose_update_task` — mark an existing
task done/reopen it, reschedule its due date, rename it, edit its
description, or reassign its life area, referenced by the `task_id`
now shown alongside each task in Context Assembly (mirroring Memory's
own `mem_id` precedent). Reuses the exact `create_task`/`save_memory`/
`forget_memory` pattern end to end: `ProposedTaskUpdate` (a thin
`TaskUpdate` subclass adding `task_id`) is one more entry in
`actions_service._ACTION_ARGUMENT_SCHEMAS`, `confirm_and_execute`
dispatches a new `update_task` branch calling the *existing*, already-
tested `tasks_service.update_task`, and the existing zero-LLM confirm/
reject route (3.9) generalizes to it with no changes of its own. No
migration — `action_type` remains a plain, code-validated string.

Two correctness findings surfaced during implementation, both fixed as
part of this checkpoint:
- `validate_arguments`'s JSONB round-trip previously dumped every
  optional field (even ones the model never mentioned) as an explicit
  `null`. Harmless for `create_task`/`save_memory` (an omitted field
  and an explicit null already mean the same thing for a brand-new
  row), but would have silently wiped an update-task's untouched
  fields on every confirmation. Fixed by adding `exclude_unset=True`
  to that one shared dump call — verified to change nothing for the
  three pre-existing action types, and directly proven for update_task
  by a dedicated partial-update test.
- `write_intent.py`'s existing deterministic decline patterns for
  "mark done" and "edit/reschedule ... task/due date" would have
  intercepted the exact phrasings `propose_update_task` needs to
  reach the model for, before this checkpoint ever ran. Narrowed the
  same way task **creation** was narrowed in 3.3 — task deletion and
  all Calendar/Inbox/Life-Area edits remain deterministically declined,
  unaffected.

`completed_at` (Correction 4) remains owned exclusively by
`tasks_service.update_task`'s own existing open→done/done→open logic —
`ProposedTaskUpdate` has no such field at all (it subclasses
`TaskUpdate`, which has none), and a model-supplied one is proven to be
silently ignored, never reaching storage or execution. A real
Postgres two-thread concurrency test (Correction 5) proves the
row-locked replay guard generalizes to `update_task`, including that
its `InboxItem` "Completed: …" side effect fires exactly once, not
twice, under concurrent confirmation attempts.

Live-verified: a real chat-created task, updated to `done` via a real
`propose_update_task` proposal and a bare "yes" — exactly 1 `AiTrace`
row for the propose call, zero for the confirm call, the existing 3.9
deterministic-route log firing correctly for the new action type, and
`completed_at` set via the unchanged existing behavior. An ordinary
`create_task` propose→confirm flow was re-verified live alongside it,
unaffected.

- Deferred (no current consumer or no demonstrated need): `delete_task`
  (a different, irreversible risk profile — deliberately out of this
  checkpoint); Calendar/Inbox write capability (the same pattern, one
  domain later); exposing `task_id` in the merged "Coming Up" agenda
  line (only Focus Today/Anytime widened — the sections most naturally
  used to reference a task for a status change).

### Checkpoint 3.11

Authoritative current action state — closes a gap the 3.10 live gates
surfaced: an assistant message that merely *looks like* a proposal
offer (whether from a real, now-resolved `ProposedAction`, or just the
model's own free text with no backing row at all) can remain visible
in conversation history indefinitely, with nothing marking it as no
longer active. A later turn had no explicit signal distinguishing that
from a genuinely pending action.

`chat/service.py::send_message` already computed `pending =
actions_service.get_latest_pending(...)` every ordinary turn and, when
it found a real row, folded it into context as `## Pending proposal
awaiting confirmation` — unchanged, still exactly that. The `else`
branch (`pending is None`) was previously silent; it now adds one
short, deterministic `## Current action state` note stating plainly
that nothing is pending, that historical proposal-like text is not
currently actionable, that a genuine re-request should be proposed
again fresh, and that ordinary conversational reference to history
remains unaffected. Both branches share the exact same `pending`
lookup already computed — no new query, no new table, no session or
state-machine concept, no change to `ProposedAction`'s lifecycle or the
global one-pending-proposal invariant, and no additional model call:
the note is pure context text, assembled deterministically alongside
Tasks/Calendar/Inbox/Memory exactly as before.

Live-verified: a real turn whose model reply resembled a proposal
offer without an actual tool call (no `ProposedAction` was created) was
followed by a genuinely unrelated new task request — the model tracked
the *new* request correctly rather than anchoring on the earlier,
stale text. A real create_task propose → confirm cycle alongside it
remained exactly 1 model call to propose, 0 to confirm, unaffected.

- Note: whether the model chooses to call a tool at all for a given
  message remains inherently stochastic (already observed and accepted
  in earlier checkpoints) — this checkpoint does not attempt to change
  that. What it does guarantee is that whenever nothing is genuinely
  pending, the model is explicitly, deterministically told so.

### Checkpoint 3.13

Task write-capability breadth, completed: `propose_delete_task` —
removes an existing task, referenced by the same `task_id` already
exposed for `propose_update_task`. Reuses the exact existing pattern end
to end: a minimal `ProposedTaskDelete` (`task_id: int` only — no title,
no fuzzy reference) is one more entry in
`actions_service._ACTION_ARGUMENT_SCHEMAS`, `confirm_and_execute`
dispatches a new `delete_task` branch calling the *existing*, already-
tested `tasks_service.delete_task` (soft delete via `archived_at` — the
row is never physically removed), and the existing zero-LLM confirm/
reject route (3.9) generalizes to it with no changes of its own. No
migration.

`write_intent.py`'s existing deterministic decline pattern for
"delete/remove/cancel ... task" would have intercepted the exact
phrasings `propose_delete_task` needs to reach the model for, before
this checkpoint ever ran. Narrowed the same way task creation (3.3) and
update (3.10) were narrowed — only "task"/"مهمة" dropped from the
delete/remove/cancel noun list; Calendar/meeting/reminder/Life-Area
deletion, in both English and Arabic, remain deterministically declined,
unaffected.

**Consequence-aware confirmation wording, deliberately not "delete" or
"archive":** BAZRA has no restore/unarchive surface anywhere today —
verified directly (no such function in `tasks_service`, no REST
endpoint, no frontend UI, no Chat capability) before writing this
checkpoint's confirmation text. Saying "delete" would overstate
permanence the DB doesn't have (the row survives, `archived_at` is
just set); saying "archive" or anything implying restorability would
misstate the *absence* of any recovery path. The confirmation and
success reply both say **"remove"** and state the current no-restore
consequence explicitly (EN: *"there's no way to bring it back in BAZRA
right now"*; AR: *"مفيش طريقة أرجعها دلوقتي في بذرة"*) — this is a
product decision about what's currently true, not a permanent claim;
if a restore path is ever added, this wording must be revisited.

`InboxItem` rows referencing a since-removed task (e.g. its own
"Completed: …" snapshot) are unaffected by design: `task_id` is a
nullable FK with no cascade, and the title is a frozen snapshot never
re-derived from the live Task — removing a task cannot break, hide, or
alter an existing `InboxItem` in any way; no cascade behavior was added.

A real Postgres two-thread concurrency test proves the row-locked
replay guard generalizes to `delete_task` exactly as it already did for
`update_task` in 3.10 — exactly one confirmation attempt archives the
task, the other observes nothing left pending, and a follow-up
confirmation attempt against the same (now-executed) proposal cannot
re-execute it.

Live-verified: a real chat-created task, removed via a real
`propose_delete_task` proposal and a bare "yes" — exactly 1 `AiTrace`
row for the propose call, zero for the confirm call, the task archived
and absent from the normal task-list read immediately afterward. A
read-only control request was verified not to spuriously trigger the
new tool.

- Deferred (no current consumer or no demonstrated need): `restore_task`
  /an unarchive endpoint, a Trash view, an undo framework, bulk
  deletion, physical row deletion, Calendar/Inbox/Life-Area deletion —
  the same deferral reasoning as 3.10's own list, one domain later.

### Checkpoint 3.15

Conversational CalendarEvent creation: `propose_create_event` — creates
a new CalendarEvent (a meeting, appointment, or reserved block of
time), distinguished from Task by a concise model-facing semantic rule
(no keyword router): a Task is something to do or complete; a
CalendarEvent occurs at a scheduled time or window. `CalendarEventCreate`
is reused directly as the action-argument schema (no wrapper needed,
same as `create_task`'s own `TaskCreate` reuse) via a new
`ProposedCalendarEventCreate` subclass that adds exactly one extra
requirement on top of it — see below. No migration.

**Timezone-awareness, validated only on the Chat path.** The direct
REST `POST /calendar/events` endpoint is completely unchanged — it
still accepts a naive datetime exactly as before this checkpoint.
`ProposedCalendarEventCreate` (`calendar/schemas.py`) subclasses
`CalendarEventCreate` and overrides its own `_validate_range` validator
(same method name, deliberately — Pydantic v2 runs an inherited "after"
validator before a differently-named subclass one, and the base
class's plain `ends_at < starts_at` comparison raises a raw `TypeError`,
not a clean `ValidationError`, when either side is naive; overriding
the same name guarantees the timezone-awareness check always runs
first) to reject a naive `starts_at`/`ends_at` before any
`ProposedAction` can exist. This is a real, tested asymmetry: Chat-
created events are held to a stricter guarantee than the REST API,
deliberately, since a model-supplied naive instant has no reliable way
to be corrected before it's stored.

**Model time grounding strengthened for every turn, not just Calendar.**
The 3.14 inspection found the model previously received only a local
wall-clock string with an abbreviated zone code (e.g. `"EEST"`) — no
explicit IANA name, no numeric UTC offset. `chat/service.py`'s
`_compute_current_datetime_local` now also states `Timezone: <IANA
name>` and `UTC offset: <±HH:MM>` explicitly, computed fresh from the
current instant via `ZoneInfo`/`astimezone` (never fixed, correct
across a DST transition) — this is a Chat-wide grounding improvement,
not Calendar-specific, and every existing `due_at`/task-reasoning use
of "Current date/time" benefits from it too.

**Missing duration/end time.** No default duration was invented. When
the user clearly describes a duration-based event (meeting,
appointment, scheduled session) but gives only a start time, the model
is instructed to ask a brief clarification question and NOT call
`propose_create_event` yet — pure system-prompt guidance, not a Python
detector; a genuine point-in-time event (e.g. a birthday) still
completes with `ends_at` left absent, unchanged from the existing
domain semantics.

**Confirmation wording** uses a natural month-name/12-hour format
("September 28, 11:00 AM–12:00 PM") deliberately different from Task's
existing numeric `DD/MM/YYYY 24h` style — month names are a fixed,
hand-written list (`_MONTH_NAMES_EN`/`_MONTH_NAMES_AR`), never
`strftime`'s locale-dependent `%B`, for the same determinism reason
`_format_due_at_local` already avoids month names entirely. Never shows
raw UTC; always converts through the request's own IANA timezone.

**Life Area backlog note** (not fixed in this checkpoint, per explicit
instruction): `chat/context.py`'s Life Areas section never exposes
`life_area_id` to the model — only name/counts — despite
`LifeAreaSummary` already carrying a real `id`. This was first found in
the 3.14 inspection and already affects `propose_create_task`'s own
`life_area_id` argument identically; `propose_create_event`'s
`life_area_id` inherits the same limitation, unchanged. Also worth
noting precisely: `LifeArea` itself is a **global**, not space-scoped,
model (`life_areas/models.py` — `BaseModel` only, no
`SpaceScopedMixin`) — there is no "other space's life area" to reject;
any valid `life_area_id` is valid from every space by construction.

Overlap/conflict semantics, all-day events, recurrence, and
`update_event`/`delete_event` are all unaffected/out of scope — the
existing Calendar domain's overlap-permitting behavior is preserved
exactly as-is.

Live-verified (real provider calls, 5 fixed trials, no mocking): an
explicit English event request, a natural Arabic event request, a Task
control request, a read-only control request, and a duration-missing
event request — all five produced the expected tool selection (or
correctly no tool call), with the duration-missing trial correctly
producing a plain clarification question ("What time does it finish?")
and no `ProposedAction`. Both real event-creation trials: exactly 1
`AiTrace` on the propose turn, 0 on confirm, zero `CalendarEvent` rows
before confirmation, exactly one afterward, at the correct UTC instant.

### Checkpoint 3.17

Closes a real, live-proven safety gap the 3.16 inspection surfaced: a
bare "yes"/"no" only ever checked whether SOME `ProposedAction` was
still `pending` and unexpired — never whether that proposal was still
what the conversation was actually about. An unrelated question asked
in between (e.g. "what's the weather tomorrow?") left the old proposal
fully intact, and a later bare "yes" — even one meant for something
else entirely — would deterministically execute it, 0 LLM calls, no
chance for the model to catch the mismatch. Reproduced live for
`create_event` and `delete_task` (the latter with no restore path,
the most consequential case) before this fix; both close with it.

**The adjacency guard** (`actions_service.is_still_conversationally_adjacent`)
answers one question deterministically: has anything been said in this
conversation since the pending proposal's own confirmation prompt? No
new column — `ProposedAction.source_chat_message_id` already *is* the
id of that exact confirmation `ChatMessage` (every `_handle_*_proposal`
call site has passed the assistant's own message there since 3.3), so
"adjacent" is just "no `ChatMessage` row exists strictly between that
id and the current incoming one." A bare yes/no only dispatches
deterministically when adjacent; otherwise the proposal stays
`pending` — untouched, not expired/rejected/deleted — and the turn
falls through to ordinary model reasoning instead.

**A plain id-range query alone is provably unsafe under real
concurrency**, verified with a forced two-thread Postgres experiment
before writing any production code: Postgres allocates a sequence id
at INSERT time, not at COMMIT time, so a concurrently-inserted row
with a LOWER id can stay invisible to a reader for as long as its own
transaction stays open — letting the reader wrongly conclude "nothing
intervened." Closed with a transaction-scoped Postgres advisory lock
(`pg_advisory_xact_lock`, keyed on `space_id:user_id`, auto-released at
the next commit) held from the moment a message is appended through
the adjacency decision — no migration, no new table, the standard
primitive for serializing otherwise-unrelated transactions against one
logical key. `record_user_message` gained the same `commit=False`
escape hatch `record_assistant_message` already had, so this lock, the
message insert, and (when adjacent) the deterministic confirm/reject
all land in one atomic commit; a non-adjacent or ordinary turn commits
right away, releasing the lock before the potentially slow Orchestrator
call, which needs no lock at all. A real two-thread test forces the
exact adversarial interleaving and proves the concurrent thread cannot
even allocate an id for its row until the first thread's entire
critical section has committed.

Current Action State (3.11) now describes a pending proposal two ways
from the SAME `pending` status — never a new status value: adjacent
text is unchanged; non-adjacent text explicitly says the proposal is
still stored, is NOT expired/rejected/removed, that the model must
never claim it was acted on, and that calling the same `propose_*`
tool again creates a fresh, confirmable proposal. This directly
targets a real failure observed live during 3.17's own inspection: an
explicit contextual return ("Yes, add that task.") once produced a
model reply falsely claiming completion with no backing write. In this
checkpoint's own live gate, the same phrasing after an interruption
correctly produced a fresh `propose_create_task` call instead.

Live-verified (real provider calls, 8 fixed scenarios, no mocking):
immediate yes/no, create_task/create_event/delete_task each interrupted
by an unrelated weather question then a bare "yes" (none executed),
interrupted bare "no" (not incorrectly rejected), an explicit
contextual return after interruption (correctly re-proposed rather than
falsely claiming completion), and the full re-propose → confirm chain
(exactly one task actually created). Two of the eight opening proposal
turns hit the same pre-existing, stochastic text-only tool-call miss
already documented since 3.12b — unrelated to this fix, honestly
reported rather than retried.

### Checkpoint 3.18

Conversational CalendarEvent update: `propose_update_event` — moves,
renames, extends/shortens, or reassigns the life area of an EXISTING
event. Reuses the update_task (3.10) and create_event (3.15) patterns
simultaneously, plus inherits 3.17's adjacency guard automatically —
no genuinely new architecture, only a new `action_type` following
three already-established precedents at once.

**Context Assembly now exposes `event_id` and `ends_at`** for
CalendarEvent entries in "Coming Up" (`chat/context.py`'s
`_format_agenda_line`) — both were already fetched by the existing
`home_summary` aggregation, just not previously printed. `ends_at`
matters concretely: the model needs an event's *current* end time to
preserve its duration when proposing a move. Task-sourced agenda lines
are byte-for-byte unchanged.

**Duration-preserving move semantics** are pure model reasoning, not
Python: moving a bounded event (both `starts_at` and `ends_at` visible)
without a duration change means the model computes and submits BOTH
final values itself (an 11:00–12:00 event moved to 2 PM becomes
`starts_at=14:00, ends_at=15:00`); a point event's move only touches
`starts_at`; a pure duration edit ("30 minutes longer") sends only the
resulting final `ends_at`. No `duration_delta`/shift representation
anywhere — the `ProposedAction` always stores final, already-resolved
values.

**Merged-state validation happens twice, for two different reasons.**
At proposal time, `chat/service.py`'s own
`_validate_merged_event_temporal_range` merges the supplied `changes`
onto the EXISTING event's current values in plain Python and rejects
an invalid result (e.g. `starts_at=14:00` alone against an untouched
`ends_at=12:00`) *before* any `ProposedAction` exists — a small,
deliberate duplication of the same check
`calendar_service.update_calendar_event` already performs against the
merged ORM object, not a refactor of that REST-facing function. At
confirmation time, that existing function's own check remains
authoritative (via `confirm_and_execute`'s `update_event` branch),
since the event may have drifted since proposal.

**Timezone-awareness** is scoped to `ProposedCalendarEventUpdate`
(`calendar/schemas.py`) exactly like 3.15's create-side schema — the
direct REST `CalendarEventUpdate`/`PATCH /calendar/events/{id}` is
completely unchanged. Unlike the create schema, there was no inherited
same-named validator to override here (the base `CalendarEventUpdate`
deliberately has no range check at all, by its own long-standing
design), so this is a fresh validator with no repeat of 3.15's
ordering hazard. An explicit `null` for `starts_at` (not nullable on
the domain model) is rejected outright; an explicit `null` for
`ends_at` is accepted — it's a real, meaningful "make this a point
event again" change.

**Event drift** (the event changes between proposal and confirmation)
uses the exact same "last write wins" behavior already accepted for
`update_task` since 3.10 — no optimistic concurrency/versioning was
added. The stored final patch is applied against whatever the event's
current state is at confirmation time; a field the proposal doesn't
touch simply survives whatever else changed it in the meantime.

`ConfirmResult` gained `event_action: Literal["created", "updated"]`,
`CalendarEvent`'s own exact counterpart to `task_action` (3.10/3.13) —
needed for the same reason: one shared `event` field, two producing
action types, no way to say "updated" instead of "added" without it.

Live-verified (real provider calls, 7 fixed scenarios, no mocking): an
EN move and an AR move each independently completed the full
propose→confirm loop with 0 confirm-turn `AiTrace` and the correct
duration-preserving final instant; a duration-only extension; a
title-only rename (start time confirmed unaffected); two
same-titled/different-time events correctly triggering a clarification
with no `ProposedAction`, followed by a disambiguating reply
("the 2 PM one") correctly resolving to the right `event_id`; and 3.17's
adjacency guard correctly protecting an update proposal across an
unrelated weather interruption — the interrupted "yes" reached the
model (not 0 `AiTrace`) and produced a fresh, correct re-proposal
rather than executing the stale one, with zero update_event-specific
code required for that protection. Three of the run's proposal turns
hit the same pre-existing, stochastic text-only tool-call miss already
documented since 3.12b (the model's own text reasoned correctly about
duration preservation but didn't call the tool that turn) — unrelated
to this checkpoint's own correctness, honestly reported rather than
retried; every tool call that DID happen was correct (right `event_id`,
right preserved duration, right point-event/title-only/duration-only
handling), and no false completion claim occurred anywhere in the run.

- Documented limitation, unchanged: the "Coming Up" context window is
  ~8 days out (`tomorrow + 7 days`, computed client-side) capped at 15
  items per section — an event further out has no visible `event_id`
  for the model to reference, and BAZRA must not guess one. A Calendar
  search/read tool or expanded horizon would address this; deliberately
  out of scope here (see the 3.18 design report's own Part T finding).

### Checkpoint 3.19

Conversational CalendarEvent removal: `propose_delete_event` — a
direct combination of three already-proven patterns rather than new
architecture: `delete_task`'s (3.13) soft-delete/confirmation shape,
`update_event`'s (3.18) event-identity exposure, and 3.17's adjacency
guard, inherited automatically with only a registration entry.
`ProposedCalendarEventDelete(event_id: int)` mirrors `ProposedTaskDelete`
exactly — no title/time snapshot; the database event is authoritative,
re-fetched fresh at both proposal and execution time.

**"Cancel," "delete," and "remove" are natural synonyms today, with
zero extra code**: the write-intent verb group already included
"cancel" alongside "delete"/"remove"; narrowing only the noun group
(dropping "event"/"meeting" and "حدث"/"موعد" — the same noun group
already narrowed once for "task"/"مهمة" in 3.13, now narrowed a third
time) made all three verbs reachable for CalendarEvent simultaneously.
"reminder"/"life area"/"تذكير" deliberately stay declined.

**The external-effect boundary is the one genuinely new piece of
reasoning** — verified with a repo-wide search that no external
calendar integration exists anywhere (no Google/Outlook, no attendees,
no invites, no sync/webhooks) before writing any wording. The tool
description, system instructions, deterministic confirmation, and
success reply all explicitly state the action only affects BAZRA's own
local calendar, and are instructed to never say or imply attendee
notification, a real-world cancellation, or an external calendar
change — live-verified: even a stochastic text-only reply that never
called the tool still correctly avoided implying an external effect.

**Generic rejection-copy fix** (approved alongside this checkpoint,
applies to every action type, not just delete_event): the single
literal `"Okay, I won't create that."` previously used for every
rejected action — update, delete, memory included — was replaced with
`_reply_for_rejected_action`, keyed on the pending proposal's own
`action_type` (read before `actions_service.reject` changes its
status), with both English and Arabic variants reusing the existing
`_is_arabic` per-message mechanism already used by every other
confirmation renderer in this file — not a new localization framework.
0 LLM, no schema change, no `ProposedAction` status change.

`ConfirmResult.event_action` gained `"deleted"` alongside the existing
`"created"`/`"updated"` — `CalendarEvent`'s own exact counterpart to
`task_action`'s three-way precedent. Event drift (approved decision):
deletion cares only about identity/existence, never stale field
values — an event updated after the delete proposal but before
confirmation still gets removed; an event already archived by another
path before confirmation fails safely (rolls back to pending, no false
success, no resurrection) — live-verified directly by archiving an
event through a separate service call mid-flow and confirming the
stale proposal.

Live-verified (real provider calls, 6 fixed scenarios, no mocking): an
EN cancel and an AR cancel each completed the full propose→confirm
loop, the AR one with 0 confirm-turn `AiTrace` and a real archive;
two same-titled/different-time events correctly triggering a
clarification with no `ProposedAction`, followed by "the 2 PM one"
correctly resolving to the right `event_id`; 3.17's adjacency guard
protecting a delete proposal across a weather interruption (a
subsequent real provider hiccup on the interrupted "yes" turn was
honestly reported, not retried — the safety property itself held
regardless, confirmed by the event remaining active); an immediate
rejection producing the new delete-specific wording ("Okay, I won't
remove that.") with 0 `AiTrace`; and the archived-elsewhere race
producing a safe execution failure with the proposal correctly still
`pending`, never a false success. One turn's own text falsely claimed
a removal that never happened (no tool was called that turn, so
nothing was actually written) — the same pre-existing, stochastic
text-only reliability gap documented since 3.12b, reported honestly
here rather than hidden; the database itself was never wrong, only
that one turn's own generated text.

### Checkpoint 3.21

Verified-execution truthfulness guard — fixes the Phase 3 closure blocker
the 3.20 inspection identified: a model turn could occasionally claim a
write "completed" with no tool call and no `ProposedAction` behind it.
Core invariant added: **model text is never execution evidence.**

Three narrow, structural fixes, none of them a keyword blacklist or a
second model call:

**Stale yes/no truthfulness** (`chat/service.py`'s `send_message`,
just before the plain-fallthrough persist): when the CURRENT message is
itself classified by the same deterministic `_classify_narrow_yes_no`
already used for the adjacent path as a bare yes/no, AND a real
`ProposedAction` is pending, AND it is no longer conversationally
adjacent (3.17) — the model is still consulted (cost budget unchanged:
still 1 LLM call), but if it doesn't produce a fresh tool call, its own
text is discarded in favor of a fixed, always-true reply ("That change
wasn't executed. If you still want it, I can prepare it again." / its
Arabic equivalent) rather than trusted, regardless of what it claims.
An ordinary read, weather question, or new ambiguous request never
triggers this — `narrow_answer` is `None` for all of those by
construction, so normal interruption/read behavior during a pending
proposal is unaffected (verified directly, both in tests and against
the real provider).

**Invalid proposal tool call**: a `propose_*` call that fails
deterministic validation (bad arguments, or a target that doesn't
resolve) creates no `ProposedAction` and executes nothing — the
model's own accompanying text (occasionally written as if the call
would succeed) is no longer preferred over a deterministic fallback,
applied identically across all 8 proposal handlers via one shared
`_reply_for_invalid_proposal` helper.

**Execution-failure wording fix** (the small defect 3.20 flagged): the
single literal "Something went wrong while creating that task" used
for every failed execution, regardless of actual `action_type`, is now
keyed on the pending proposal's own `action_type` (captured before
`confirm_and_execute` runs, no schema change) via the same
per-action-type-table pattern as 3.19's rejection-copy fix — grouped
by verb (create/update/delete), not by domain, per the approved
wording. 0 LLM, no new `ProposedAction` status, rollback-to-pending
semantics on execution failure are completely unchanged.

**Deliberately NOT fixed — a documented residual gap**: a genuinely
FRESH write request (no pre-existing pending proposal) that the model
answers with plain text instead of calling the required `propose_*`
tool is structurally indistinguishable, at the point the reply is
persisted, from an ordinary read that also produces plain text with no
pending proposal in play. Guarding this population would require
either a lexical completion-word blacklist or a second classifier
model — both explicitly excluded by this checkpoint's own scope. The
former adversarial test for this exact case was renamed (not
weakened) to `test_fresh_write_text_only_miss_is_a_documented_residual_truthfulness_gap`
to say so plainly rather than reading as though it were fully solved;
its DB-integrity assertion is unchanged.

Live-verified against the real provider, no mocking: two scenarios
(`propose_delete_task`, `propose_delete_event`, one via a genuine
proposal, two via a directly-seeded real `ProposedAction` row to
isolate the guard from the separate, already-documented propose-step
miss rate) each produced the exact deterministic guard text on a real,
unmocked, non-adjacent "yes"/"no" turn, with the underlying row
provably untouched (`status` still `'pending'`) and the target
provably unmutated; a real execution-failure race (target archived via
a direct service call between proposal and confirmation) produced the
new action-aware wording exactly ("I couldn't remove that just now.
You can confirm again to retry.") with 0 confirm-turn `AiTrace`, same
as before this checkpoint; ordinary reads and a pending-proposal-plus-
weather interruption both worked normally. Three propose-turns in this
same run naturally missed their tool call (the same pre-existing,
already-documented-since-3.12b stochastic phenomenon, not a new
regression — nothing in this checkpoint touches the propose-decision
path at all) — reported honestly; none of the three produced a false
completion claim (two declined/asked to proceed honestly in text, one
asked a clarifying question), consistent with this checkpoint's
finding that the un-guarded residual population is real but was not
observed to misfire during this particular live-gate run.

`ProposedAction`'s state machine, the 3.17 advisory lock, and AiTrace
are all byte-for-byte unchanged — no migration.

## Run locally (without Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --reload
pytest
```

## Run via Docker Compose (from repo root)

```bash
docker compose up -d postgres backend
curl http://localhost:8001/api/v1/health
```
