from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, SecretStr

# A tenant_admin may only grant tenant-scoped roles — never platform_admin.
MemberRole = Literal["tenant_user", "tenant_admin"]
_DEFAULT_MEMBER_ROLES: list[MemberRole] = ["tenant_user"]

# Slug shape shared by tenant onboarding and self-service signup.
_SLUG_PATTERN = r"^[a-z][a-z0-9_]{1,40}$"


class CreateTenantRequest(BaseModel):
    slug: str = Field(
        pattern=_SLUG_PATTERN,
        description="Lowercase identifier, becomes the Postgres schema tenant_<slug>.",
        examples=["acme"],
    )
    name: str = Field(min_length=1, max_length=200, examples=["ACME Corp"])
    admin_email: EmailStr = Field(description="Email of the organization's first admin user.")


class SignupRequest(BaseModel):
    """Public self-service company registration."""

    company_name: str = Field(min_length=1, max_length=200, examples=["ACME Corp"])
    slug: str = Field(
        pattern=_SLUG_PATTERN,
        description="Lowercase identifier, becomes the Postgres schema tenant_<slug>.",
        examples=["acme"],
    )
    admin_email: EmailStr = Field(description="Email of the founding administrator.")
    # SecretStr keeps the password out of logs / tracebacks / repr. Length is bounded
    # at both ends: a 12-char floor for strength, a 128 ceiling so an over-long value
    # can't be used to burn CPU in the password hasher.
    admin_password: SecretStr = Field(min_length=12, max_length=128)


class SignupResponse(BaseModel):
    """Deliberately minimal — exposes no internal Keycloak IDs to anonymous callers."""

    tenant_id: UUID
    slug: str
    login_url: str


class TenantCreatedResponse(BaseModel):
    tenant_id: UUID
    slug: str
    keycloak_group_id: str
    admin_user_id: str


class InviteMemberRequest(BaseModel):
    email: EmailStr
    roles: list[MemberRole] = Field(default_factory=lambda: list(_DEFAULT_MEMBER_ROLES))


class MemberInvitedResponse(BaseModel):
    user_id: str
    tenant_id: UUID
    email: EmailStr
