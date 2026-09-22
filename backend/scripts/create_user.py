"""Create a Keycloak user wired to a tenant: tenant_id attribute + realm roles.

The tenant_id attribute is not optional — the backend resolves the tenant solely
from that token claim (oidc-usermodel-attribute-mapper on `bap-backend`), so a
user without it authenticates at Keycloak and then gets 401/403 everywhere.

Usage:
    python scripts/create_user.py --tenant beeline --username ba \
        --email ba@korxona.com --roles tenant_user,ba

Prints the password (generated unless --password is given).

Exit codes:
    0  success
    1  runtime error
    2  bad arguments
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from uuid import UUID

from sqlalchemy import text

from app.config import get_settings
from app.core.db import dispose_engine, get_engine, get_sessionmaker


async def _tenant_id(slug: str) -> UUID:
    settings = get_settings()
    get_engine(settings)
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                text("SELECT id FROM public.tenants WHERE slug = :slug"),
                {"slug": slug},
            )
        ).first()
    if row is None:
        raise SystemExit(f"tenant '{slug}' not found — run provision_tenant.py first")
    return row.id  # type: ignore[no-any-return]


async def _create(args: argparse.Namespace) -> str:
    from app.core.keycloak_admin import KeycloakAdminClient

    settings = get_settings()
    if not settings.keycloak_admin_enabled:
        raise SystemExit("KEYCLOAK_ADMIN_CLIENT_SECRET unset — admin API disabled")

    tenant_id = await _tenant_id(args.tenant)
    password = args.password or secrets.token_urlsafe(12)

    async with KeycloakAdminClient(
        issuer=str(settings.keycloak_issuer),
        realm=settings.keycloak_realm,
        client_id=settings.keycloak_admin_client_id,
        client_secret=settings.keycloak_admin_client_secret.get_secret_value(),
    ) as kc:
        user_id = await kc.create_user(
            username=args.username,
            email=args.email,
            first_name=args.first_name,
            last_name=args.last_name,
            attributes={"tenant_id": [str(tenant_id)]},
            email_verified=True,
        )
        await kc.set_user_password(user_id, password, temporary=args.temporary)
        for role in args.roles.split(","):
            await kc.assign_realm_role(user_id, role.strip())
    return password


def main() -> int:
    ap = argparse.ArgumentParser(description="Create a tenant user in Keycloak")
    ap.add_argument("--tenant", required=True, help="tenant slug (public.tenants.slug)")
    ap.add_argument("--username", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument(
        "--roles",
        default="tenant_user",
        help="comma-separated realm roles; tenant_user = Заявитель (default)",
    )
    ap.add_argument("--password", help="omit to generate one")
    ap.add_argument("--first-name")
    ap.add_argument("--last-name")
    ap.add_argument(
        "--temporary",
        action="store_true",
        help="force a password change on first login",
    )
    args = ap.parse_args()

    try:
        password = asyncio.run(_create(args))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — CLI boundary, report and exit 1
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    finally:
        asyncio.run(dispose_engine())

    print(f"{args.username}  {password}  roles={args.roles}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
