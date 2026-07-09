from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest


@pytest.fixture(autouse=True, scope="session")
def _test_env() -> None:
    os.environ.setdefault("APP_ENV", "local")
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5432/app")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("KEYCLOAK_ISSUER", "http://localhost:8080/realms/saas")
    os.environ.setdefault("KEYCLOAK_AUDIENCE", "saas-backend")


@pytest.fixture
async def client() -> AsyncIterator:
    # Defer the heavy imports so that unit tests (domain, services) do not
    # require the HTTP stack — only integration tests that ask for `client`
    # pay the cost of importing httpx / asgi-lifespan / the app factory.
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac,
    ):
        yield ac
