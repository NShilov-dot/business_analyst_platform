"""Alembic environment with two heads: `public` and `tenant`.

Pick the head via `-x scope=public` (default) or `-x scope=tenant -x schema=tenant_<slug>`.
The `tenant` head is a schema-template — its migrations run inside the named schema and
are how new tenants are provisioned (copy template → run tenant head against new schema).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.core.db import Base

# Import model modules so their tables register on Base.metadata
import app.modules.tenants.models  # noqa: F401  (public schema)
import app.modules.tasks.infrastructure.models  # noqa: F401  (tenant schema)
import app.modules.departments.infrastructure.models  # noqa: F401  (tenant schema)
import app.modules.intake_templates.infrastructure.models  # noqa: F401  (tenant schema)
import app.modules.audit.infrastructure.models  # noqa: F401  (tenant schema)
import app.modules.tickets.infrastructure.models  # noqa: F401  (tenant schema)
import app.modules.ai_structuring.infrastructure.models  # noqa: F401  (tenant schema)


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", str(settings.database_url))

target_metadata = Base.metadata


def _scope() -> str:
    return (context.get_x_argument(as_dictionary=True).get("scope") or "public").lower()


def _schema() -> str:
    scope = _scope()
    if scope == "public":
        return "public"
    schema = context.get_x_argument(as_dictionary=True).get("schema")
    if not schema:
        raise RuntimeError("scope=tenant requires -x schema=tenant_<slug>")
    if not schema.startswith("tenant_"):
        raise RuntimeError("schema must be prefixed with 'tenant_'")
    return schema


def _include_object(obj, name, type_, reflected, compare_to):
    # Split heads: tables explicitly tagged as `tenant_scope=public` go to public head,
    # everything else (default) is treated as tenant-scoped.
    if type_ == "table":
        scope_tag = obj.info.get("tenant_scope", "tenant")
        return scope_tag == _scope()
    return True


def _do_run_migrations(connection: Connection) -> None:
    schema = _schema()
    connection.execute(__import__("sqlalchemy").text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    connection.execute(__import__("sqlalchemy").text(f'SET search_path TO "{schema}", public'))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=False,
        include_object=_include_object,
        version_table=f"alembic_version_{_scope()}",
        version_table_schema=schema,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=f"alembic_version_{_scope()}",
        version_table_schema=_schema(),
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
        # Commit explicitly: the pre-migration CREATE SCHEMA / SET search_path opens
        # the connection's transaction before Alembic's begin_transaction, so Alembic
        # treats it as caller-owned and does NOT commit — without this the DDL is
        # rolled back on close (async SQLAlchemy 2.0 commit-as-you-go).
        await connection.commit()
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
