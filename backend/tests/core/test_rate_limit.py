"""Unit tests for RateLimiter.

Uses an in-memory fake Redis so no real Redis process is required.
"""

from __future__ import annotations

import pytest

from app.core.rate_limit import RateLimiter


class _FakeRedis:
    """Minimal Redis fake that tracks INCR state in memory."""

    def __init__(self) -> None:
        self._data: dict[str, int] = {}
        self.raise_on_next: bool = False

    async def incr(self, key: str) -> int:
        if self.raise_on_next:
            raise ConnectionError("Redis unavailable")
        self._data[key] = self._data.get(key, 0) + 1
        return self._data[key]

    async def expire(self, key: str, ttl: int) -> None:
        pass  # TTL not needed for unit tests

    async def aclose(self) -> None:
        pass


def _make_limiter() -> tuple[RateLimiter, _FakeRedis]:
    fake = _FakeRedis()
    limiter = RateLimiter.__new__(RateLimiter)
    limiter._redis = fake  # type: ignore[attr-defined]
    return limiter, fake


@pytest.mark.asyncio
async def test_allows_requests_within_limit() -> None:
    limiter, _ = _make_limiter()
    for _ in range(5):
        assert await limiter.allow("user:alice", limit=5, window_seconds=60) is True


@pytest.mark.asyncio
async def test_denies_request_over_limit() -> None:
    limiter, _ = _make_limiter()
    for _ in range(5):
        await limiter.allow("user:alice", limit=5, window_seconds=60)
    assert await limiter.allow("user:alice", limit=5, window_seconds=60) is False


@pytest.mark.asyncio
async def test_different_keys_are_independent() -> None:
    limiter, _ = _make_limiter()
    for _ in range(5):
        await limiter.allow("user:alice", limit=5, window_seconds=60)
    # alice is at the limit; bob should still be allowed
    assert await limiter.allow("user:bob", limit=5, window_seconds=60) is True


@pytest.mark.asyncio
async def test_fails_open_when_redis_is_unavailable() -> None:
    limiter, fake = _make_limiter()
    fake.raise_on_next = True
    # Should return True (allow) even though Redis failed
    assert await limiter.allow("user:alice", limit=5, window_seconds=60) is True


@pytest.mark.asyncio
async def test_fails_closed_when_requested() -> None:
    """Pre-auth / auth-flow scopes deny on Redis failure instead of allowing."""
    limiter, fake = _make_limiter()
    fake.raise_on_next = True
    allowed = await limiter.allow("auth:1.2.3.4", limit=5, window_seconds=60, fail_closed=True)
    assert allowed is False


@pytest.mark.asyncio
async def test_limit_of_one_allows_first_then_denies() -> None:
    limiter, _ = _make_limiter()
    assert await limiter.allow("strict", limit=1, window_seconds=60) is True
    assert await limiter.allow("strict", limit=1, window_seconds=60) is False


@pytest.mark.asyncio
async def test_separate_scopes_are_independent() -> None:
    """global and writes scopes must not share a counter."""
    limiter, _ = _make_limiter()
    for _ in range(3):
        await limiter.allow("global:user", limit=3, window_seconds=60)
    # global is exhausted; writes scope is a different key and should still allow
    assert await limiter.allow("writes:user", limit=3, window_seconds=60) is True
