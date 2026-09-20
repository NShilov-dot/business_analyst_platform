# departments — thin module, Pre-MVP (Phase 1)

Organizational department registry, membership management, and the Phase-1
seam for data-zone access control.

## Phase 1 scope

- `Department` entity: name (unique per tenant), description, is_active flag.
- `DepartmentMembership`: maps a Keycloak `sub` to a department row (many
  subjects per department; one subject may belong to multiple departments).
- CRUD API at `/api/v1/departments` — list/get open to all authenticated tenant
  members; create/update/member-management restricted to `tenant_admin` or
  `platform_admin`.
- Alembic tenant-branch migration (`0003_tenant_departments`) — tables live
  inside each `tenant_<slug>` schema.
- Default department seed in the root `Makefile` `seed-demo` target.

## What is NOT in Phase 1

- `RoutingRule` — triage routing rules are a Phase 2 feature.
- DataZone access filtering — the `DepartmentResolver` port and its
  `SqlAlchemyDepartmentResolver` adapter exist, but wiring into `core/authz.py`
  and actually filtering ticket/artifact reads is deferred to Phase 2.

## Reserved seam: DataZonePolicy

`application/ports.py` declares the `DepartmentResolver` protocol:

```python
class DepartmentResolver(Protocol):
    async def get_department_ids_for_subject(self, subject: str) -> list[UUID]: ...
```

`infrastructure/repositories.py` provides `SqlAlchemyDepartmentResolver` as
the concrete adapter. Phase 2 will:

1. Add `DataZoneDep` to `core/authz.py` that wraps this adapter.
2. Inject `DataZoneDep` after `PrincipalDep`/`TenantDep` in handlers that need
   department-scoped data access (tickets, requirements, audit).
3. Activate the second department in the demo tenant and validate that cross-
   department data is invisible to members of a single department.

Until then, all authenticated tenant members see all departments and all
tickets (same as today's tasks module).

## Architecture decisions

- Thin variant (no `domain/` layer) because departments are a simple reference
  table without a complex state machine. Entities and commands co-locate in
  `application/dtos.py`.
- `tenant_id` is never read from request input — it comes from the verified
  JWT claim via `PrincipalDep` / `SessionDep` (the `SET LOCAL search_path`
  ensures every query is already scoped to the caller's tenant).
- Repositories `flush()`, never `commit()` — `SessionDep` owns the transaction.
