# Audit Module

## Scope

Append-only audit log of every domain action within a tenant. Satisfies
PRODUCT_MODULES §4.4 and §8: "полные аудит-логи", "полная история изменений
и согласований".

## Architecture

### Fail-closed design

Publishing a domain event IS the act of auditing — nothing writes to the
`audit_entries` table directly. The flow is:

```
Domain mutation (service layer)
  → EventBus.publish(event, session)
    → AuditEventSubscriber.__call__(event, session)
      → SqlAlchemyAuditRepository.append(cmd)        ← flush(), never commit()
        → SessionDep commits the whole transaction
```

If the audit write fails (DB constraint, connection issue, etc.), the
exception propagates through the bus back to the request handler, the
SessionDep rolls back, and the mutation is NOT committed. This is
fail-closed: a domain change that cannot be audited must not persist.

### INSERT-only enforcement (defence-in-depth)

Migration `0005_tenant_audit` REVOKEs `UPDATE` and `DELETE` on
`audit_entries` from the `app` database role. Caveats:

- The database owner role can technically bypass these grants.
- Phase 2 hardening: the provisioning saga should also enforce the same
  grants at schema-creation time and audit any owner-level access.

### Tenant-scoped table

`audit_entries` lives in the tenant schema (no `schema=` clause — relies on
`search_path` set by `session_for_tenant()`). This keeps audit data inside
the tenant's data zone for residency compliance (§8, Uzbekistan localisation).

## Open questions

1. **PII vs append-only (PRODUCT_MODULES §8, open item 7):** Retention
   policies and GDPR/Uzbek PII erasure requests conflict with the
   append-only guarantee. Resolution options:
   - Pseudonymise actor fields (replace sub with a one-way hash) — then the
     row contains no direct PII.
   - Legal-hold exception: erasure requests become "suppress, not delete"
     (hide from API but keep the row for compliance).
   - A `RetentionPolicy` entity (explicitly out of scope for Phase 1) that
     governs which columns can be NULL-ed on request.
   **A legal review is required before the schema is frozen.**

2. **Granularity:** Currently one row per domain event. When the tickets
   module emits status-transition events, the `before`/`after` snapshots
   should capture the complete set of changed fields — teams must agree on
   what "complete" means for each entity type.
