# CLAUDE.md — frontend

Guidance for Claude Code (claude.ai/code) working in this repository.

## What this is

The React SPA for the **AI Business Analyst** system (BuildX / Beeline Uzbekistan) —
structured intake and end-to-end tracking of business requests: intake templates →
managed workflow → ticket tracking → «было/стало» capture → ТЗ → double acceptance →
analytics. Stage: Pre-MVP / Discovery.

**This is one of three repositories:**

| Repo | Contains |
|---|---|
| `frontend` (this one) | React 18 + Vite SPA, its Dockerfile and nginx SPA server |
| `backend` | FastAPI + Postgres + Keycloak + Redis, the BFF, all domain modules |
| `configs` | docker-compose stack, edge nginx, `.env.example`, product docs & domain brief |

The full local stack lives in **configs** — it builds this repo's image itself.
The product domain (ticket workflow, traceability chain, roles, acceptance gates) is
documented there; check it before making product decisions from UI code alone.

## Stack

React 18 · Vite 5 · TypeScript · TanStack Query · React Router v6 · Tailwind ·
Radix + cva + tailwind-merge (shadcn pattern) · react-hook-form + zod ·
lucide-react · sonner · next-themes. Design snapshot: `.hallmark/preflight.json`.

```
src/
  api/         one module per backend resource — hand-written fetch wrappers
  auth/        AuthProvider.tsx (session bootstrap + 401 handling), access.ts
  components/  ui/ (shadcn primitives), layout/
  features/    feature-local components (tickets/)
  pages/       route components
  types/       api.ts — hand-written response types
  lib/         utils
```

## Commands

```bash
npm ci            # install
npm run dev       # Vite dev server on :5173, proxies /v1 -> http://localhost:8000
npm run check     # tsc --noEmit — the type gate
npm run build     # tsc && vite build
```

For day-to-day work: `npm run dev` + the backend running (from **configs**:
`make up`, or `make dev-backend` for uvicorn on the host).

`docker-compose.yml` here builds and serves the production image against an
arbitrary `BACKEND_UPSTREAM` — useful for checking the built bundle, not for
day-to-day UI work.

## Architecture: talking to the BFF

**The browser never sees a token.** Auth is a Backend-for-Frontend OIDC bridge: the
backend is a confidential OIDC client holding all tokens server-side, and the browser
holds only an opaque HttpOnly session cookie. The SPA therefore has *no* Keycloak
config, no token storage, and no `Authorization` header.

- `api/client.ts` sends every request with `credentials: 'include'` and bounces to
  `/v1/auth/login` on 401. `AuthProvider.tsx` has a sessionStorage-based loop breaker
  (max 3 redirects / 10s) — keep it when touching the 401 path.
- `auth/access.ts` (`canManageTask`, `isOwnTask`) is **UX gating only.** The backend is
  the security boundary and authorizes every endpoint independently. Never treat a
  hidden button as an access control.

### Invariant: `/v1` is proxied WITHOUT rewriting the path

Same-origin by design, in all three places that proxy this app:

| Where | File |
|---|---|
| dev server | `vite.config.ts` (`/v1` → `http://localhost:8000`) |
| this repo's SPA image | `nginx.conf.template` (`/v1/` → `${BACKEND_UPSTREAM}`) |
| the stack's edge proxy | `configs/nginx/nginx.{dev,prod}.conf` |

Rewriting or re-prefixing the path breaks the backend's **path-scoped `oidc_state`
cookie** on `/v1/auth/callback`, and login fails in a way that looks like a Keycloak
problem. Note `proxy_pass` has no trailing slash on purpose.

Two more values are duplicated across those same nginx configs and must match the
backend's settings: `client_max_body_size 12m` (backend `MAX_BODY_SIZE_BYTES`) and
`proxy_read_timeout 240s` (transcription cold start).

### `nginx.conf.template` also carries the SPA's security headers

CSP (`script-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`),
`X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, and
`Permissions-Policy: camera=(), microphone=(self), geolocation=()` — the microphone
grant is what the voice-intake feature needs. This is the **only** place they are set
for the document that loads the app JS; the edge proxy in configs does not set them.
Don't drop them when editing that file.

`BACKEND_UPSTREAM` is substituted at container start by the nginx image's envsubst
entrypoint; `NGINX_ENVSUBST_FILTER` pins substitution to that one variable so nginx's
own `$host` / `$scheme` / `$uri` survive.

## The API contract is hand-written

`src/types/api.ts` and the eleven modules in `src/api/` mirror the backend's Pydantic
schemas **by hand** — there is no OpenAPI codegen, and nothing checks the two repos
against each other. When a backend response shape changes, it changes here too, in the
same pass. Backend responses are wrapped: `Envelope<T>` / `PagedEnvelope<T>`; errors
come back as `{"error": {code, message, details}, "meta": {requestId}}`.
