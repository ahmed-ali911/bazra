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

### Checkpoint 3.23

Mandatory structured turn routing — the primary chat model contract changed
from "text OR a tool call" to "exactly one tool call, always." A new
`respond_with_text(kind: "answer" | "clarification", text: string)` tool is
now offered alongside every `propose_*`/`get_weather` tool, and the primary
chat call now forces `tool_choice={"type": "any", "disable_parallel_tool_use":
true}` (`orchestrator/service.py`'s `_PRIMARY_CHAT_TOOL_CHOICE`) — a bare,
tool-less text reply is no longer a possible response shape at all. This
structurally eliminates the specific failure the 3.20/3.21/3.22 checkpoints
tracked as "Population C, part 1": the model silently omitting a required
`propose_*` call for a fresh write and defaulting to plain text.

**`generate_reply` enforces the contract itself, not just the provider**:
zero or multiple parsed `tool_use` blocks both raise
`OrchestratorContractViolationError` (a subclass of the existing
`OrchestratorError`, so `chat_service`'s existing failure handling catches it
with no new code there) — never silently falling back to bare text or
picking the first of several calls. A defensive `stop_reason` cross-check
(now exposed on `ModelResponse`, read straight off the raw provider response,
never persisted to `AiTrace`) logs a warning on disagreement but never gates
correctness by itself — parsed `tool_use` blocks remain authoritative.

**`respond_with_text` is exactly as inert as every `propose_*` tool call**:
it creates no `ProposedAction`, touches no domain module, and its own
`_handle_respond_with_text` returns a plain candidate string rather than
persisting immediately — specifically so the existing 3.21 stale-proposal
guard can still override it before anything is persisted. MODEL TEXT IS
STILL NEVER EXECUTION EVIDENCE: the guard's own condition
(`narrow_answer is not None and pending is not None and not adjacent`) is
completely unchanged, it just now fires against `respond_with_text`'s
candidate text instead of the old bare `result.text`.

**The honest, explicitly-not-solved residual**: a fresh write with no
pre-existing pending proposal can still be *misrouted* — the model calls the
always-legal `respond_with_text(kind="answer", ...)` instead of the correct
`propose_*` tool, and that tool's own `text` argument can still falsely claim
completion. `chat_service` has no structural signal, when `pending is None`,
to distinguish that misroute from a `respond_with_text` call that correctly
judged no action was needed — both produce an identical shape. Guarding this
would require either a lexical blacklist or a second classifier model, both
explicitly excluded. The pre-existing adversarial test for this exact case
(`test_fresh_write_misrouted_to_respond_with_text_is_a_documented_residual_truthfulness_gap`)
still passes, now demonstrating the misroute shape instead of the old bare-
text shape, with identical DB-integrity guarantees.

Live-verified against the real provider (15 scenarios, no mocking): ordinary
English/Egyptian-Arabic conversation, a clear task creation, a clear event
deletion, an ambiguous event move (two same-titled candidates — correctly
asked which one, never guessed an id), a missing-duration meeting request
(correctly asked for the time), informational/negated write-shaped questions
in both languages, factual and interpretive weather (AiTrace deltas exactly
1 and 2, matching the unchanged cost model), and the full stale-yes/stale-no/
immediate-confirm/immediate-reject family. One especially instructive result:
on a non-adjacent stale "yes," the model sometimes correctly *re-proposes* a
fresh, real `propose_delete_task` call (a new `ProposedAction`, requiring its
own fresh confirmation) rather than needing the deterministic override at
all — both outcomes are safe; the override exists for when the model doesn't
do that. No false completion claims were observed in this run; DB state and
disposable data were verified clean afterward.

No new `ProposedAction` status, no change to the 3.17 advisory lock or the
confirm/reject/execution-failure paths, no migration.

### Checkpoint 3.25

Closes the final demonstrated Phase 3 truthfulness gap: a fresh write with no
pending proposal, misrouted by the primary model to `respond_with_text`,
whose own `text` argument could falsely claim BAZRA already completed a
mutation. An independent, narrowly-scoped verifier
(`orchestrator_service.verify_no_mutation_claim`) now sits between every
`respond_with_text` candidate and the user: a separate, cheap model
(`claude-haiku-4-5`, resolved via a new small `purpose -> model` override
table in Model Router — `_MODEL_BY_PURPOSE`, falling back to the existing
default for every other purpose, no other model selection changed) answers
exactly one narrow question — "does this candidate reply claim BAZRA already
completed a state-changing action?" — via a forced, single, strict boolean
tool call (`certify_claim`). It never sees the user's message, conversation
history, Context Assembly, Memory, or Space data — only the candidate text
itself, deliberately minimizing both the classification problem's difficulty
and its privacy exposure.

**Fail-closed, not merely best-effort**: an explicit `true` certification, a
provider failure, or any contract violation (zero/multiple tool calls, wrong
tool name, missing/non-boolean field) all collapse to the exact same
deterministic reply ("I haven't made that change. If you want, I can prepare
it for confirmation." / a natural Arabic equivalent) — never to the
candidate text. The verifier never generates replacement text itself; the
application owns that, exactly as it already owns every real execution-success
reply.

**Ordering matters**: the existing 3.21 stale-proposal guard is checked
*before* the verifier even runs, not after — that guard's own condition
depends only on already-known DB/message state, never on candidate text, so
when it fires there is nothing left to extract or verify. This means zero
extra model calls for that branch, not merely fewer lines of code.

**A real implementation bug was caught and fixed by this checkpoint's own
live gate, not shipped**: the verifier initially sent the candidate as a bare,
unattributed `{"role": "user", "content": candidate_text}` message. A live
run against the exact adversarial set from the prior checkpoint's own
inspection scored only 21/27 (and a repeat, 5/10 on the unsafe subset alone)
— reproducibly missing the *same* short, first-person completions
("Done — I've added it.", "I removed the meeting.") every time, because an
unattributed user-role message let the verifier read those as the *user*
reporting their own past action, matching the system prompt's own explicit
safe-exception clause. Wrapping the candidate as `"Candidate reply:\n{text}"`
(matching the framing already validated in the prior checkpoint's own
standalone experiment) resolved it — two independent full re-runs afterward
both scored 27/27, real Haiku, no mocking.

Live-verified end to end (no mocking): English and Egyptian Arabic
conversation, an ambiguous-target clarification, factual and interpretive
weather (verifier never invoked for either, AiTrace deltas unchanged at 1 and
2), a stale interrupted "yes" (the model re-proposed a fresh, real
`propose_delete_task` rather than needing the override — an equally safe
outcome), immediate confirm/reject (0 LLM calls, unaffected), and — the
scenario this whole checkpoint exists for — a forced misroute to
`respond_with_text` with a real, unmocked verifier call: the false claim
never reached the user; the deterministic fail-closed reply did.

**Honest residual, not overclaimed**: a verifier *false negative* (a genuine
false-completion claim the verifier itself misjudges as safe) remains
possible in principle — this is the accepted cost of using any probabilistic
classifier at all, not a structural hole the application could have caught
and didn't. A dedicated test keeps this residual visible rather than hidden.
No new `ProposedAction` status, no AiTrace schema change, no migration.

**Phase 3 acceptance note (Checkpoint 3.26) — a testing-methodology
distinction worth preserving**: the acceptance audit's controlled
verifier-failure scenario injected its failure by intercepting
`model_router_service.complete` itself for `purpose == "claim_verification"`,
one layer above the real provider-call error-tracing path inside Model
Router. That scenario correctly proved the chat-level fail-closed behavior
(verifier failure → unsafe candidate blocked → deterministic safe reply, no
false completion shown) but produced zero new `AiTrace` rows for the
injected failure, since it never reached `complete()`'s own
`_safe_record_trace(status="error", ...)` call. This must not be read as
evidence that genuine production provider failures go untraced — a real
failure at the actual `_call_anthropic`/SDK boundary passes through the
router's normal, unconditional error tracing exactly as it always has,
covered separately by Model Router's own tests. The scenario proves
fail-closed chat behavior under verifier failure; it does not independently
prove production provider-error tracing.

**Evidence-labeling rule for future closure reports**: runtime claims should
distinguish ACTUAL CAPTURED VALUE (printed/observed during the real run),
DATABASE-VERIFIED VALUE (queried from rows the run actually committed),
CODE-INFERRED BEHAVIOR (deduced from reading the source, not observed at
runtime), and NOT RETAINED / NOT AVAILABLE. Code-inferred behavior should
never be presented as captured runtime evidence.

## Phase 4 — Attention & Proactivity

### Checkpoint 4.1

Task gained an explicit `priority` field: `low | normal | high`, defaulting
to `normal` (additive migration, every pre-existing task backfilled to
`normal`). This is a plain string column, the same code-validated
convention as `Task.status` — not a native Postgres enum.

Supported everywhere Task fields already are: direct REST create/update,
and conversationally via `propose_create_task`/`propose_update_task`. Chat
tool descriptions ask the model to set it *only* when the user's own
message actually indicates a priority — this is prompt-level product
guidance, not a deterministic parser; conversational priority remains
model-interpreted user intent, exactly like every other optional field
(`due_at`, `life_area_id`) a proposal tool already accepts. Whatever the
model proposes is always visible in the deterministic `ProposedAction`
confirmation text before anything executes, and — unchanged from every
other field — requires explicit adjacent confirmation before any mutation
occurs. Priority introduces no new write authority: it flows through the
existing `propose_*` → schema validation → `ProposedAction` → confirmation
→ `confirm_and_execute` path unmodified (confirmed by inspection: both
`create_task` and `update_task` already apply their input generically —
via `Task(**data.model_dump())` and a field-by-field `setattr` loop
respectively — so the new field required zero execution-path code changes).

Confirmation wording is deliberately asymmetric between create and update:
a **create** with priority omitted or explicitly `"normal"` stays silent
about it (normal is the invisible default for a brand-new task); an
**update** that explicitly proposes `priority="normal"` is still stated
visibly, since it only ever appears in that proposal because the user
asked for exactly that change.

Live-verified against the real provider: a high-priority creation and a
low-priority update both correctly extracted the stated priority, showed
it in the confirmation, and left the task unchanged until explicit
confirmation; a plain creation with no priority language present resulted
in `normal` with no invented priority — reported as one observed run, not
as proof the model can never infer a priority under any phrasing.

### Checkpoint 4.2

Introduced the deterministic **Attention Signal Engine**
(`app/modules/attention/`) — the read-only foundation the rest of Phase 4
builds on. A **Signal** is a deterministic fact about current Task /
CalendarEvent / InboxItem state (e.g. "this task is overdue by N
seconds"). It is explicitly *not* a message, notification, persisted row,
priority decision, recommendation, or LLM judgment — this checkpoint makes
**zero** Model Router / Anthropic calls.

Exactly five v1 signal types are derived, live, on every call — nothing is
persisted (no Signal table, no migration):

- `TASK_OVERDUE` — open, non-archived task with `due_at` in the past.
- `TASK_DUE_TODAY` — open task due before the next **local** midnight.
- `TASK_DUE_SOON` — open task due within the next 4 hours (locked v1 window).
- `EVENT_UPCOMING` — non-archived event starting within the next 2 hours
  (locked v1 window).
- `INBOX_NEEDS_ATTENTION` — unread, non-archived inbox item.

A task due soon *and* due today produces **both** signals — Checkpoint 4.2
deliberately does not deduplicate across types (or across sources, e.g. an
overdue task and its own "Completed: ..." inbox item can both appear);
that ranking/dedup policy belongs to Checkpoint 4.3's scoring engine, not
this one.

**Timezone authority** (Architecture Contract 4.0b-R1 correction #1):
`generate_signals(db, space_id, now, timezone_name)` takes the evaluation
instant as an explicit parameter — nothing in this module calls
`datetime.now()` itself, so every signal in one pass is guaranteed to be
judged against the exact same instant. `timezone_name` must be a
validated IANA zone name; an unresolvable name raises a controlled
`InvalidTimezoneError`, never a silent UTC fallback. The next local
midnight (`TASK_DUE_TODAY`'s upper bound) is constructed from the local
**calendar date** (`datetime.combine(local_date + 1 day, time(0, 0),
tzinfo=zone)`), never a naive `+timedelta(days=1)` on a UTC instant —
proven with real `America/New_York` spring-forward (2026-03-08, a
23-hour local day) and fall-back (2026-11-01, a 25-hour local day) tests
that assert the exact correct UTC instant on both sides of the boundary.

**Canonical snapshot serialization**: every timestamp embedded in a
Signal's `snapshot` is normalized to UTC ISO-8601
(`dt.astimezone(timezone.utc).isoformat()`, e.g.
`2026-09-30T12:00:00+00:00`) so two equivalent instants that arrived with
different UTC-offset representations always serialize identically —
Checkpoint 4.4's dismiss-invalidation comparison will depend on exact
structural equality here, never fuzzy time comparison.

Signals are returned in a fixed, neutral technical order (grouped by
`signal_type`, then by `source_id`) — **not** an urgency order; scoring,
ranking, thresholds, cooldown, snooze, dismiss, `attention_feedback`
persistence, `ACTED_ON`, `APP_OPENED`, and Daily Brief are all explicitly
out of scope for this checkpoint and remain unimplemented.

### Checkpoint 4.3

Added `app/modules/attention/scoring.py` — a pure, deterministic function
of its own inputs that turns Checkpoint 4.2's raw `Signal`s into scored,
deduped, suppression-evaluated, ranked `AttentionCandidate`s. Zero
database queries, zero Model Router/Anthropic calls, zero persistence —
`attention_feedback` and every mutation API (snooze/dismiss/mark-acted-on)
remain unimplemented; that is Checkpoint 4.4.

**Locked scoring formula** — base score per signal type
(`TASK_OVERDUE=50`, `EVENT_UPCOMING=45`, `TASK_DUE_TODAY=40`,
`TASK_DUE_SOON=25`, `INBOX_NEEDS_ATTENTION=15`), plus:

- Task priority (task signals only): `high=+20`, `normal=0`, `low=-10`.
- `TASK_OVERDUE`: `min(30, floor(overdue_hours/24) * 5)`.
- `TASK_DUE_SOON` proximity: `round(20 * (1 - hours_until_due/4))`.
- `TASK_DUE_TODAY` proximity: `round(15 * max(0, 1 - hours_until_due/12))`.
- `EVENT_UPCOMING` proximity: `round(25 * (1 - minutes_until_start/120))`.
- `INBOX_NEEDS_ATTENTION`: `min(10, floor(age_hours/24))`.

Every `round(...)` above uses one explicit, shared **round-half-up** rule
(`scoring._round_half_up`, computed against an exact `fractions.Fraction`
value, never a float re-approximation) — deliberately not Python's
built-in `round()`, which uses banker's-rounding-to-even.

**A genuine contradiction in the original architecture scenario table,
resolved during this checkpoint**: two of the required worked examples
land on an exact `.5` proximity tie — a HIGH task due in 2h needs
`round(12.5)` in its `TASK_DUE_TODAY` score, and a NORMAL task due in 10h
needs `round(2.5)`. The brief's own numbers required these two ties to
round in *opposite* directions (12.5→12 alongside 2.5→3), which no single
rounding rule can satisfy. Flagged back rather than silently forced to
pass; the resolution was to adopt round-half-up as the one shared rule
and correct the due-in-2h scenario's expected score from 72 to **73**
(50→60 base+priority, +13 proximity). The due-in-10h scenario's expected
43 needed no correction under this rule. See
`test_round_half_up_on_exact_half_values` and the two
`test_due_today_proximity_hits_*_exact_half_tie*` tests for the explicit
proof.

**Same-source dedup** (`(source_type, source_id)` identity): only the
highest-scoring candidate per identity survives — e.g. a task due soon
and due today today both score independently, but only one is retained,
never summed or merged. An exact-score tie is broken by a fixed
signal-category order (`TASK_OVERDUE > EVENT_UPCOMING > TASK_DUE_TODAY >
TASK_DUE_SOON > INBOX_NEEDS_ATTENTION`) — the SAME order also used as the
second key in the final ranking tie-break, never two independently
drifting orderings. Different sources (e.g. an overdue task and its own
"Completed: ..." inbox item) are never deduped against each other.

**Final ranking** sorts by: score descending → signal-category order →
earliest `relevant_timestamp` (missing timestamps sort last) → lowest
`source_id` — never by input/list order (proven by dedicated
shuffled-input determinism tests).

**Threshold policy** is selected per consumer surface, not hard-wired:
`app_opened` requires `score >= 45`; `daily_brief` requires `score >=
20`. Neither surface's own endpoint exists yet.

**Suppression** evaluates three feedback-based gates against a caller-
supplied `SuppressionState` (surfaced_at/snoozed_until/dismissed_at/
dismissed_snapshot/acted_on_at) — a narrow, storage-agnostic read-model
input Checkpoint 4.4 will construct from its own `attention_feedback`
table, never the reverse. Locked precedence, first match wins:
1. **`SNOOZED`** — `snoozed_until` is set and `now < snoozed_until`
   (exactly `now == snoozed_until` is no longer suppressed).
2. **`DISMISSED_UNCHANGED`** — `dismissed_at` is set and the stored
   snapshot structurally equals the Signal's *current* snapshot (never a
   bare `updated_at` or fuzzy time comparison — an edit that changes the
   snapshot make the old dismissal stop suppressing).
3. **`COOLDOWN`** — `surfaced_at` is set and `now < surfaced_at + 12h`
   (exactly `surfaced_at + 12h` is eligible again).
4. **`BELOW_THRESHOLD`** — applied only by `rank_for_surface`, after the
   three gates above, using the selected surface's own threshold.

`acted_on_at` is accepted in `SuppressionState` but deliberately never
consulted by the gates — a historical acted-on exposure never itself
suppresses a new, current Signal (only snooze/dismiss/cooldown do).

### Checkpoint 4.4a

Added `app/modules/attention/models.py` (`AttentionExposure`, a new,
additive `attention_exposures` table) and `app/modules/attention/
history.py` (`record_exposure`, `load_suppression_states`) — the first
piece of **durable** Attention state. The distinction this checkpoint
exists to make precise:

- **`Signal`** (4.2): derived live, never persisted.
- **`AttentionCandidate`** (4.3): derived/scored live, never persisted.
  **Selection/ranking is NOT delivery** — scoring and ranking a
  candidate creates no durable row at all.
- **`AttentionExposure`** (4.4a): durable evidence that a candidate was
  **actually delivered** to a real surface. Only a future surfacing
  consumer, after it genuinely shows something to the user, may call
  `record_exposure` — nothing in this checkpoint calls it on its own
  initiative, and no such consumer exists yet.

**Table shape** — one table, by design (see the accepted architecture
inspection for the full reasoning): an IMMUTABLE core written once at
insert (`task_id`/`event_id`/`inbox_item_id` — exactly one non-null,
`CHECK`-enforced; `signal_type`, `surface`, `policy_version`, `score`,
`reason_codes`, `exposure_snapshot`, `surfaced_at`), plus three nullable
feedback columns declared now as part of the accepted shape but **never
written by 4.4a**: `snoozed_until` (4.4b), `dismissed_at` (4.4b),
`acted_on_at` (4.4c). Typed nullable FKs, never a generic
`(source_type, source_id)` pair — the same concrete-FK convention
`ProposedAction.executed_task_id`/`executed_memory_id` already
established.

**`updated_at` (inherited from `BaseModel`) is ordinary bookkeeping
only** — it bumps on every feedback write like any other table, but is
**never** consulted as attention evidence, a user-action signal,
dismissal-invalidation evidence, or future learning evidence. Only the
four explicit semantic columns above carry those meanings;
`load_suppression_states` never reads `updated_at` at all (proven by a
dedicated structural test).

**Policy provenance**: `scoring.POLICY_VERSION` (currently `"4.3"`) — a
bare, manually-bumped string, no registry. Persisted verbatim on every
row; a historical row's score is never recomputed under a newer policy
and presented as original evidence.

**`record_exposure(db, space_id, user_id, candidate, surface,
surfaced_at, commit=True)`**: `surfaced_at` is an explicit, caller-
supplied delivery instant — never substituted with the candidate's own
scoring-time `now`, since the caller is the only party that knows the
real delivery moment. Validates, before writing anything: `surface`/
`signal_type` are members of the locked value sets; the space is
actually owned by `user_id` (`Space.user_id` match); and the Signal's
own source genuinely exists in that space, re-verified through that
source's own scoped getter (`tasks_service.get_task`/
`calendar_service.get_calendar_event`/`inbox_service.get_item`) — never
trusted merely because an id was present on the Signal. `reason_codes`/
`exposure_snapshot`/`score` are taken entirely from the already-scored
`AttentionCandidate`, never from any separately-supplied client JSON.

**`load_suppression_states(db, space_id, identities)`**: batch-
constructs 4.3's `SuppressionState` for many `(source_type, source_id)`
identities in at most 3 queries total (one `DISTINCT ON` query per
distinct source type present, never one per identity). For each
identity, uses the single latest exposure row — `ORDER BY surfaced_at
DESC, id DESC` — **across all surfaces**: cooldown is global, not
per-surface, which falls directly out of 4.3's own already-shipped
`SuppressionState` having exactly one `surfaced_at` field. Repeated
real deliveries (e.g. the same source surfaced again after cooldown
expires) each create their own distinct row — proven directly, along
with the persisted-`surfaced_at`-drives-cooldown integration (11h59m
suppressed, exactly 12h eligible, both against real persisted rows,
not synthetic `SuppressionState` objects).

**Not implemented in 4.4a** (structurally proven by dedicated tests —
no such functions exist in `history.py`): snooze/dismiss mutation APIs
(4.4b), the deterministic ACTED_ON evaluator (4.4c). `acted_on_at`
remains a declared, nullable column that `record_exposure` never sets.
A future BAZRA Evolution System may eventually consume this durable
exposure history as factual evaluation evidence, but no learning,
preference-inference, or policy-comparison exists anywhere in 4.4a.

### Checkpoint 4.4b

Added `record_dismiss` and `record_snooze` to `history.py` — explicit
user feedback against one specific, already-delivered
`AttentionExposure`, targeted by its own `exposure_id` (never "the
latest exposure for this source" — the caller always knows exactly
which delivered exposure it's responding to). No migration — both
columns (`dismissed_at`, `snoozed_until`) already existed, declared but
unwritten, since 4.4a.

**Dismiss is first-write-wins.** `record_dismiss(db, space_id,
user_id, exposure_id, now)` uses a single conditional `UPDATE ... WHERE
id=... AND space_id=... AND user_id=... AND dismissed_at IS NULL ...
RETURNING *` — the same real-row-lock replay-guard convention
`actions_service.confirm_and_execute`/`reject` already established. A
duplicate dismiss is a **true no-op**: `dismissed_at` is never
rewritten and `updated_at` is never bumped, because the conditional
UPDATE touches zero rows once `dismissed_at` is already set — proven
with a real two-thread Postgres concurrency test (same pattern as
`test_actions_concurrency.py`).

**Snooze is not idempotent the same way — a later snooze can be a
genuine new instruction (re-snooze).** `record_snooze(db, space_id,
user_id, exposure_id, snoozed_until, now)` validates `snoozed_until >
now` *before* touching the database (`InvalidSnoozeInstantError`,
zero mutation, otherwise) — a `snoozed_until` at or before `now` could
never actually suppress anything under 4.3's own locked `now <
snoozed_until` gate. The write itself is one conditional UPDATE guarded
by `snoozed_until IS DISTINCT FROM :requested` (NULL-safe inequality) —
a single statement that correctly covers all three cases: first snooze
(existing `NULL`, always distinct → write), a genuine re-snooze
(existing value differs, earlier or later, both legitimate → write),
and an **exact-duplicate** request (existing value already equals the
requested one → excluded from the `WHERE` → zero rows touched → true
no-op, `updated_at` untouched, exactly mirroring dismiss's own
duplicate handling).

**Snooze and dismiss coexist — neither write clears the other.** Both
are preserved as independent, equally-true historical facts; 4.3's
already-locked gate precedence (`SNOOZED` → `DISMISSED_UNCHANGED` →
`COOLDOWN` → `BELOW_THRESHOLD`) alone decides which currently applies —
no new precedence logic lives in `history.py`. Concretely: snooze until
11:00 then dismiss at 09:10 means `SNOOZED` governs until 11:00, after
which — if the signal's snapshot is still unchanged — `DISMISSED_UNCHANGED`
takes back over automatically.

**Stale feedback is historical truth, never current suppression
authority.** `load_suppression_states` (4.4a) only ever reads the
single *latest* exposure row per source (`surfaced_at DESC, id DESC`).
A delayed dismiss/snooze call against an older, superseded exposure is
still accepted and persisted on that historical row — it is a true,
worth-keeping fact — but it is structurally invisible to any future
suppression evaluation once a newer exposure exists for that source,
with zero extra "is this still current" check needed anywhere.

**Immutable exposure core, enforced by construction, not by convention
alone**: both functions' own `UPDATE` statements have a `SET` clause
touching only `dismissed_at`/`snoozed_until` respectively — `space_id`,
`user_id`, every source FK, `signal_type`, `surface`, `policy_version`,
`score`, `reason_codes`, `exposure_snapshot`, and `surfaced_at` are
structurally untouched by either, proven by dedicated before/after
field-equality tests. `updated_at` remains ordinary bookkeeping only —
it legitimately bumps on a genuine feedback write (never on a true
no-op) but is never itself read as attention evidence, a user-action
signal, dismissal-invalidation evidence, or learning evidence; `acted_on_at`
remains completely untouched by any 4.4b function.

**Known, accepted limitation for v1**: `attention_exposures` stores
the *latest* snooze/dismiss state per exposure, not an append-only log
of every individual re-snooze instruction a user ever issued. If a
future BAZRA Evolution System genuinely needs every historical
re-snooze transition as evaluation evidence, a separate append-only
feedback-event table may be introduced then — deliberately not built
now. Concurrency for v1 is deliberately minimal: no version counters,
no optimistic-concurrency framework, no distributed locks — ordinary
Postgres row-level write semantics (last-committed-wins for concurrent
differing re-snoozes; the proven conditional-UPDATE row lock for
duplicate dismisses) are sufficient and are all that's implemented.

Still not implemented: the ACTED_ON evaluator, any mutation-time
hooks into Task/CalendarEvent/InboxItem, and natural-language
snooze-phrase parsing (every timestamp `record_snooze` receives is
already fully resolved by its caller) — all deferred to Checkpoint 4.4c
or later.

### Checkpoint 4.4c-1

Added `AttentionExposure.timezone_name` (nullable `String`, additive
migration) and made it a **required** parameter of `record_exposure`.

**What it records**: the validated IANA timezone context that gave the
surfaced Signal its temporal meaning at exposure time — concretely,
what made a `TASK_DUE_TODAY` signal mean "today". This is
**exposure-time provenance, not a user preference**: it is NOT Ahmed's
permanent timezone, NOT his current location, and NOT something future
Attention evaluation should assume still applies — two exposures for
the exact same source may legitimately carry different `timezone_name`
values if the surfacing context differed between them (proven by a
dedicated test). This app has no stored per-user timezone preference
anywhere; timezone is always supplied fresh, per request, matching
every other place in this codebase that needs one (chat's own request
parameter, Home's client-computed day boundaries) — `timezone_name`
here is captured the same way, at the moment of surfacing, never
assumed from anywhere else.

**Why nullable**: every pre-existing 4.4a/4.4b exposure row has no
timezone provenance at all and is **never backfilled** with a guessed
value — a historical `NULL` means "provenance unavailable," and a
future ACTED_ON evaluator must treat that as unknown/skip, never guess.

**Validation**: the exact same `zoneinfo`/IANA discipline Checkpoint
4.2's own `generate_signals` already established — an unresolvable name
raises `InvalidTimezoneError` *before* anything is persisted (proven:
zero rows are created on a rejected value). `record_exposure`'s
`timezone_name` parameter has no default value at all (proven by a
structural signature test) — never a server timezone, never
`datetime.now().astimezone()`, never a hard-coded fallback.

**Unaffected by this change** (proven by the full, unmodified 4.4a/4.4b
test suites still passing): `load_suppression_states`'s own output
(`SuppressionState` has no `timezone_name` field at all — suppression
was never timezone-dependent), `record_snooze`, `record_dismiss`,
cooldown, stale-exposure handling, and score/policy provenance.

Still not implemented: the ACTED_ON evaluator itself, any mutation-time
hooks into Task/CalendarEvent/InboxItem, and the same-transaction vs.
`SAVEPOINT`-isolated write question the 4.4c architecture inspection
raised. **One accepted correction to that inspection, recorded here
for the implementation that follows**: ACTED_ON attribution must never
be allowed to make an otherwise-valid Task/CalendarEvent/InboxItem
mutation fail — domain truth outranks derived Attention evidence. The
inspection's proposed same-transaction, all-or-nothing atomicity was
**not** accepted; a later checkpoint (4.4c-3) must instead use a local
isolation mechanism (e.g. a Postgres `SAVEPOINT`/SQLAlchemy nested
transaction) so an Attention-attribution failure can roll back only the
derived Attention write while the authoritative domain mutation still
succeeds. Not implemented in this slice.

### Checkpoint 4.4c-2

Added `app/modules/attention/resolution.py` — a pure, **100% DB-free**
deterministic evaluator answering one narrow question: *given an
exposure's original `signal_type`, and the source's own state
immediately after a qualifying mutation, does the concern that signal
represented still hold at the mutation's own instant?* It is not wired
into any production mutation path yet (that's Checkpoint 4.4c-3) and
has no Session, no SQL, no ORM objects, and never writes `acted_on_at`
— all structurally proven by dedicated tests.

**Tri-state result, never a bool**: `RESOLVED` / `NOT_RESOLVED` /
`UNKNOWN`. `UNKNOWN` means *insufficient trustworthy evidence* (e.g. a
`TASK_DUE_TODAY` exposure with a missing or unresolvable
`timezone_name`) — categorically different from `NOT_RESOLVED`, which
means the evidence says the concern still holds. The evaluator never
collapses the two, and never guesses a timezone to avoid returning
`UNKNOWN`.

**Inputs**: a small, immutable, DB-free post-mutation state dataclass
per source type — `TaskMutationState` (`status`, `due_at`,
`archived_at`), `EventMutationState` (`starts_at`, `archived_at`),
`InboxMutationState` (`read_at`, `archived_at`) — plus the mutation's
own authoritative `now` (always caller-supplied; the module never
calls `datetime.now()` itself) and, only for `TASK_DUE_TODAY`, the
exposure's own `timezone_name`.

**Source-type safety without a redundant parameter**: there is no
separate `source_type` string argument to independently get wrong —
`evaluate_acted_on`'s own `isinstance` check against the required
dataclass type for the given `signal_type` makes "Event state handed
to a Task signal" structurally impossible to pass silently. Any
mismatch (or an unsupported `signal_type`) raises
`ActedOnResolutionError` — a caller bug, never an evidentiary gap, and
it can never produce `RESOLVED` by accident.

**Per-signal semantics** (all against the mutation's own `now`, never
a later re-evaluation instant):
- `TASK_OVERDUE`: resolved by completion, archive, or `due_at` moving
  to now/the future, or `due_at` becoming null. Moving `due_at` later
  but still in the past is **not** resolved — "less overdue" is not
  resolution.
- `TASK_DUE_TODAY`: resolved by completion, archive, `due_at` null, or
  `due_at` moving to tomorrow-or-later (using the exact same
  calendar-day/DST-safe `_next_local_midnight` helper Checkpoint 4.2
  already established — reused directly, never reimplemented).
  **Concern-continuity protection**: if the mutation leaves `due_at <
  now`, this is **not** resolved — that's a transition into the more
  severe `TASK_OVERDUE` concern, not an improvement, exactly as the
  architecture review's own locked rule requires.
- `TASK_DUE_SOON`: same shape as `TASK_DUE_TODAY` (resolved by
  completion/archive/null due_at/moving outside the 4h window; moving
  `due_at` into the past is concern-continuity `NOT_RESOLVED`, not
  success).
- `EVENT_UPCOMING`: resolved by archive or `starts_at` moving outside
  the 2h window. `starts_at` moving into the past is **conservatively
  not resolved** — there is no `EVENT_OVERDUE` concept, and a backward
  move is treated as suspicious, never automatic success.
- `INBOX_NEEDS_ATTENTION`: resolved by marking read or archiving;
  marking **unread again** is a real mutation but re-opens the concern
  — correctly `NOT_RESOLVED` with zero special-casing, since it's
  simply a direct re-check of the same two fields.

**Timezone/DST proof**: dedicated tests prove the exact Kuwait (no-DST)
boundary, and both the real `America/New_York` 2026 spring-forward
(23-hour local day) and fall-back (25-hour local day) transitions,
using the same independently-derived exact UTC instants Checkpoint
4.2's own DST tests use — never "roughly 24 hours."

**Not implemented in this slice** (structurally proven): any hook into
`tasks_service`/`calendar_service`/`inbox_service`/`actions_service`
(no import of any of them exists in `resolution.py` at all), any
`SAVEPOINT`/nested-transaction logic, and any write of `acted_on_at`
anywhere. This evaluator proves only state association at the
mutation's own instant — never that the mutation *caused* the
resolution, and it is invoked only when Checkpoint 4.4c-3 decides a
qualifying mutation actually occurred.

### Checkpoint 4.4c-3

Wired the pure 4.4c-2 evaluator into all six real mutation boundaries:
`tasks_service.update_task`/`delete_task`,
`calendar_service.update_calendar_event`/`delete_calendar_event`,
`inbox_service.mark_read`/`dismiss_item`.

**What `acted_on_at` means**: a qualifying semantic source mutation
occurred, and the post-mutation state resolved the *latest* surfaced
concern for that source at that mutation's own instant. It does **not**
prove causation — not that BAZRA's own reminder caused Ahmed to act,
only temporal/state association at a known instant.

**Domain truth outranks Attention attribution, enforced structurally,
not just by convention.** `history.attempt_acted_on_attribution` —
the one shared helper every one of the six call sites invokes,
immediately before its own existing `db.commit()` — isolates its own
work (the latest-exposure lookup, the evaluator call, the conditional
write) inside a single Postgres `SAVEPOINT` (`Session.begin_nested()`).
Any exception from that work rolls back *only* the savepoint, is
logged (`logger.exception`, no payload content — never title,
description, or snapshot contents), and is swallowed — the function
never raises. This was proven, not assumed: disposable experiment
scripts run against the real dev Postgres (since deleted) confirmed
`begin_nested()` autoflushes pending outer changes before establishing
the savepoint, that a nested rollback leaves both the Session and the
outer object's already-flushed state fully intact, and that the
caller's own subsequent commit succeeds normally.

**A real, subtle bug this same empirical approach caught before it
shipped**: `begin_nested()`'s own autoflush can flush *unrelated*
pending writes made earlier in the same transaction — if one of those
is invalid, that is a genuine **core domain failure**, not an Attention
failure, and must never be miscategorized and swallowed. The fix:
`attempt_acted_on_attribution` calls `db.flush()` explicitly *before*
entering its own try/except, so a pre-existing bad write surfaces
honestly outside Attention's fail-open boundary, never inside it.

**Normal, expected no-attribution outcomes are not failures and
involve no exception or rollback at all**: no exposure exists for the
source; the latest exposure already has `acted_on_at` set; the
evaluator returns `NOT_RESOLVED` or `UNKNOWN`. All four are plain early
returns from inside the (still normally-committing) savepoint.

**Only the single latest exposure for a source is ever eligible** —
queried directly (`ORDER BY surfaced_at DESC, id DESC`, across all
surfaces), never derived from `SuppressionState`, and never excluded
merely because it happens to be currently snoozed, dismissed, or
within cooldown — ACTED_ON is historical attribution, not a
suppression gate. The write itself is first-write-wins
(`WHERE id=... AND acted_on_at IS NULL`), the same idempotent
conditional-UPDATE convention `record_dismiss` already established.

**No semantic value change, no attribution attempt** — now a
correctness requirement, not merely an optimization. Each of the six
functions captures the qualifying field(s)' pre-mutation value(s) and
only calls the hook when at least one actually changed (`due_at`
re-PATCHed to its own existing value, or a title/description/
life_area_id-only edit, never attempts attribution). For the three
delete functions this check is unconditional — the scoped getters they
call already guarantee `archived_at` was `NULL` beforehand, so every
reachable archive is a genuine first-time transition.

**One mutation-time `now` per call**: each of the six functions now
computes `datetime.now(timezone.utc)` exactly once, at the top, and
reuses that same instant for every timestamp it sets — `completed_at`,
`archived_at`, and the Attention hook's own `now` — never a second,
independently-called `datetime.now()` for Attention.

**Phase 3 remains fully isolated from Attention's own fate.** Because
the hook lives inside the same six functions `confirm_and_execute`
already calls, a confirmed Task/Event mutation automatically carries
attribution with zero Phase-3-specific code — and a contained
Attention failure during a confirmed mutation still results in
`ConfirmResult.outcome == "executed"`, the domain mutation durable, and
`acted_on_at` left `NULL` (proven directly, including through the
confirmed-ProposedAction path, not just the direct-REST path).

**Known, pre-existing transactional debt, surfaced by this checkpoint's
own empirical inspection (not introduced by it, and deliberately not
fixed here)**: `confirm_and_execute`'s `update_task`/`update_event`
branches call a domain service function that commits *internally*,
before `confirm_and_execute`'s own later `status="executed"` commit —
a real two-commit boundary, confirmed directly against the dev DB. A
failure strictly between those two commits (independent of Attention
entirely) can leave a `ProposedAction` stuck at `status="confirmed"`
forever, even though the domain mutation itself succeeded. This
predates Attention; fixing it would expand scope into Phase 3's own
transaction semantics and requires a separate, future hardening
checkpoint before any autonomous/proactive action capability expands
on top of `confirm_and_execute`.

**Evolution interpretation, unchanged from 4.4c-2**: `acted_on_at` is
temporal/state association evidence only — never a success score,
never causal credit, never a signal of user preference.

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
