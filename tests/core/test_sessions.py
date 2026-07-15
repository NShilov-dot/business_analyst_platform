"""Unit tests for SessionStore using a fake Redis."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from app.core.sessions import LoginState, SessionData, SessionStore


class _FakeRedis:
    """In-memory Redis stand-in that supports SET ex=, GET, DELETE, EXPIRE, pipeline."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float]] = {}  # key → (value, expires_at)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = (value, time.time() + ex)

    async def get(self, key: str) -> str | None:
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at < time.time():
            del self.store[key]
            return None
        return value

    async def expire(self, key: str, ttl: int) -> None:
        if key in self.store:
            value, _ = self.store[key]
            self.store[key] = (value, time.time() + ttl)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def aclose(self) -> None:
        pass


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str]] = []

    def get(self, key: str) -> None:
        self._ops.append(("get", key))

    def delete(self, key: str) -> None:
        self._ops.append(("delete", key))

    async def execute(self) -> list[Any]:
        results = []
        for op, key in self._ops:
            if op == "get":
                results.append(await self._redis.get(key))
            elif op == "delete":
                await self._redis.delete(key)
                results.append(1)
        return results


def _make_store(
    *, ttl: int = 3600, idle: int = 1800, cipher: object | None = None
) -> tuple[SessionStore, _FakeRedis]:
    fake = _FakeRedis()
    store = SessionStore.__new__(SessionStore)
    store._redis = fake  # type: ignore[attr-defined]
    store._ttl = ttl  # type: ignore[attr-defined]
    store._idle = idle  # type: ignore[attr-defined]
    store._cipher = cipher  # type: ignore[attr-defined]
    return store, fake


def _session_data() -> SessionData:
    now = int(time.time())
    return SessionData(
        subject="user-1",
        access_token="AT",
        refresh_token="RT",
        id_token="IT",
        access_expires_at=now + 300,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Session create / get / touch / update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_unique_session_ids() -> None:
    store, _ = _make_store()
    sid1 = await store.create(_session_data())
    sid2 = await store.create(_session_data())
    assert sid1 != sid2
    assert len(sid1) >= 32  # url-safe encoding of 32 random bytes


@pytest.mark.asyncio
async def test_get_returns_stored_session() -> None:
    store, _ = _make_store()
    original = _session_data()
    sid = await store.create(original)
    got = await store.get(sid)
    assert got == original


@pytest.mark.asyncio
async def test_get_missing_session_returns_none() -> None:
    store, _ = _make_store()
    assert await store.get("nonexistent") is None


@pytest.mark.asyncio
async def test_get_drops_session_past_absolute_lifetime() -> None:
    store, fake = _make_store(ttl=10)
    sid = await store.create(_session_data())
    # Manually rewind created_at to be older than ttl
    raw_key = f"sess:{sid}"
    raw, _ = fake.store[raw_key]
    payload = json.loads(raw)
    payload["created_at"] = int(time.time()) - 100
    fake.store[raw_key] = (json.dumps(payload), time.time() + 3600)

    assert await store.get(sid) is None
    # Confirm get() actually deleted the stale row
    assert raw_key not in fake.store


@pytest.mark.asyncio
async def test_corrupt_payload_is_dropped() -> None:
    store, fake = _make_store()
    sid = "fakesid"
    fake.store[f"sess:{sid}"] = ("not-json-at-all", time.time() + 100)
    assert await store.get(sid) is None
    assert f"sess:{sid}" not in fake.store


@pytest.mark.asyncio
async def test_touch_extends_idle_ttl() -> None:
    store, fake = _make_store(idle=60)
    sid = await store.create(_session_data())
    before = fake.store[f"sess:{sid}"][1]
    await store.touch(sid)
    after = fake.store[f"sess:{sid}"][1]
    assert after >= before


@pytest.mark.asyncio
async def test_update_replaces_payload() -> None:
    store, _ = _make_store()
    sid = await store.create(_session_data())
    new_data = SessionData(
        subject="user-1",
        access_token="NEW-AT",
        refresh_token="NEW-RT",
        id_token="NEW-IT",
        access_expires_at=int(time.time()) + 600,
        created_at=int(time.time()),
    )
    await store.update(sid, new_data)
    got = await store.get(sid)
    assert got is not None
    assert got.access_token == "NEW-AT"


@pytest.mark.asyncio
async def test_delete_removes_session() -> None:
    store, _ = _make_store()
    sid = await store.create(_session_data())
    await store.delete(sid)
    assert await store.get(sid) is None


# ---------------------------------------------------------------------------
# Login state (single-use)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_state_round_trip() -> None:
    store, _ = _make_store()
    ls = LoginState(pkce_verifier="VER", redirect_uri="http://cb", return_to="/tasks")
    await store.save_login_state("state-1", ls)
    popped = await store.pop_login_state("state-1")
    assert popped == ls


@pytest.mark.asyncio
async def test_login_state_is_single_use() -> None:
    """pop must consume — replay attacks must fail."""
    store, _ = _make_store()
    ls = LoginState(pkce_verifier="VER", redirect_uri="http://cb", return_to="/")
    await store.save_login_state("state-1", ls)
    first = await store.pop_login_state("state-1")
    second = await store.pop_login_state("state-1")
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_pop_unknown_state_returns_none() -> None:
    store, _ = _make_store()
    assert await store.pop_login_state("never-set") is None


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tokens_are_encrypted_at_rest_and_round_trip() -> None:
    from app.core.crypto import TokenCipher, generate_key

    # Distinctive markers: random base64 ciphertext won't contain these verbatim.
    marker = "ACCESS-TOKEN-PLAINTEXT-MARKER"
    refresh_marker = "REFRESH-TOKEN-PLAINTEXT-MARKER"
    now = int(time.time())
    data = SessionData(
        subject="user-1",
        access_token=marker,
        refresh_token=refresh_marker,
        id_token="IT",
        access_expires_at=now + 300,
        created_at=now,
    )
    cipher = TokenCipher.from_raw_keys([generate_key()])
    store, fake = _make_store(cipher=cipher)
    sid = await store.create(data)

    raw, _ = fake.store[f"sess:{sid}"]
    # The raw Redis value must NOT expose the token plaintext or be JSON.
    assert marker not in raw and refresh_marker not in raw
    assert not raw.lstrip().startswith("{")

    got = await store.get(sid)
    assert got is not None
    assert got.access_token == marker and got.refresh_token == refresh_marker


@pytest.mark.asyncio
async def test_legacy_plaintext_row_is_still_readable_after_enabling_encryption() -> None:
    """A row written before encryption (plaintext JSON) must decode on read."""
    from app.core.crypto import TokenCipher, generate_key

    plain_store, fake = _make_store()  # no cipher → writes plaintext JSON
    sid = await plain_store.create(_session_data())

    # Re-bind the SAME fake redis to a store that now has a cipher.
    enc_store = SessionStore.__new__(SessionStore)
    enc_store._redis = fake  # type: ignore[attr-defined]
    enc_store._ttl = 3600  # type: ignore[attr-defined]
    enc_store._idle = 1800  # type: ignore[attr-defined]
    enc_store._cipher = TokenCipher.from_raw_keys([generate_key()])  # type: ignore[attr-defined]

    got = await enc_store.get(sid)
    assert got is not None and got.access_token == "AT"


@pytest.mark.asyncio
async def test_login_state_is_encrypted_at_rest() -> None:
    from app.core.crypto import TokenCipher, generate_key

    cipher = TokenCipher.from_raw_keys([generate_key()])
    store, fake = _make_store(cipher=cipher)
    ls = LoginState(pkce_verifier="VERIFIER", redirect_uri="http://cb", return_to="/tasks")
    await store.save_login_state("state-1", ls)

    raw, _ = fake.store["login:state-1"]
    assert "VERIFIER" not in raw
    assert await store.pop_login_state("state-1") == ls
