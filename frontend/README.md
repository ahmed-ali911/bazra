# frontend/

Vite + React + TypeScript app shell (Checkpoint 4). Design tokens and the real
design system arrive in **Checkpoint 5** — for now this is a plain placeholder
shell proving routing and rendering work end to end.

## Structure

- `src/main.tsx` — entry point, mounts `<App />`
- `src/app/App.tsx` — `BrowserRouter` with a single `/` route
- `src/app/Layout.tsx` — placeholder sidebar stub + content area
- `src/index.css` — minimal reset, no design tokens yet
- `src/app/App.test.tsx` — smoke test: app shell renders without crashing

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
