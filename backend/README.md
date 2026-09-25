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
  limitation, not an oversight.

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
