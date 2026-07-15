"""tenant: empty initial schema marker

Revision ID: 0001_tenant_init
Revises:
Create Date: 2026-06-16

This is the seed revision of the tenant-template head. Real tenant-scoped
tables (subscriptions, audit, …) will land in subsequent revisions on this
branch. Provisioning a new tenant = run this branch against `tenant_<slug>`.
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_tenant_init"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("tenant",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
