# BAZRA (بذرة)

A personal AI operating system and long-term digital companion.

This repository is being built incrementally, phase by phase. See
`docs/adr/` for architecture decisions and their reasoning.

## Status

**Phase 0 (Foundation) — complete.** Repo skeleton and Docker Compose,
backend skeleton and testing foundation, frontend skeleton and design
tokens, and single-user auth are all in place and verified against live
containers. Phase 1 (UI Foundation) is next.

## Prerequisites

- Docker and Docker Compose
- Only needed if running a service outside Docker: Python 3.12+, Node.js 22+

## Setup

```bash
cp .env.example .env
docker compose up -d
docker compose ps   # postgres, backend, frontend should all be running/healthy
```

A fresh Postgres volume has no tables yet — apply the migrations, then set
the app's one user password (there's no registration flow):

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.modules.auth.seed
```

To stop:

```bash
docker compose down          # keeps the pgdata volume (and your user/password)
docker compose down -v       # also removes the volume — full reset, you'll
                              # need to run the two commands above again
```

## Verify the stack end-to-end

Backend health:

```bash
curl http://localhost:8001/api/v1/health
# {"status":"ok"}
```

Auth flow (replace `yourpassword` with what you set above):

```bash
curl -i -c /tmp/bazra_cookies -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" -d '{"password":"yourpassword"}'
# 200, Set-Cookie: bazra_session=...

curl -i -b /tmp/bazra_cookies http://localhost:8001/api/v1/auth/me
# 200 {"authenticated":true}

curl -i -b /tmp/bazra_cookies -X POST http://localhost:8001/api/v1/auth/logout
curl -i -b /tmp/bazra_cookies http://localhost:8001/api/v1/auth/me
# 401 {"detail":"Not authenticated"}
```

Frontend: open http://localhost:5174 in a browser.

## Running tests

```bash
docker compose exec backend pytest
docker compose exec frontend npx vitest run
```

See `backend/README.md` and `frontend/README.md` for what each suite covers.

## Port mappings

`docker-compose.yml` currently maps these host ports:

| Service  | Host port | Container port |
|----------|-----------|----------------|
| postgres | 5434      | 5432           |
| backend  | 8001      | 8000           |
| frontend | 5174      | 5173           |

**These non-default host ports (5434/8001/5174) are a local override
specific to the dev machine this was built on** — it already had other,
unrelated projects bound to the standard 5432/8000/5173, so those were
remapped to avoid collisions. They are not architecturally significant and
are not "the correct" values BAZRA requires. If you're setting this up on a
machine that doesn't have those collisions, standard 5432/8000/5173 work
just as well — just change the host-side number (left of the colon) in
`docker-compose.yml`'s `ports:` entries and the matching values in
`.env`/`.env.example` (`DATABASE_URL`, `VITE_API_BASE_URL`); nothing else in
the stack depends on these specific numbers.

## Repository layout

```
bazra/
├── backend/                     # FastAPI app — see backend/README.md
│   ├── app/
│   │   ├── api/v1/              # versioned routes
│   │   ├── core/                # BaseModel/SpaceScopedMixin, deps (auth, db session)
│   │   └── modules/auth/        # single-user session-cookie auth
│   ├── migrations/               # Alembic
│   └── conftest.py, tests/
├── frontend/                     # Vite + React + TS app — see frontend/README.md
│   └── src/
│       ├── app/                  # routing, layout
│       └── design-system/        # tokens.css, primitives (Button, Card)
├── infrastructure/docker/        # Postgres init script
├── docs/adr/                     # Architecture Decision Records
├── docker-compose.yml
├── .env.example
└── README.md
```

## Architecture Decision Records

- [0001 — Space scoping via explicit mixin, not a universal base field](docs/adr/0001-space-scoping-convention.md)
- [0002 — Tailwind v4 (CSS-first) instead of the v3 config-file model](docs/adr/0002-tailwind-v4-css-first.md)

Not every implementation choice gets an ADR — only ones that would be
expensive or confusing to rediscover later.
