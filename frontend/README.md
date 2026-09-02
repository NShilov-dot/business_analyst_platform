# BA Platform — Frontend

React SPA for the AI Business Analyst system (BuildX / Beeline Uzbekistan).

Part of a three-repo setup: **frontend** (this) · [backend](https://gitlab.beeline.uz/ai-ba/backend) · [configs](https://gitlab.beeline.uz/ai-ba/configs) (the docker-compose stack + product docs).

## Quick start

```bash
npm ci
npm run dev          # http://localhost:5173
```

The dev server proxies `/v1` to `http://localhost:8000`, so you need the backend
running. From the **configs** repo: `make up` (full stack in Docker) or
`make dev-backend` (uvicorn on the host).

## Commands

| Command | What |
|---|---|
| `npm run dev` | Vite dev server on :5173 with HMR |
| `npm run check` | `tsc --noEmit` — the type gate |
| `npm run build` | `tsc && vite build` → `dist/` |
| `npm run preview` | serve the built bundle locally |

## Running the built image

```bash
BACKEND_UPSTREAM=https://ba-dev.example.com docker compose up -d --build
# → http://localhost:3000
```

`BACKEND_UPSTREAM` is where nginx proxies `/v1`. The full local stack doesn't use
this file — the configs repo builds this image itself and wires it to the `app`
service.

## Stack

React 18 · Vite 5 · TypeScript · TanStack Query · React Router v6 · Tailwind ·
Radix + cva (shadcn pattern) · react-hook-form + zod.

See [CLAUDE.md](CLAUDE.md) for architecture — in particular the BFF auth model
(the browser never holds a token) and the `/v1`-without-rewrite invariant.
