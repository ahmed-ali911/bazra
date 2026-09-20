-- Runs once, on first container initialization only (Postgres only executes
-- files in /docker-entrypoint-initdb.d on an empty data directory).
--
-- Intentionally minimal for Checkpoint 1: no application schema yet — that
-- arrives via Alembic migrations starting in Checkpoint 2.
--
-- pgvector is enabled here (idempotent) so the extension is available once
-- Memory Engine actually needs it (Phase 4). Not used before then.
--
-- NOTE: requires the pgvector/pgvector:pg16 image (not plain postgres:16),
-- which ships the extension precompiled. See docker-compose.yml.

CREATE EXTENSION IF NOT EXISTS vector;
