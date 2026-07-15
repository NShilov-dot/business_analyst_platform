"""Provision a new tenant: insert into public.tenants, create Keycloak group,
run tenant-head Alembic migrations.

Usage:
    python scripts/provision_tenant.py --slug acme --name "ACME Corp"

Flags:
    --skip-keycloak   Skip Keycloak group creation even if admin is configured.
                      Useful when provisioning in environments without Keycloak.

Exit codes:
    0  success
    1  runtime error
    2  bad arguments
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
from uuid import UUID

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.core.db import dispose_engine, get_engine, get_sessionmaker

logger = structlog.get_logger(__name__)

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


async def _create_tenant_row(slug: str, name: str) -> UUID:
    settings = get_settings()
    get_engine(settings)
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "INSERT INTO public.tenants (slug, name) "
                    "VALUES (:slug, :name) RETURNING id"
                ),
                {"slug": slug, "name": name},
            )
        ).first()
        await session.commit()
    assert row is not None
    return row.id


async def _update_keycloak_group_id(tenant_id: UUID, group_id: str) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE public.tenants SET keycloak_group_id = :gid WHERE id = :tid"
            ),
            {"gid": group_id, "tid": tenant_id},
        )
        await session.commit()


async def _create_keycloak_group(tenant_id: UUID, slug: str) -> str | None:
    """Create a Keycloak group for the tenant.

    Returns the group ID on success, None when admin integration is disabled.
    """
    from app.core.keycloak_admin import KeycloakAdminClient, TenantGroupSpec

    settings = get_settings()
    if not settings.keycloak_admin_enabled:
        logger.info("provision.keycloak_skipped", reason="admin not configured")
        return None

    spec = TenantGroupSpec(tenant_slug=slug, tenant_id=str(tenant_id))
    async with KeycloakAdminClient(
        issuer=str(settings.keycloak_issuer),
        realm=settings.keycloak_realm,
        client_id=settings.keycloak_admin_client_id,
        client_secret=settings.keycloak_admin_client_secret.get_secret_value(),
    ) as kc:
        group_id = await kc.create_group(spec.group_name, attributes=spec.attributes)
    return group_id


def _run_tenant_migrations(schema: str) -> None:
    cmd = [
        "alembic", "-x", "scope=tenant", "-x", f"schema={schema}",
        "upgrade", "tenant@head",
    ]
    subprocess.run(cmd, check=True)


async def _provision(slug: str, name: str, *, skip_keycloak: bool) -> int:
    # 1. Create the public.tenants row.
    tenant_id = await _create_tenant_row(slug, name)
    logger.info("provision.tenant_created", slug=slug, tenant_id=str(tenant_id))

    # 2. Create Keycloak group (optional).
    if not skip_keycloak:
        group_id = await _create_keycloak_group(tenant_id, slug)
        if group_id is not None:
            await _update_keycloak_group_id(tenant_id, group_id)
            logger.info(
                "provision.keycloak_group_linked",
                group_id=group_id,
                tenant_id=str(tenant_id),
            )

    # 3. Initialize tenant schema via Alembic.
    await dispose_engine()
    schema = f"tenant_{slug}"
    _run_tenant_migrations(schema)
    logger.info("provision.migrations_applied", schema=schema)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a new tenant.")
    parser.add_argument("--slug", required=True, help="Lowercase slug, e.g. acme")
    parser.add_argument("--name", required=True, help="Human-readable tenant name")
    parser.add_argument(
        "--skip-keycloak",
        action="store_true",
        help="Skip Keycloak group creation",
    )
    args = parser.parse_args()

    if not SLUG_RE.fullmatch(args.slug):
        print(f"error: --slug must match {SLUG_RE.pattern}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(_provision(args.slug, args.name, skip_keycloak=args.skip_keycloak))
    except Exception as exc:
        logger.error("provision.failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
