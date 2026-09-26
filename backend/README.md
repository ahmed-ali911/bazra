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
