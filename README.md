# SaaS Backend

Multi-tenant SaaS backend. FastAPI (async) + Postgres + Keycloak + Redis.

## Tenancy

Isolated schema-per-tenant inside a single Postgres database:

- `public` schema — global registry (`tenants` table) and system tables.
- `tenant_<slug>` schema — one per customer, holds all tenant-scoped tables.

Tenant is resolved per request from the JWT `tenant_id` claim. The session
dependency sets `search_path` for the duration of the request.

## Layout

```
backend/
├── pyproject.toml
├── docker-compose.yml
├── docker/Dockerfile
├── alembic/                 # two heads: public + tenant
├── src/app/
│   ├── main.py              # app factory + lifespan
│   ├── config.py            # pydantic-settings
│   ├── core/                # db, tenancy, security, logging, deps
│   ├── api/v1/              # versioned HTTP routes
│   └── modules/             # bounded contexts
└── tests/
```

## Local dev

```bash
cp .env.example .env
docker compose up -d
make migrate
make test
```

Then open http://localhost:8000/docs.

## Make targets

| target      | purpose                                  |
|-------------|------------------------------------------|
| `make up`   | start compose stack                      |
| `make down` | stop compose stack                       |
| `make migrate` | run public + tenant-template migrations |
| `make test` | run pytest                               |
| `make fmt`  | ruff format + import sort                |
| `make lint` | ruff check + mypy                        |
