from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

# A client-supplied X-Request-Id is untrusted: cap length and restrict to a safe
# charset so it cannot inject newlines/control chars into logs or be reflected as
# an oversized/forged response header. Anything else gets a fresh uuid4.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id")
        request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception("request.unhandled", duration_ms=duration_ms)
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers["x-request-id"] = request_id
            logger.info(
                "request.completed",
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()
