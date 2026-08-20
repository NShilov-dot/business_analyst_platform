from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.db import get_sessionmaker
from app.core.errors import TenantResolutionError

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Resolved tenant for the current request."""

    id: UUID
    slug: str

    @property
    def schema(self) -> str:
        return tenant_schema(self.slug)


def tenant_schema(slug: str) -> str:
    if not _SLUG_RE.fullmatch(slug):
        raise TenantResolutionError(
            f"Invalid tenant slug: {slug!r}. Must match {_SLUG_RE.pattern}.",
        )
    return f"tenant_{slug}"


def bucket_for(settings: Settings, slug: str) -> str:
    """Per-tenant object-store bucket name — mirrors `tenant_schema()`'s slug
    validation so the slug→resource-name mapping lives in one place."""
    if not _SLUG_RE.fullmatch(slug):
        raise TenantResolutionError(
            f"Invalid tenant slug: {slug!r}. Must match {_SLUG_RE.pattern}.",
        )
    return f"{settings.s3_bucket_prefix}-{slug}"


async def resolve_tenant(*, tenant_id: UUID) -> TenantContext:
    """Look up tenant in the public registry. Raises TenantResolutionError if missing/disabled."""
    factory = get_sessionmaker()
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT id, slug FROM public.tenants WHERE id = :tid AND status = 'active'"),
                {"tid": tenant_id},
            )
        ).first()
    if row is None:
        raise TenantResolutionError(f"Tenant {tenant_id} not found or inactive")
    return TenantContext(id=row.id, slug=row.slug)


async def session_for_tenant(tenant: TenantContext) -> AsyncIterator[AsyncSession]:
    """Open an AsyncSession whose search_path is locked to the tenant's schema."""
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(text(f'SET LOCAL search_path TO "{tenant.schema}", public'))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
