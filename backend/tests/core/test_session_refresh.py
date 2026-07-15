"""Tests for the token-refresh single-flight in deps._maybe_refresh.

The bug being guarded against: two requests in the 30s pre-expiry window both
refresh with the same one-time refresh_token; Keycloak rotates on the first, the
second's refresh fails, and the old code deleted the live session — force-logging
the user out. The fix serialises refresh with a per-session lock and, on a failed
refresh, prefers a concurrently-stored fresh session over deleting.
"""

from __future__ import annotations

import time

from app.core.deps import _maybe_refresh
from app.core.oidc import OIDCError, TokenSet
from app.core.sessions import SessionData

_NOW = int(time.time())


def _expiring_session(access: str = "old-access") -> SessionData:
    return SessionData(
        subject="sub-1",
        access_token=access,
        refresh_token="refresh-1",
        id_token="id-1",
        access_expires_at=_NOW + 10,  # within the 30s refresh leeway
        created_at=_NOW - 100,
    )


class _FakeStore:
    def __init__(self, *, lock_free: bool = True, current: SessionData | None = None) -> None:
        self._lock_free = lock_free
        self.current = current
        self.deleted = False
        self.updated: SessionData | None = None
        self.lock_released = False

    async def acquire_refresh_lock(self, sid: str, *, ttl_seconds: int = 10) -> bool:
        return self._lock_free

    async def release_refresh_lock(self, sid: str) -> None:
        self.lock_released = True

    async def get(self, sid: str) -> SessionData | None:
        return self.current

    async def update(self, sid: str, data: SessionData) -> None:
        self.updated = data
        self.current = data

    async def delete(self, sid: str) -> None:
        self.deleted = True
        self.current = None


class _FakeOIDC:
    def __init__(self, *, tokens: TokenSet | None = None, error: bool = False) -> None:
        self._tokens = tokens
        self._error = error
        self.called = False

    async def refresh(self, *, refresh_token: str) -> TokenSet:
        self.called = True
        if self._error or self._tokens is None:
            raise OIDCError("refresh failed")
        return self._tokens


def _tokens(access: str = "new-access") -> TokenSet:
    return TokenSet(
        access_token=access,
        refresh_token="refresh-2",
        id_token="id-2",
        expires_in=300,
        refresh_expires_in=1800,
        token_type="Bearer",
    )


async def test_not_near_expiry_returns_unchanged() -> None:
    data = SessionData("s", "a", "r", "i", _NOW + 999, _NOW)
    store = _FakeStore()
    oidc = _FakeOIDC(tokens=_tokens())
    out = await _maybe_refresh("sid", data, oidc=oidc, store=store)  # type: ignore[arg-type]
    assert out is data
    assert oidc.called is False


async def test_happy_refresh_updates_and_releases_lock() -> None:
    store = _FakeStore(lock_free=True)
    oidc = _FakeOIDC(tokens=_tokens("new-access"))
    out = await _maybe_refresh("sid", _expiring_session(), oidc=oidc, store=store)  # type: ignore[arg-type]
    assert out.access_token == "new-access"
    assert store.updated is not None
    assert store.lock_released is True


async def test_lock_miss_reuses_concurrent_refresh_without_calling_oidc() -> None:
    # Loser of the single-flight: winner already stored a fresh token.
    winner = _expiring_session(access="winner-access")
    store = _FakeStore(lock_free=False, current=winner)
    oidc = _FakeOIDC(tokens=_tokens())
    out = await _maybe_refresh("sid", _expiring_session("old-access"), oidc=oidc, store=store)  # type: ignore[arg-type]
    assert out.access_token == "winner-access"
    assert oidc.called is False  # did NOT refresh in parallel


async def test_refresh_error_with_concurrent_update_does_not_delete_session() -> None:
    # Our refresh fails, but a racing request already rotated the token: reuse it,
    # do NOT delete the live session (the core anti-logout fix).
    concurrent = _expiring_session(access="rotated-access")
    store = _FakeStore(lock_free=True, current=concurrent)
    oidc = _FakeOIDC(error=True)
    out = await _maybe_refresh("sid", _expiring_session("old-access"), oidc=oidc, store=store)  # type: ignore[arg-type]
    assert out.access_token == "rotated-access"
    assert store.deleted is False
    assert store.lock_released is True


async def test_refresh_error_without_concurrent_update_deletes_and_raises() -> None:
    same = _expiring_session("old-access")
    store = _FakeStore(lock_free=True, current=same)
    oidc = _FakeOIDC(error=True)
    raised = False
    try:
        await _maybe_refresh("sid", _expiring_session("old-access"), oidc=oidc, store=store)  # type: ignore[arg-type]
    except Exception as exc:  # AuthError
        raised = True
        assert "expired" in str(exc).lower()
    assert raised is True
    assert store.deleted is True
