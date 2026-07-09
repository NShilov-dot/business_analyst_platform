"""FastAPI exception handlers. Imports FastAPI; keep out of domain layers."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import DomainError

logger = structlog.get_logger(__name__)


def _request_id(request: Request) -> str:
    # Prefer the ID already set by RequestContextMiddleware to ensure consistency
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = request.headers.get("x-request-id")
    return rid or "unknown"


def _envelope(
    *, code: str, message: str, details: list[dict[str, Any]], request_id: str
) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details},
        "meta": {"requestId": request_id},
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        headers: dict[str, str] = {}
        if exc.http_status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            status_code=exc.http_status,
            headers=headers,
            content=_envelope(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(
                code="VALIDATION_ERROR",
                message="Request validation failed",
                details=details,
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                code=f"HTTP_{exc.status_code}",
                message=str(exc.detail),
                details=[],
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "request.unhandled_exception",
            path=request.url.path,
            method=request.method,
            request_id=_request_id(request),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                code="INTERNAL_ERROR",
                message="Internal server error",
                details=[],
                request_id=_request_id(request),
            ),
        )
