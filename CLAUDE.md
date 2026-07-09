# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**The product being built:** an **AI Business Analyst** system for the private enterprise AI platform **BuildX** (Beeline Uzbekistan) — structured intake and end-to-end tracking of business requests between Product Owner, business managers, and development. It turns vague verbal requests into structured, traceable, measurable artifacts: intake templates → managed workflow → ticket tracking → «было/стало» (before/after) capture → ТЗ (spec) → double acceptance → end-to-end analytics. Stage: **Pre-MVP / Discovery**, 12-month horizon. **Domain source of truth: `AI_Business_Analyst_System.md`** (Russian; it links the full PRD, phase plan, and template analysis). The "Product domain" section below is its condensed operational summary — when they disagree, the brief wins. **Module decomposition: `docs/PRODUCT_MODULES.md`** — the agreed module map, phasing, and build order.

**The codebase today:** the Beeline multi-tenant BA Platform starter the product is built on — a monolith with **FastAPI (async) + Postgres + Keycloak + Redis** backend, **React 18 + Vite + TanStack Query + React Router v6 + Tailwind** frontend. Auth is a **Backend-for-Frontend (BFF) OIDC bridge** — the browser never sees a token; the backend is a confidential OIDC client and holds all tokens server-side. Tenants are isolated by **Postgres schema-per-tenant**. Designed to run on Docker locally and graduate to Kubernetes. `modules/tasks` is the starter's example domain — the reference pattern for the product's real modules, expected to be replaced as they land.

## Product domain (condensed from `AI_Business_Analyst_System.md`)

### Domain core: the ticket + unified metadata
The central aggregate is the **ticket** (заявка) — every business request becomes one. All requirement artifacts (BRD / SRS / Use Case / Change Spec) share a **unified metadata core**: `ID`, title, type, author, date, version, artifact status (Черновик → На согласовании → Утверждён → В работе → Закрыт), priority (value·urgency·risk), source/requester, **trace links**. Note there are two distinct status enums: the 7-state *ticket workflow* below vs the 5-state *artifact lifecycle* here — don't merge them.

Everything built must support **end-to-end analytics** and **«было/стало» (before/after) capture** — those are the product's reason to exist.

### Traceability chain (drives the entity model)
`BG → BR → (UC / US) → FR / NFR → AC → TC` — every lower level links to its parent, so anything traces up to a business goal.

| Prefix | Meaning | Documented in |
|---|---|---|
| `BG-XX` | Business goal / KPI | BRD |
| `BR-XX` | Business requirement | BRD |
| `RULE-XX` | Business rule (domain policy) | SRS / Use Case |
| `UC-XX` / `US-XX` | Use case / user story | Use Case / SRS |
| `FR-XX` / `NFR-XX` | Functional / non-functional requirement | SRS / Change Spec |
| `AC-XX` | Acceptance criterion | all artifact types |
| `TC-XX` | Test case | SRS / test plan |

**Deliberately fixed collision — don't reintroduce it:** `BR` means business *requirement* only; business *rules* use `RULE`. (Legacy templates conflated the two.)

### Ticket workflow
```
Создан → Триаж → ТЗ/Согласование → В работе → Фиксация изменений → Приёмка → Закрыт
                                                                          ↘ Отклонён
```
(created → triage → spec & approval → in progress → before/after capture → acceptance → closed / rejected)

Hard rules of the state machine:
- A ticket enters **«В работе» only after the ТЗ is approved**.
- **Closing requires the double acceptance gate:** (1) *formal DoD — decided by the BA*: spec ACs met, «было/стало» recorded, protocol filed, no open P0/P1; (2) *value — decided by the business requester*: the result actually solves the original need.
- **Отклонён** (rejected): duplicate / irrelevant / unjustified — decided by BA/triage.

### Roles & access (open access + mandatory triage)
Any employee can file a ticket; nothing reaches work without triage.

| Role | Responsibility |
|---|---|
| **Заявитель** (requester — any employee) | files tickets (template or free form), tracks status |
| **Триаж / BA** (business analyst) | completeness check, dedupe, routing, writes the ТЗ, drives the workflow, formal acceptance |
| **Бизнес-заказчик** (business owner) | approves requirements, confirms value at acceptance |
| **Исполнитель** (development) | accepts the spec, estimates, executes, records «было/стало» |
| **Согласующий** (division head) | prioritization, sign-off on M+ initiatives |

### Intake templates (hybrid: catalog + free form)
Typed forms for frequent cases + free form for the rest, but **every** form requires: problem/need, expected result + measurable effect, affected systems/stakeholders, urgency/deadline, acceptance criteria. Also mandatory and measurable everywhere: business goal (link to a `BG`), a **numeric** success metric, AS-IS / TO-BE.

Template routing (the logic a triage feature implements): new idea needing a business case → **BRD**; new app / large system / >2 changes → **SRS**; behavior & scenarios → **Use Case**; small change (1–2 edits) or defect → **Change Specification**; integration/data → SRS (integrations) + Change Spec; analytics/report request → Change Spec (output data); atypical → **free form + metadata core**.

BA process context — Track A (new application): Clarification → AS-IS → TO-BE → BRD → decomposition (FR/NFR, UC/US) → SRS → grooming → estimation → prioritization → DoD → handoff. Track B (change to existing): quick clarification → Impact Analysis → decomposition (Epic → Feature → Story) → Change Spec → JIT detail (AC, edge cases) → grooming.

### Compliance & NFRs — architectural, not bolt-on
- **RBAC** by role; **data-zone separation between departments**.
- **Personal-data localization in Uzbekistan**; full **audit logs** of every ticket action; complete history of changes and approvals.
- **MCP-style integrations** (Confluence, tracker, data sources).
- **Human control on legally significant decisions** — never AI-only.

### Roadmap (what to scope for)
Phase 0 Discovery (0–2 mo: interviews, platform choice) → **Phase 1 Pre-MVP (2–4 mo: templates, status model, triage, 1 department)** → Phase 2 MVP (4–8 mo: ТЗ + «было/стало» + double gate + analytics, 2–3 departments) → Phase 3 Scale (8–12 mo: more departments, AI structuring of free-form intake). MVP success metrics live in the brief §2 (e.g. ≥80% of requests via template, schedule deviation ≤20%, 100% ticket coverage).

### Open decisions — keep the code flexible
- **The tracking platform is undecided** (Jira + customization vs an own module on this codebase). Keep tracker-specific logic behind ports/adapters (`domain/ports.py`); do not hard-couple workflow logic to any tracker.
- **How the product maps onto the starter's multi-tenancy** (e.g. department = tenant?) is not decided; departments do need separated data zones. Don't hardcode either assumption.

## Commands

There are **two Makefiles**, and which one you want depends on the task:

- **Root `Makefile`** — the primary developer task runner. Quality gates run against a **local venv at `backend/.venv`** (fast, no container). Run `make help`.
- **`backend/Makefile`** — runs everything **inside the `app` container** (`docker compose exec app …`) and owns the per-tenant migration / provisioning targets. Invoke as `make -C backend <target>`.

### First-time setup
```bash
make env          # scaffold ./.env from backend/.env.example + a fresh SESSION_ENCRYPTION_KEYS
make up           # build & start full stack: postgres, keycloak(+db), redis, app, frontend
make migrate      # apply PUBLIC-head migrations (docker compose exec app alembic -x scope=public upgrade public@head)
make seed-demo    # seed the demo tenant (matches the realm-export seed users) so /v1/tasks works
# backend/.venv must exist for the quality gates below — deps are in backend/pyproject.toml (managed with uv)
```

### Quality gates (local venv — these are the canonical check commands)
```bash
make check        # = lint + typecheck + test
make lint         # ruff check src tests
make typecheck    # mypy --strict src
make fmt          # ruff --fix + ruff format
make test         # pytest (cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests -q)
```
Run a **single test** (note `PYTHONPATH=src` is required — the package lives under `src/`):
```bash
cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/core/test_oidc.py -q
cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests -k "verify_token" -q
```
Tests use `asyncio_mode=auto` (no `@pytest.mark.asyncio` needed). Integration tests spin up Postgres via `testcontainers`, so Docker must be running.

### Frontend
```bash
make fe-dev       # Vite dev server on :5173, proxies /v1 -> http://localhost:8000
make fe-build     # tsc && vite build
make dev-backend  # run uvicorn locally (loads ./.env, talks to docker-exposed ports); pair with fe-dev
```

### Scaffolding a domain module
```bash
make new-module NAME=tickets ENTITY=Ticket    # scaffold all four hexagonal layers (see docs/MODULES.md)
```

### Migrations & tenant provisioning (two Alembic heads — see architecture below)
```bash
make migrate                                          # public head -> public schema
make -C backend migrate-tenant SCHEMA=tenant_acme     # tenant head -> one tenant schema
make -C backend provision-tenant SLUG=acme NAME="ACME Corp"   # create tenant row + KC group + schema
make revision m="message"                             # autogenerate (root); or backend/Makefile revision-public / revision-tenant SCHEMA=...
```

## Architecture

### Auth: BFF OIDC bridge (the core of this codebase)
The browser holds **only an opaque, HttpOnly session-id cookie** (`bap_session`, or `__Host-bap_session` over HTTPS). Access/refresh/id tokens live **server-side in a Redis session, AES-256-GCM encrypted at rest** (`core/crypto.py`, `core/sessions.py`). Flow:

1. `GET /v1/auth/login` (`api/v1/auth.py`) — generates `state` + PKCE (S256), stashes a `LoginState` in Redis (`login:{state}`, 5-min TTL), sets a path-scoped (`/v1/auth/callback`) HttpOnly `oidc_state` cookie, redirects to Keycloak using the **public issuer**.
2. `GET /v1/auth/callback` — constant-time-compares the `state` cookie vs query param, one-shot-pops the Redis login state, exchanges the code for tokens over the **internal issuer**, verifies the access token locally, creates the encrypted Redis session, sets the session cookie.
3. Authenticated requests — `core/deps.py` `_principal()` reads the session cookie → loads & decrypts the Redis session → **auto-refreshes the access token if within 30s of expiry** → `verify_token()` (`core/security.py`) validates signature against cached JWKS and asserts `typ` is `Bearer` → slides the idle TTL → returns a `Principal(subject, tenant_id, roles)`.
4. `POST /v1/auth/logout` — best-effort backchannel refresh-token revoke, deletes the Redis session, returns a Keycloak RP-Initiated Logout URL for the SPA to visit.

**Two Keycloak issuers** are intentional and a frequent source of confusion: `KEYCLOAK_ISSUER` (internal/backchannel, e.g. `http://keycloak:8080/...`, used for token exchange + JWKS) vs `KEYCLOAK_PUBLIC_ISSUER` (browser-facing, e.g. `http://localhost:8080/...`, used for redirects and as the token `iss` claim the backend validates against).

### Multi-tenancy: schema-per-tenant
- `public` schema holds the global `tenants` registry; each customer gets a `tenant_<slug>` schema (`tenant_schema()` in `core/tenancy.py`). Slug regex `^[a-z][a-z0-9_]{1,40}$` is enforced at four layers (Pydantic, SQLAlchemy CHECK, Python, Alembic) because **schema names cannot be SQL-parameterized** and are string-interpolated.
- **Tenant is resolved from the verified JWT `tenant_id` claim — never from request bodies/params.** The dependency chain in `core/deps.py` is `PrincipalDep → TenantDep (resolve_tenant) → SessionDep`. `session_for_tenant()` issues `SET LOCAL search_path TO "tenant_<slug>", public`, so the change is **transaction-scoped** and cannot bleed across pooled connections. A handler that depends on `SessionDep` gets a session already locked to the caller's tenant.
- Provisioning (`modules/tenants/application/services.py`): insert `public.tenants` row → create Keycloak group carrying `tenant_id` as an attribute → `run_tenant_migrations()` (shells out to `alembic -x scope=tenant -x schema=tenant_<slug> upgrade tenant@head`) → create the first `tenant_admin` user. This is **not atomic across subsystems** — a partial failure can leave a registry row without a schema; retry/cleanup manually.

### Alembic: two independent heads
One Alembic tree, two branches selected by `-x scope=…` (`alembic/env.py`):
- **`public@head`** (`branch_labels=("public",)`) → applied once to `public`, tracked in `alembic_version_public`.
- **`tenant@head`** (`branch_labels=("tenant",)`) → a replayable **template** applied per tenant schema, tracked in `alembic_version_tenant` (inside each tenant schema). Tenant migrations carry **no `schema=` clause** — they rely on the `search_path` set by `env.py`.
- Tables route to a branch via `info={"tenant_scope": "public"|"tenant"}` in `__table_args__`; `_include_object()` filters autogenerate accordingly.
- **Invariant:** `env.py` must `await connection.commit()` after the migration loop — async SQLAlchemy 2.0 (commit-as-you-go) otherwise rolls back the DDL on connection close.

### Per-module hexagonal layout
Each bounded context under `src/app/modules/<name>/` follows four layers with dependencies pointing **inward only** (`domain` imports nothing from the others):
- `domain/` — `entities.py` (aggregates with state-transition methods + invariants), `ports.py` (`typing.Protocol` repository contracts), `errors.py` (subclasses of `core/errors.DomainError`, each with `code` + `http_status`). **No framework imports.**
- `application/` — `services.py` (orchestration; depends on ports, takes an injected clock for testability), `dtos.py` (plain `*Command`/`*Query` dataclasses; nullable updates use `*_set` flags to distinguish "leave alone" from "set null").
- `infrastructure/` — `models.py` (SQLAlchemy rows + DB CHECK constraints), `repositories.py` (adapters implementing the ports; call `flush()`, **never `commit()`** — the `SessionDep` owns the transaction boundary).
- `interface/` — `router.py` (FastAPI, wires `service → repo` via `Depends`, RBAC via `require_roles(...)`), `schemas.py` (Pydantic `*Request.to_command()` / `*Response.from_entity()`, `Envelope[T]`/`PagedEnvelope[T]`).

`modules/tasks/` is the **full reference template** (domain + full CRUD). `modules/tenants/` is the **thin variant**: one-time provisioning orchestration with no `domain/` layer and no repository (raw `text()` against the public schema) — a reminder that not every module needs all four layers. The product's bounded contexts (`tickets`, `intake_templates`, `departments`, `audit`, `requirements`, `acceptance`, `analytics`, `integrations`, `ai_structuring` — see `docs/PRODUCT_MODULES.md` for responsibilities, phasing, and build order) go under `modules/` in this same layout; scaffold with `make new-module`.

### Domain events & audit
`core/events.py` — synchronous in-process event dispatcher, no broker. Services receive a **required** `EventPublisher`; the publisher is bound per-request (session + request_id + actor) via `EventPublisherDep`. Subscribers run in the same request and the **same `AsyncSession`**, so side-effect writes are atomic under `SessionDep` (flush, never commit) and handler errors propagate (fail-closed). **Publishing an event IS the act of auditing**: the only writer to `audit_entries` is the subscriber in `modules/audit/infrastructure/subscriber.py` — never write audit rows directly. The audit table is INSERT-only (migration REVOKEs UPDATE/DELETE/TRUNCATE from the app role; owner can technically bypass — real enforcement is provisioning-level grants, Phase-2 hardening).

### Errors & responses
Raise `DomainError` subclasses from domain/service code (never put HTTP status in messages). `core/error_handlers.py` converts them to `{"error": {code, message, details}, "meta": {requestId}}` with an `x-request-id` header. Success responses use the `Envelope`/`PagedEnvelope` wrappers.

### Frontend ↔ BFF
Same-origin by design: both the Vite dev proxy (`vite.config.ts`) and production nginx (`frontend/nginx.conf`) forward `/v1/*` to the backend **without rewriting the path** — keep it that way or you break the path-scoped `oidc_state` cookie. `api/client.ts` sends every request with `credentials: 'include'` and bounces to `/v1/auth/login` on 401 (`AuthProvider.tsx` has a sessionStorage-based loop breaker: max 3 redirects / 10s). `auth/access.ts` (`canManageTask`, `isOwnTask`) is **UX gating only — the backend is the security boundary** and must independently authorize every endpoint.

## Critical invariants & footguns
- **Never trust `tenant_id` from input.** It comes solely from the verified token claim via `PrincipalDep`.
- **Repositories `flush()`, the request dependency commits.** Don't `commit()` inside a repo or service.
- **`SET LOCAL search_path` must stay transaction-scoped.** Don't drop `LOCAL` or set search_path on a session you reuse.
- **Two issuers / same-origin `/v1` proxy** — both must hold or auth breaks in subtle ways (see Auth section).
- **Session encryption keys** (`SESSION_ENCRYPTION_KEYS`, base64 AES-256) are optional locally (plaintext fallback) but **mandatory in prod**; rotate by prepending a new key (decrypt tries all, newest-first). `make gen-key` prints one.
- **Rate limiting** (`core/rate_limit.py`) is **fail-closed for unauthenticated auth endpoints** (Redis down ⇒ block) and **fail-open for authenticated traffic**. Client IP comes from `X-Real-IP` set by the reverse proxy — the backend must not be directly internet-reachable.
- **`config.py` `validate_for_production()`** enforces HTTPS URLs, real secrets, Redis auth, encryption keys, CORS + trusted hosts when `app_env=prod`; it runs at startup (`main.py` lifespan). Adjust it when adding security-relevant settings.
- **Domain-level gates are invariants too:** work starts only after an approved ТЗ; a ticket closes only through the double acceptance gate; every requirement entity links to its parent in the traceability chain (see "Product domain").

## Keycloak (`backend/keycloak/realm-export.json`)
Realm `bap`, auto-imported on `start-dev`. Roles: `platform_admin` (cross-tenant), `tenant_admin`, `tenant_user`, plus the product's domain roles `ba`, `business_owner`, `executor`, `approver` (Заявитель has **no dedicated role** by design — any `tenant_user` can file a ticket; `business_owner`/`executor`/`approver` are declared but not yet gate-checked in code — per-ticket authority flows through assignments by subject). Clients: `bap-backend` (confidential BFF, PKCE/S256, with a `tenant_id` protocol mapper) and `bap-backend-admin` (service account with `realm-admin` for provisioning; gated by `KEYCLOAK_ADMIN_CLIENT_SECRET` — admin features are disabled if unset). Seed users (shared dev password `demo-password-123`, all in the demo tenant): `demo` (admin) + one per domain role — `requester`, `ba`, `business_owner`, `executor`, `approver`; `backend/scripts/remove_demo_user.py` strips them all before non-dev deploys. Two import footguns: (1) `--import-realm` skips import if the realm already exists — after editing the export, wipe the `keycloak-db` volume to re-import; (2) the realm `passwordPolicy` (`length(12) and notUsername and notEmail`) IS enforced on imported plaintext credentials — a too-short seed password makes Keycloak crashloop at startup (`invalidPasswordMinLengthMessage`).
