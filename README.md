# Multi-tenant SaaS starter template

An internal Beeline starter template for building multi-tenant SaaS products.
Clone it, rebrand it (one script), replace the example domain, and you have a
production-shaped backend + SPA with auth, tenant isolation, and the quality
gates already wired.

**Stack:** FastAPI (async) · Postgres · Keycloak · Redis · React 18 + Vite +
TanStack Query + shadcn/ui. Runs on Docker locally; designed to graduate to
Kubernetes.

## Why this template (the parts you don't want to rebuild)

- **Backend-for-Frontend (BFF) OIDC auth.** The browser holds only an opaque,
  HttpOnly session cookie — never a token. The backend is a confidential OIDC
  client and keeps access/refresh/id tokens server-side in Redis, **AES-256-GCM
  encrypted at rest**. Auto-refresh, JWKS caching, RP-initiated logout.
- **Tenant isolation by Postgres schema-per-tenant.** Tenant comes from the
  verified JWT claim (never from input) and pins a transaction-scoped
  `search_path` — data can't bleed across tenants.
- **Self-service signup + admin invites.** A company self-registers at
  `POST /v1/signup`; its admin invites employees. Rate-limited, brute-force- and
  password-policy-hardened.
- **Hexagonal modules.** Each feature is a clean domain/application/
  infrastructure/interface slice. `make new-module` scaffolds one.
- **Quality gates + safe-by-default prod.** ruff, mypy --strict, pytest (with
  testcontainers), `validate_for_production()` that refuses to boot prod with dev
  secrets, dev/prod compose split behind an edge nginx.

## Quick start

```bash
make env            # scaffold ./.env + a fresh encryption key
make up             # build & start the full stack (postgres, keycloak, redis, app, frontend, nginx)
make migrate        # apply public-schema migrations
make seed-demo      # seed the demo tenant so the seed users can resolve a tenant
# open http://localhost:3000 and log in as demo (admin) or one of the domain users —
# requester, ba, business_owner, executor, approver (password for all: demo-password-123)
```

Full walkthrough (including prerequisites and troubleshooting):
**[docs/QUICKSTART.md](docs/QUICKSTART.md)**.

## Using it for a new product

1. **Rebrand:** `scripts/init-template.sh --name <slug> --display "<Name>"` — see
   **[docs/CLONING.md](docs/CLONING.md)**.
2. **Build your domain:** replace the example `tasks` module — see
   **[docs/MODULES.md](docs/MODULES.md)** / `make new-module NAME=<name>`.
3. **Deep architecture & invariants:** **[CLAUDE.md](CLAUDE.md)** (read this — it
   documents the auth flow, the two Keycloak issuers, the two Alembic heads, and
   the footguns).

## Commands

`make help` lists everything. The common ones: `make up` / `down` / `logs`,
`make check` (lint + typecheck + test), `make fe-dev`, `make migrate`,
`make new-module NAME=…`, and the `prod-*` variants (`make up ENV=prod`).

## Ownership

Internal template — maintained by the Platform team. File issues / changes
through the team's normal process; keep `CLAUDE.md` and these docs in sync when
you change the architecture.
# business_analyst_platform
