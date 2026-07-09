#!/usr/bin/env python3
"""Scaffold a new bounded-context module in the hexagonal layout used by `tasks`.

    python backend/scripts/scaffold_module.py --name invoices [--entity Invoice]

Generates backend/src/app/modules/<name>/ with the four layers
(domain / application / infrastructure / interface) and a minimal, ruff- and
mypy-strict-clean create+get+list slice you can extend. `tasks` remains the full
reference (CRUD + state machine + RBAC); this gives you a clean starting point.

After generating, follow the printed wiring steps (register the router + add a
tenant migration).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

_MODULES = Path(__file__).resolve().parent.parent / "src" / "app" / "modules"

# Each template uses __NAME__ (package/table/route), __ENTITY__ (PascalCase class),
# __UPPER__ (error-code prefix). Plain .replace() — NOT str.format — so the f-strings
# and FastAPI "{item_id}" braces inside the templates are left untouched.
_TEMPLATES: dict[str, str] = {
    "__init__.py": "",
    "domain/__init__.py": "",
    "application/__init__.py": "",
    "infrastructure/__init__.py": "",
    "interface/__init__.py": "",
    "domain/entities.py": '''from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.modules.__NAME__.domain.errors import __ENTITY__ValidationError


@dataclass(slots=True)
class __ENTITY__:
    """Aggregate root. Add invariants + state-transition methods here."""

    id: UUID
    owner_id: UUID
    name: str
    created_at: datetime

    @classmethod
    def create(cls, *, owner_id: UUID, name: str, now: datetime) -> __ENTITY__:
        name = name.strip()
        if not name:
            raise __ENTITY__ValidationError("name must not be empty")
        return cls(id=uuid4(), owner_id=owner_id, name=name, created_at=now)
''',
    "domain/errors.py": '''from __future__ import annotations

from app.core.errors import DomainError, NotFoundError


class __ENTITY__NotFoundError(NotFoundError):
    code = "__UPPER___NOT_FOUND"


class __ENTITY__ValidationError(DomainError):
    code = "__UPPER___VALIDATION_ERROR"
    http_status = 422
''',
    "domain/ports.py": '''from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.__NAME__.domain.entities import __ENTITY__


class __ENTITY__Repository(Protocol):
    """Port for persistence. Implemented in the infrastructure layer."""

    async def add(self, item: __ENTITY__) -> None: ...

    async def get_by_id(self, item_id: UUID) -> __ENTITY__ | None: ...

    async def list_items(self, *, limit: int, offset: int) -> tuple[list[__ENTITY__], int]: ...
''',
    "application/dtos.py": '''from __future__ import annotations

from dataclasses import dataclass

from app.modules.__NAME__.domain.entities import __ENTITY__


@dataclass(slots=True)
class Create__ENTITY__Command:
    name: str


@dataclass(slots=True)
class List__ENTITY__Query:
    limit: int = 20
    offset: int = 0


@dataclass(slots=True)
class Page:
    items: list[__ENTITY__]
    total: int
    limit: int
    offset: int
''',
    "application/services.py": '''from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.modules.__NAME__.application.dtos import (
    Create__ENTITY__Command,
    List__ENTITY__Query,
    Page,
)
from app.modules.__NAME__.domain.entities import __ENTITY__
from app.modules.__NAME__.domain.errors import __ENTITY__NotFoundError
from app.modules.__NAME__.domain.ports import __ENTITY__Repository


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class __ENTITY__Service:
    repo: __ENTITY__Repository
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def create(self, *, owner_id: UUID, command: Create__ENTITY__Command) -> __ENTITY__:
        item = __ENTITY__.create(owner_id=owner_id, name=command.name, now=self.clock())
        await self.repo.add(item)
        return item

    async def get(self, *, item_id: UUID) -> __ENTITY__:
        item = await self.repo.get_by_id(item_id)
        if item is None:
            raise __ENTITY__NotFoundError(f"__ENTITY__ {item_id} not found")
        return item

    async def list(self, *, query: List__ENTITY__Query) -> Page:
        items, total = await self.repo.list_items(limit=query.limit, offset=query.offset)
        return Page(items=items, total=total, limit=query.limit, offset=query.offset)
''',
    "infrastructure/models.py": '''from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class __ENTITY__Row(Base):
    """Tenant-scoped row — lives in `tenant_<slug>` via the request search_path,
    so NO `schema=` override. tenant_scope routes it to the tenant Alembic head."""

    __tablename__ = "__NAME__"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="__NAME___name_len_chk"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
''',
    "infrastructure/repositories.py": '''from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.__NAME__.domain.entities import __ENTITY__
from app.modules.__NAME__.infrastructure.models import __ENTITY__Row


def _to_entity(row: __ENTITY__Row) -> __ENTITY__:
    return __ENTITY__(
        id=row.id, owner_id=row.owner_id, name=row.name, created_at=row.created_at
    )


class SqlAlchemy__ENTITY__Repository:
    """Concrete repository on AsyncSession. The session-per-request dependency
    owns the transaction — flush() here, never commit()."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, item: __ENTITY__) -> None:
        self._session.add(
            __ENTITY__Row(
                id=item.id, owner_id=item.owner_id, name=item.name, created_at=item.created_at
            )
        )
        await self._session.flush()

    async def get_by_id(self, item_id: UUID) -> __ENTITY__ | None:
        row = await self._session.get(__ENTITY__Row, item_id)
        return _to_entity(row) if row is not None else None

    async def list_items(self, *, limit: int, offset: int) -> tuple[list[__ENTITY__], int]:
        rows = (
            await self._session.scalars(
                select(__ENTITY__Row).order_by(__ENTITY__Row.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
        total = await self._session.scalar(select(func.count()).select_from(__ENTITY__Row)) or 0
        return [_to_entity(r) for r in rows], total
''',
    "interface/schemas.py": '''from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.__NAME__.application.dtos import Create__ENTITY__Command, Page
from app.modules.__NAME__.domain.entities import __ENTITY__

T = TypeVar("T")


class Create__ENTITY__Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)

    def to_command(self) -> Create__ENTITY__Command:
        return Create__ENTITY__Command(name=self.name)


class __ENTITY__Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    created_at: datetime

    @classmethod
    def from_entity(cls, item: __ENTITY__) -> __ENTITY__Response:
        return cls.model_validate(item)


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class Envelope(BaseModel, Generic[T]):
    data: T


class PagedEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta

    @classmethod
    def from_page(cls, page: Page) -> PagedEnvelope[__ENTITY__Response]:
        return PagedEnvelope[__ENTITY__Response](
            data=[__ENTITY__Response.from_entity(i) for i in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
''',
    "interface/router.py": '''from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import PrincipalDep, SessionDep, check_csrf, check_rate_limit
from app.core.security import AuthError, Principal
from app.modules.__NAME__.application.dtos import List__ENTITY__Query
from app.modules.__NAME__.application.services import __ENTITY__Service
from app.modules.__NAME__.infrastructure.repositories import SqlAlchemy__ENTITY__Repository
from app.modules.__NAME__.interface.schemas import (
    Create__ENTITY__Request,
    Envelope,
    PagedEnvelope,
    __ENTITY__Response,
)

router = APIRouter(
    prefix="/__NAME__",
    tags=["__NAME__"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)


def _owner_id(principal: Principal) -> UUID:
    try:
        return UUID(principal.subject)
    except ValueError as exc:
        raise AuthError("Token 'sub' is not a UUID") from exc


async def _service(session: SessionDep) -> __ENTITY__Service:
    return __ENTITY__Service(repo=SqlAlchemy__ENTITY__Repository(session))


ServiceDep = Annotated[__ENTITY__Service, Depends(_service)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[__ENTITY__Response],
    summary="Create a __NAME__ item",
)
async def create_item(
    body: Create__ENTITY__Request, principal: PrincipalDep, service: ServiceDep
) -> Envelope[__ENTITY__Response]:
    item = await service.create(owner_id=_owner_id(principal), command=body.to_command())
    return Envelope(data=__ENTITY__Response.from_entity(item))


@router.get(
    "",
    response_model=PagedEnvelope[__ENTITY__Response],
    summary="List the organization's __NAME__",
)
async def list_items(
    principal: PrincipalDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedEnvelope[__ENTITY__Response]:
    page = await service.list(query=List__ENTITY__Query(limit=limit, offset=offset))
    return PagedEnvelope.from_page(page)


@router.get(
    "/{item_id}",
    response_model=Envelope[__ENTITY__Response],
    summary="Get a __NAME__ item by id",
)
async def get_item(
    item_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[__ENTITY__Response]:
    item = await service.get(item_id=item_id)
    return Envelope(data=__ENTITY__Response.from_entity(item))
''',
}


def _pascal(name: str) -> str:
    # invoices -> Invoice (drop a trailing plural 's' for the singular entity name)
    singular = name[:-1] if name.endswith("s") and len(name) > 3 else name
    return "".join(p.capitalize() for p in re.split(r"[_-]", singular) if p)


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a hexagonal module.")
    ap.add_argument("--name", required=True, help="module/package, table & route prefix (e.g. invoices)")
    ap.add_argument("--entity", help="PascalCase entity class (default: singular of --name)")
    args = ap.parse_args()

    name = args.name
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", name):
        print(f"ERROR: --name must match ^[a-z][a-z0-9_]{{1,40}}$ (got {name!r})")
        return 2
    entity = args.entity or _pascal(name)
    upper = re.sub(r"(?<!^)(?=[A-Z])", "_", entity).upper()

    target = _MODULES / name
    if target.exists():
        print(f"ERROR: {target} already exists — choose another --name or remove it first.")
        return 2

    for rel, template in _TEMPLATES.items():
        content = template.replace("__ENTITY__", entity).replace("__NAME__", name).replace(
            "__UPPER__", upper
        )
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # Auto-format so the result is import-sorted & ruff-clean regardless of how the
    # entity name sorts (isort order is name-dependent). Prefer the venv's ruff.
    ruff = Path(sys.executable).with_name("ruff")
    ruff_cmd = str(ruff) if ruff.exists() else shutil.which("ruff")
    if ruff_cmd:
        subprocess.run([ruff_cmd, "check", "--fix", "--quiet", str(target)], check=False)
        subprocess.run([ruff_cmd, "format", "--quiet", str(target)], check=False)

    print(f"Created module '{name}' (entity {entity}) at {target}")
    print("\nNext steps:")
    print(f"  1. Register the router in src/app/api/v1/__init__.py:")
    print(f"       from app.modules.{name}.interface.router import router as {name}_router")
    print(f"       router.include_router({name}_router)")
    print(f"  2. Generate a TENANT migration (the table is tenant-scoped):")
    print(f"       make revision m=\"add {name} table\"   # then review the autogenerated file")
    print(f"       make -C backend migrate-tenant SCHEMA=tenant_demo   # apply to a tenant")
    print(f"  3. Run the gates: make check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
