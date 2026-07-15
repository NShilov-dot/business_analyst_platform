"""Create a tenant's Postgres schema by running the Alembic `tenant` head.

This shells out to Alembic (the same mechanism scripts/provision_tenant.py uses)
so the per-tenant schema is versioned in `alembic_version_tenant`. It runs in the
app container's working directory, where alembic.ini lives.

Tenant onboarding is infrequent; awaiting a short subprocess in the request is
acceptable. For very high volume this would move to a background job.
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.get_logger(__name__)

# Bound the migration so a hung/slow Alembic run can't block the request forever.
_MIGRATION_TIMEOUT_SECONDS = 120


async def run_tenant_migrations(slug: str) -> None:
    schema = f"tenant_{slug}"
    proc = await asyncio.create_subprocess_exec(
        "alembic",
        "-x",
        "scope=tenant",
        "-x",
        f"schema={schema}",
        "upgrade",
        "tenant@head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_MIGRATION_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()  # reap so we don't leak a zombie
        logger.error("tenant.schema_migration_timeout", schema=schema)
        raise RuntimeError(f"tenant schema migration timed out for {schema}") from None
    if proc.returncode != 0:
        detail = out.decode(errors="replace")[-800:] if out else ""
        logger.error("tenant.schema_migration_failed", schema=schema, detail=detail)
        raise RuntimeError(f"tenant schema migration failed for {schema}")
    logger.info("tenant.schema_migrated", schema=schema)
