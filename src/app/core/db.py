from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass


def get_engine(settings: Settings) -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            str(settings.database_url),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_recycle=settings.database_pool_recycle_seconds,
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "application_name": "saas-backend",
                    "statement_timeout": str(settings.database_statement_timeout_ms),
                }
            },
            future=True,
        )
        _sessionmaker = async_sessionmaker(
            bind=_engine,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Sessionmaker not initialized. Call get_engine(settings) first.")
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def session_for_schema(schema: str) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession with search_path set to (schema, public)."""
    factory = get_sessionmaker()
    async with factory() as session:
        # Identifiers cannot be parameterized — schema is validated upstream.
        await session.execute(
            __import__("sqlalchemy").text(f'SET LOCAL search_path TO "{schema}", public')
        )
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
