# frontend/

Vite + React + TypeScript app shell with design tokens (Checkpoints 4–5). The
real sidebar/nav layout and further primitives (Tabs, Dialog, Table, ...)
arrive in Phase 1.

## Structure

- `src/main.tsx` — entry point, mounts `<App />`; imports `design-system/tokens.css`
  before `index.css`
- `src/app/App.tsx` — `BrowserRouter` with a single `/` route
- `src/app/Layout.tsx` — placeholder sidebar stub + content area, rendering a
  `Card`/`Button` to prove the token pipeline end to end
- `src/design-system/tokens.css` — design tokens (see ADR 0002 for why this is
  Tailwind v4 CSS-first rather than `tailwind.config.ts`); the comment at the
  top of the file explains the two token-consumption patterns components use
- `src/design-system/components/` — primitives (`Button`, `Card` so far)
- `src/index.css` — minimal reset, applies the default bg/text tokens
- `src/app/App.test.tsx`, `src/design-system/components/*.test.tsx` — smoke
  tests

## Run locally (without Docker)

```bash
cd frontend
npm install
npm run dev
npm test
npm run build
```

## Run via Docker Compose (from repo root)

```bash
docker compose up -d backend frontend
open http://localhost:5174
```

Host port is 5174 (not Vite's default 5173) to avoid clashing with other local
dev servers.

### Troubleshooting: dependency changes not showing up in the container

`docker-compose.yml` mounts `./frontend:/app` for hot reload, plus an
anonymous volume at `/app/node_modules` so the host bind mount doesn't shadow
the container's own installed packages. That anonymous volume **persists
across `docker compose up --build`** — Compose reuses it rather than
recreating it, so after adding or upgrading a frontend dependency, a plain
rebuild silently keeps serving the *old* `node_modules` and the dev server
fails with `Cannot find package '<new-dep>'`.

Fix: drop the container and its volumes before recreating, not just rebuild:

```bash
docker compose rm -f -v frontend
docker compose up -d frontend
```
