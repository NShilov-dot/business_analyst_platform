from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_healthz_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_readyz_reports_db_and_jwks(client: AsyncClient) -> None:
    response = await client.get("/v1/readyz")
    # Either 200 (deps reachable) or 503 (deps down). Body must always list both checks.
    assert response.status_code in {200, 503}
    body = response.json()
    assert set(body["checks"].keys()) == {"database", "jwks"}
