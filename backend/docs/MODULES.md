# Adding a module (bounded context)

Each feature lives under `backend/src/app/modules/<name>/` in a four-layer
hexagonal layout, with dependencies pointing **inward only** (`domain` imports
nothing from the other layers).

```
modules/<name>/
  domain/          # entities.py (aggregates + invariants), ports.py (Protocols), errors.py
  application/     # services.py (orchestration), dtos.py (Command/Query dataclasses)
  infrastructure/  # models.py (SQLAlchemy rows), repositories.py (adapters implementing ports)
  interface/       # router.py (FastAPI + RBAC), schemas.py (Request/Response/Envelope)
```

Reference implementations already in the repo:
- **`modules/tasks`** — the full template: domain entity with a state machine, full
  CRUD, RBAC (owner vs. tenant-admin), pagination. Copy patterns from here.
- **`modules/tenants`** — the thin variant: one-time provisioning orchestration with
  no `domain/` layer and no repository (raw `text()`). Not every module needs all four.

## Scaffold a new module

```bash
make new-module NAME=invoices            # entity class inferred: Invoice
make new-module NAME=invoices ENTITY=Bill # or set it explicitly
```

This generates the four layers with a minimal, ruff/mypy-strict-clean
**create + get + list** slice you then extend. (It mirrors `tasks`; the two
`UP046` generic-class lint notes it carries are the same ones the reference module
has and are not gated.)

## Wire it up (printed by the scaffolder)

1. **Register the router** in `backend/src/app/api/v1/__init__.py`:
   ```python
   from app.modules.invoices.interface.router import router as invoices_router
   router.include_router(invoices_router)
   ```
2. **Add a migration.** New tables are **tenant-scoped** (the generated model sets
   `__table_args__ = ({"info": {"tenant_scope": "tenant"}},)`), so they belong to the
   **tenant** Alembic head, not public:
   ```bash
   make revision m="add invoices table"            # autogenerate; REVIEW the output
   make -C backend migrate-tenant SCHEMA=tenant_demo  # apply to one tenant schema
   ```
   For a **global** (cross-tenant) table instead, set `tenant_scope: "public"` and use
   `backend/Makefile`'s `revision-public` / `make migrate`.
3. **Gates:** `make check` (lint + mypy + tests). Add tests under
   `backend/tests/modules/<name>/` — see `tests/modules/tasks` for the layering
   (pure domain tests, service tests with a fake repo, endpoint tests).

## Conventions to keep

- **Repositories `flush()`, never `commit()`** — the `SessionDep` owns the transaction.
- **Never trust `tenant_id` from input** — it comes from the verified token via `PrincipalDep`.
- **Raise `DomainError` subclasses** (with `code` + `http_status`); the central handler
  renders the `{error, meta}` envelope. No HTTP status in domain/service code.
- **`owner_id` comes from `principal.subject`** (see `_owner_id` in the generated router).
