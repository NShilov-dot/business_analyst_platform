from __future__ import annotations

import structlog
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.core.deps import JWKSDep, SettingsDep

router = APIRouter(tags=["health"])
logger = structlog.get_logger(__name__)


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe (DB + JWKS reachable)")
async def readyz(_: SettingsDep, jwks: JWKSDep, response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}
    healthy = True

    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # surface concrete reason in body, stay up
        healthy = False
        checks["database"] = f"failed: {exc.__class__.__name__}"
        logger.warning("readyz.db_failed", error=str(exc))

    try:
        await jwks.get()
        checks["jwks"] = "ok"
    except Exception as exc:  # surface concrete reason in body, stay up
        healthy = False
        checks["jwks"] = f"failed: {exc.__class__.__name__}"
        logger.warning("readyz.jwks_failed", error=str(exc))

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}
