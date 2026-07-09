"""Redis-backed fixed-window rate limiter.

Key schema:  rl:{scope}:{subject}:{bucket}
  scope  — "global" | "writes"
  subject — principal.subject (Keycloak sub, a UUID string)
  bucket  — int(unix_time) // window_seconds
             guarantees at most 2x limit at window boundaries; acceptable for
             abuse-prevention (not billing-grade metering).

Fails open: if Redis is unreachable every request is allowed and a WARNING
is emitted. This keeps the API alive during Redis downtime.
"""

from __future__ import annotations

import time
from typing import Annotated

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, Request
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)


class RateLimiter:
    def __init__(self, redis_url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)

    async def allow(
        self, key: str, *, limit: int, window_seconds: int, fail_closed: bool = False
    ) -> bool:
        """Return True if this request is within the rate limit; False otherwise.

        On a Redis error the default is fail-open (availability over enforcement),
        but `fail_closed=True` denies the request — use it for unauthenticated /
        auth-flow scopes where an attacker could deliberately pressure Redis to
        disable the limiter.
        """
        bucket = int(time.time()) // window_seconds
        full_key = f"rl:{key}:{bucket}"
        try:
            count: int = await self._redis.incr(full_key)
            if count == 1:
                await self._redis.expire(full_key, window_seconds + 1)
            return count <= limit
        except (RedisError, OSError, TimeoutError):
            # Narrowed to connectivity errors so genuine logic bugs surface as 500s
            # rather than being silently treated as "allow".
            logger.warning("rate_limiter.redis_unavailable", key=key, fail_closed=fail_closed)
            return not fail_closed

    async def aclose(self) -> None:
        await self._redis.aclose()


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
