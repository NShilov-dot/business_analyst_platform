"""Tenant + member provisioning.

onboard_tenant  — register a new organization (tenant): public.tenants row →
                  Keycloak group (carrying tenant_id) → tenant Postgres schema →
                  first admin user (tenant_admin).
invite_member   — add an employee to an EXISTING tenant. The tenant_id is taken
                  from the caller's principal at the interface layer, never from
                  client input, so a tenant_admin can only add to their own tenant.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.core.errors import (
    ConflictError,
    DomainError,
    PermissionDeniedError,
    ServiceUnavailableError,
    TenantResolutionError,
    WeakPasswordError,
)
from app.core.keycloak_admin import KeycloakAdminClient, KeycloakAdminError, TenantGroupSpec
from app.core.tenancy import bucket_for
from app.modules.documents.domain.ports import ObjectStorePort

logger = structlog.get_logger(__name__)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
# Slugs an anonymous self-service signup may NOT claim: operational names and the
# seeded demo tenant. Squatting these would let a stranger occupy a confusing or
# privileged-looking schema/group name. Platform-admin onboarding is unrestricted.
_RESERVED_SLUGS = frozenset(
    {
        "admin",
        "api",
        "app",
        "auth",
        "demo",
        "internal",
        "keycloak",
        "platform",
        "public",
        "root",
        "bap",
        "static",
        "status",
        "support",
        "system",
        "tenant",
        "tenants",
        "www",
    }
)
# Required actions stamped on every new member so they must set a password (and
# verify email) on first login — independent of whether the invite email sends.
_MEMBER_REQUIRED_ACTIONS = ["UPDATE_PASSWORD", "VERIFY_EMAIL"]
# Roles a tenant invite may grant. Defence-in-depth alongside the interface-layer
# Literal: never let a tenant_admin mint a platform_admin, even via a future caller.
_TENANT_GRANTABLE_ROLES = frozenset({"tenant_user", "tenant_admin"})

SchemaMigrator = Callable[[str], Awaitable[None]]

# Cap concurrent provisioning sagas process-wide. Each one forks an alembic
# subprocess and opens a direct (NullPool) Postgres connection outside the app pool,
# so without a ceiling a burst of concurrent signups could exhaust processes / DB
# connections regardless of the per-IP rate limits. Shared across all service
# instances (they are constructed per-request via DI).
_MAX_CONCURRENT_PROVISIONS = 4
_PROVISION_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_PROVISIONS)


class InvalidSlugError(DomainError):
    code = "INVALID_SLUG"
    http_status = 400


@dataclass(slots=True)
class TenantOnboarded:
    tenant_id: UUID
    slug: str
    keycloak_group_id: str
    admin_user_id: str


@dataclass(slots=True)
class MemberInvited:
    user_id: str
    tenant_id: UUID
    email: str


class TenantProvisioningService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        kc: KeycloakAdminClient,
        migrate_schema: SchemaMigrator,
        invite_client_id: str | None = None,
        object_store: ObjectStorePort | None = None,
    ) -> None:
        self._sm = sessionmaker
        self._kc = kc
        self._migrate_schema = migrate_schema
        self._invite_client_id = invite_client_id
        # Optional — envs without S3 configured provision as before (mirrors
        # disabled-Keycloak-admin). Self-heals via ensure_bucket on first
        # upload for tenants provisioned before S3 was enabled.
        self._store = object_store

    # ------------------------------------------------------------------
    # Organization onboarding
    # ------------------------------------------------------------------

    async def onboard_tenant(self, *, slug: str, name: str, admin_email: str) -> TenantOnboarded:
        """Platform-admin onboarding: the first admin gets an invite email and must
        set a password / verify email on first login (no password is supplied here)."""

        async def _provision(tenant_id: UUID, group_id: str) -> str:
            return await self._provision_member(
                tenant_id=tenant_id, group_id=group_id, email=admin_email, roles=["tenant_admin"]
            )

        return await self._onboard(slug=slug, name=name, provision_admin=_provision)

    async def register_tenant(
        self,
        *,
        slug: str,
        name: str,
        admin_email: str,
        admin_password: str,
        require_email_verification: bool = False,
    ) -> TenantOnboarded:
        """Self-service signup: the founder picks their own password. The admin user is
        always created email_verified=False (self-service proves no email ownership).
        When require_email_verification is set (SMTP available) the VERIFY_EMAIL required
        action is stamped and a verification email is sent, so the founder must confirm
        the address on first login; otherwise they can log in immediately.

        Reserved slugs are rejected here (but not for platform-admin onboarding),
        since this entry point is reachable by anonymous callers."""
        if slug.strip().lower() in _RESERVED_SLUGS:
            raise InvalidSlugError(f"slug '{slug.strip().lower()}' is reserved")

        async def _provision(tenant_id: UUID, group_id: str) -> str:
            return await self._provision_admin_with_password(
                tenant_id=tenant_id,
                group_id=group_id,
                email=admin_email,
                password=admin_password,
                require_email_verification=require_email_verification,
            )

        return await self._onboard(slug=slug, name=name, provision_admin=_provision)

    async def _onboard(
        self,
        *,
        slug: str,
        name: str,
        provision_admin: Callable[[UUID, str], Awaitable[str]],
    ) -> TenantOnboarded:
        slug = slug.strip().lower()
        if not _SLUG_RE.fullmatch(slug):
            raise InvalidSlugError(f"slug must match {_SLUG_RE.pattern}")

        tenant_id = uuid4()
        # 1. Registry row in public.tenants (id generated app-side so we can build
        #    the Keycloak group attribute before any further side effects).
        async with self._sm() as session:
            try:
                await session.execute(
                    text("INSERT INTO public.tenants (id, slug, name) VALUES (:id, :slug, :name)"),
                    {"id": tenant_id, "slug": slug, "name": name},
                )
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(f"Tenant slug '{slug}' already exists") from exc

        # Steps 2-4 happen after the row is committed; if any fails we compensate
        # (delete the user + group + the registry row) so a stuck half-provisioned
        # tenant doesn't block re-onboarding the same slug. The whole side-effecting
        # saga is bounded by a semaphore so a burst of concurrent signups can't fork
        # an unbounded number of alembic subprocesses / open uncounted DB connections.
        group_id: str | None = None
        admin_user_id: str | None = None
        async with _PROVISION_SEMAPHORE:
            try:
                # 2. Keycloak group carrying the tenant_id attribute.
                spec = TenantGroupSpec(tenant_slug=slug, tenant_id=str(tenant_id))
                group_id = await self._kc.create_group(spec.group_name, attributes=spec.attributes)
                async with self._sm() as session:
                    await session.execute(
                        text("UPDATE public.tenants SET keycloak_group_id = :gid WHERE id = :id"),
                        {"gid": group_id, "id": tenant_id},
                    )
                    await session.commit()

                # 3. First admin user (strategy supplied by the caller). Done BEFORE
                #    the expensive schema migration so the cheap, attacker-forcible
                #    failures (duplicate email -> 409, weak password -> 400) fail fast
                #    and never leave behind an orphaned migrated schema.
                admin_user_id = await provision_admin(tenant_id, group_id)

                # 4. Tenant Postgres schema (tasks tables, etc.). Idempotent
                #    (CREATE SCHEMA IF NOT EXISTS + versioned alembic), so it's safe to
                #    re-run on a later re-onboard.
                await self._migrate_schema(slug)

                # 5. Object-store bucket. Idempotent (ensure_bucket) and LAST —
                #    the schema migration is the expensive step, so cheap,
                #    attacker-forcible failures above it still fail first.
                #    Skipped entirely when S3 is not configured.
                if self._store is not None:
                    await self._store.ensure_bucket(bucket_for(get_settings(), slug))
            except Exception:
                await self._compensate_onboard(
                    tenant_id=tenant_id,
                    group_id=group_id,
                    slug=slug,
                    admin_user_id=admin_user_id,
                )
                raise

        logger.info(
            "tenant.onboarded", tenant_id=str(tenant_id), slug=slug, admin_user_id=admin_user_id
        )
        return TenantOnboarded(
            tenant_id=tenant_id, slug=slug, keycloak_group_id=group_id, admin_user_id=admin_user_id
        )

    async def _compensate_onboard(
        self, *, tenant_id: UUID, group_id: str | None, slug: str, admin_user_id: str | None = None
    ) -> None:
        """Best-effort rollback of a partially-provisioned tenant.

        Removes the admin user (if one was created), the Keycloak group, and the
        public.tenants row. The tenant schema is left as-is — it's idempotent and
        reused on a successful re-onboard (and with the user provisioned before the
        migration, a schema only exists here on a rare post-user infra failure).
        """
        logger.warning("tenant.onboard_compensating", tenant_id=str(tenant_id), slug=slug)
        if admin_user_id is not None:
            await self._safe_delete_user(admin_user_id)
        if group_id is not None:
            try:
                await self._kc.delete_group(group_id)
            except KeycloakAdminError:
                logger.warning("tenant.compensate_group_failed", group_id=group_id)
        if self._store is not None:
            try:
                await self._store.delete_bucket(bucket_for(get_settings(), slug))
            except Exception:
                logger.warning("tenant.compensate_bucket_failed", slug=slug)
        try:
            async with self._sm() as session:
                await session.execute(
                    text("DELETE FROM public.tenants WHERE id = :id"), {"id": tenant_id}
                )
                await session.commit()
        except Exception:
            # Compensation is best-effort; it must not mask the original error.
            logger.warning("tenant.compensate_row_failed", tenant_id=str(tenant_id))

    # ------------------------------------------------------------------
    # Member (employee) onboarding
    # ------------------------------------------------------------------

    async def invite_member(
        self, *, tenant_id: UUID, email: str, roles: Sequence[str] = ("tenant_user",)
    ) -> MemberInvited:
        disallowed = set(roles) - _TENANT_GRANTABLE_ROLES
        if disallowed:
            raise PermissionDeniedError(f"Cannot grant roles: {sorted(disallowed)}")
        async with self._sm() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT slug, keycloak_group_id FROM public.tenants "
                        "WHERE id = :id AND status = 'active'"
                    ),
                    {"id": tenant_id},
                )
            ).first()
        if row is None:
            raise TenantResolutionError(f"Tenant {tenant_id} not found or inactive")
        if row.keycloak_group_id is None:
            raise ServiceUnavailableError("Tenant has no Keycloak group; re-provision required")

        user_id = await self._provision_member(
            tenant_id=tenant_id,
            group_id=row.keycloak_group_id,
            email=email,
            roles=list(roles),
        )
        return MemberInvited(user_id=user_id, tenant_id=tenant_id, email=email)

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    async def _provision_member(
        self, *, tenant_id: UUID, group_id: str, email: str, roles: Sequence[str]
    ) -> str:
        try:
            user_id = await self._kc.create_user(
                username=email,
                email=email,
                attributes={"tenant_id": [str(tenant_id)]},
                required_actions=list(_MEMBER_REQUIRED_ACTIONS),
                enabled=True,
                email_verified=False,
            )
        except KeycloakAdminError as exc:
            # 409 = email/username already taken (realm-wide; duplicateEmails off).
            # Map to a clean 409 instead of a generic 500. Keep the message generic
            # so it doesn't confirm WHICH tenant the existing user belongs to.
            if exc.status_code == 409:
                raise ConflictError("A user with this email already exists") from exc
            raise

        try:
            await self._kc.add_user_to_group(user_id, group_id)
            for role in roles:
                await self._kc.assign_realm_role(user_id, role)
        except Exception:
            # Don't leave a half-configured user behind.
            await self._safe_delete_user(user_id)
            raise

        # The required actions are already on the user; the email is just delivery,
        # so a missing SMTP config OR a transient transport error (httpx.HTTPError,
        # which is NOT a KeycloakAdminError) must not fail provisioning / orphan it.
        try:
            await self._kc.send_execute_actions_email(
                user_id, list(_MEMBER_REQUIRED_ACTIONS), client_id=self._invite_client_id
            )
        except Exception as exc:
            logger.warning("tenant.invite_email_failed", user_id=user_id, error=str(exc))
        return user_id

    async def _provision_admin_with_password(
        self,
        *,
        tenant_id: UUID,
        group_id: str,
        email: str,
        password: str,
        require_email_verification: bool,
    ) -> str:
        """Create the founding admin for a self-service signup with a permanent,
        founder-chosen password.

        The user is ALWAYS created email_verified=False — signup proves no email
        ownership, so we never assert it. When require_email_verification is set (SMTP
        available), we also stamp the VERIFY_EMAIL required action and send the
        verification link, forcing confirmation on first login; otherwise the founder
        can log in immediately (the realm's verifyEmail stays off)."""
        required_actions = ["VERIFY_EMAIL"] if require_email_verification else []
        try:
            user_id = await self._kc.create_user(
                username=email,
                email=email,
                attributes={"tenant_id": [str(tenant_id)]},
                required_actions=required_actions,
                enabled=True,
                # Never claim a verified email we haven't proven ownership of.
                email_verified=False,
            )
        except KeycloakAdminError as exc:
            if exc.status_code == 409:
                raise ConflictError("A user with this email already exists") from exc
            raise

        try:
            # Set the password first — most likely step to be rejected (policy).
            await self._kc.set_user_password(user_id, password, temporary=False)
        except KeycloakAdminError as exc:
            await self._safe_delete_user(user_id)
            if exc.status_code == 400:
                raise WeakPasswordError("Password does not meet the required policy") from exc
            raise

        try:
            await self._kc.add_user_to_group(user_id, group_id)
            await self._kc.assign_realm_role(user_id, "tenant_admin")
        except Exception:
            await self._safe_delete_user(user_id)
            raise

        # Best-effort verification email (only when required + SMTP configured). A
        # failed/missing SMTP transport must not fail or orphan an otherwise-complete
        # signup — the VERIFY_EMAIL required action is already on the user regardless.
        if require_email_verification:
            try:
                await self._kc.send_execute_actions_email(
                    user_id, ["VERIFY_EMAIL"], client_id=self._invite_client_id
                )
            except Exception as exc:
                logger.warning("signup.verify_email_failed", user_id=user_id, error=str(exc))
        return user_id

    async def _safe_delete_user(self, user_id: str) -> None:
        try:
            await self._kc.delete_user(user_id)
        except KeycloakAdminError:
            logger.warning("tenant.member_cleanup_failed", user_id=user_id)
