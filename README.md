# BAZRA (بذرة)

A personal AI operating system and long-term digital companion.

This repository is being built incrementally, phase by phase. See
`docs/adr/` for architecture decisions and their reasoning.

## Status

**Phase 0 — Checkpoint 1: repo skeleton + Docker Compose.**

Only the Postgres service is functional at this checkpoint. The backend and
frontend applications do not exist yet — they arrive in Checkpoint 2 and
Checkpoint 4 respectively. `docker-compose.yml` has their service
definitions commented out with a note for when each is added.

## Prerequisites

- Docker and Docker Compose
- (From Checkpoint 2 onward: Python 3.12+, Node.js 20+)

## Setup (current checkpoint)

```bash
cp .env.example .env
docker compose up -d postgres
docker compose ps            # postgres should report "healthy"
docker compose logs postgres # confirm it started without errors
```

To stop:

```bash
docker compose down          # keeps the pgdata volume
docker compose down -v       # also removes the volume (full reset)
```

## Repository layout

```
bazra/
├── backend/                  # FastAPI app (Checkpoint 2+)
├── frontend/                 # React + Vite app (Checkpoint 4+)
├── infrastructure/docker/    # Postgres init script, etc.
├── docs/adr/                 # Architecture Decision Records
├── docker-compose.yml
├── .env.example
└── README.md
```

## Architecture Decision Records

Significant, hard-to-reverse decisions are recorded in `docs/adr/`. Not
every implementation choice gets an ADR — only ones that would be
expensive or confusing to rediscover later.
