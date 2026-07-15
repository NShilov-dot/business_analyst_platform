# Quickstart

From clone to a running, logged-in app in ~10 minutes.

## Prerequisites

- **Docker** (with Compose v2) running.
- **Python 3.12** + [`uv`](https://docs.astral.sh/uv/) for the local quality gates.
- **Node 20+** for frontend dev.
- Free local ports: `3000` (edge), `8080` (Keycloak), and the loopback-bound
  `5433`/`6380`/`8001` (postgres/redis/app). Change them in `docker-compose.dev.yml`
  if they collide.

## 1. Bring up the stack

```bash
make env            # creates ./.env from backend/.env.example + a fresh SESSION_ENCRYPTION_KEYS
make up             # builds & starts: postgres, keycloak(+db), redis, app, frontend, edge nginx
make migrate        # applies the PUBLIC-schema migrations (tenant registry)
make seed-demo      # seeds the demo tenant so the realm's seed users can resolve a tenant
```

Keycloak takes ~20–40s on first boot to import the realm. Then open
**http://localhost:3000** and log in as **`demo`** (admin), or as one of the
AI Business Analyst domain users. Password for every seed user:
**`demo-password-123`** (the realm's `passwordPolicy` — `length(12) and
notUsername` — is enforced even on `--import-realm` plaintext credentials, so
short per-user passwords would crashloop Keycloak at import). All are in the
demo tenant:

| Login | Кто (роль домена) | Realm-роли |
|---|---|---|
| `demo` | Админ платформы/тенанта | `platform_admin`, `tenant_admin`, `tenant_user` |
| `requester` | Заявитель (Aziz Karimov) | `tenant_user` — заявителю отдельная роль не нужна |
| `ba` | Триаж / бизнес-аналитик (Nilufar Saidova) | `tenant_user`, `ba` |
| `business_owner` | Бизнес-заказчик (Timur Rashidov) | `tenant_user`, `business_owner` |
| `executor` | Исполнитель (Sherzod Umarov) | `tenant_user`, `executor` |
| `approver` | Согласующий (Gulnora Alimova) | `tenant_user`, `approver` |

> Editing `backend/keycloak/realm-export.json` later? `--import-realm` only runs
> when the realm does **not** exist yet — to re-import, wipe the Keycloak volume
> (`docker compose stop keycloak keycloak-db && docker compose rm -f keycloak keycloak-db`
> `&& docker volume rm bap_keycloak-db-data`), then `make up` and restart `app`.
> App Postgres/Redis data survive; everyone has to log in again.

Try the self-service flow too: visit **http://localhost:3000/signup**, register a
company, then log in as that founder and invite a teammate.

## 2. Local development

```bash
make fe-dev         # Vite dev server on :5173 (proxies /v1 -> backend); fast FE iteration
make dev-backend    # run uvicorn locally with reload (talks to the docker-exposed services)
```

> The dockerized `frontend` is a **built** image (nginx serving a production
> bundle), not a live dev server. After changing frontend code in the docker
> stack, rebuild it: `make up` (or `docker compose ... up -d --build frontend`).
> For fast FE iteration use `make fe-dev`.

## 3. Quality gates (the canonical checks)

```bash
make check          # = lint + typecheck + test  (local venv at backend/.venv)
make lint           # ruff
make typecheck      # mypy --strict
make test           # pytest (testcontainers spins up Postgres → Docker must be running)
```

A single test:

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/core/test_oidc.py -q
```

## 4. API docs

With the stack up (non-prod), Swagger UI is at **http://localhost:3000/docs** and
the schema at `/openapi.json`. (Disabled automatically when `APP_ENV=prod`.)

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `make up` can't reach the Docker API | Docker isn't running — start Docker Desktop. |
| `Tenant … not found or inactive` on `/v1/tasks` | The demo tenant isn't seeded after a reset — run `make seed-demo` (all seed users resolve into this tenant). |
| Login redirect goes to `http://keycloak:8080/...` (unreachable in the browser) | `KEYCLOAK_PUBLIC_ISSUER` must be the **browser-facing** URL (`http://localhost:8080/...`), distinct from the internal `KEYCLOAK_ISSUER` (`http://keycloak:8080/...`). See the two-issuer note below. |
| `Invalid username or password` for a valid user | Keycloak brute-force lockout after repeated attempts — wait, or clear via the admin API / Keycloak console. |
| Port conflicts | Edit the loopback port mappings in `docker-compose.dev.yml`, or `make clean` then `make up`. |
| Migration error about `search_path` | Tenant migrations rely on the per-request/`env.py` search_path — don't add a `schema=` clause to tenant tables. |

### The two Keycloak issuers (the #1 gotcha)

- `KEYCLOAK_ISSUER` — **internal/backchannel** (`http://keycloak:8080/realms/<realm>`):
  the backend uses it for token exchange, refresh, revoke, and JWKS.
- `KEYCLOAK_PUBLIC_ISSUER` — **browser-facing** (`http://localhost:8080/realms/<realm>`
  in dev, `https://auth.example.com/realms/<realm>` in prod): used for the login/logout
  redirects and as the token `iss` the backend validates against.

In single-URL dev they can look similar, but a mismatch causes silent token-validation
or unreachable-redirect failures. Set both correctly in `.env.prod.example`.

For the full architecture and invariants, read **[../CLAUDE.md](../CLAUDE.md)**.
