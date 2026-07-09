"""Redis-backed server-side session store.

Session model:
  - session_id  — 32-byte cryptographically random URL-safe token, set in
                  HttpOnly cookie. THIS IS the secret: anyone who has the
                  cookie can act as the user until session_idle_seconds
                  passes without a request.
  - payload     — stored as a Redis Hash at `sess:{session_id}`:
                    access_token, refresh_token, id_token, subject, expires_at
  - TTL         — Redis key TTL = session_idle_seconds. Each request bumps it
                  (sliding idle timeout). A separate `created_at` field caps
                  the absolute lifetime.

Login flow state (the pre-callback bag of nonce + PKCE verifier) is stored
in the same Redis under `login:{state}` with a short TTL.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.core.crypto import TokenCipher, TokenCipherError

logger = structlog.get_logger(__name__)

_SESSION_KEY_PREFIX = "sess:"
_LOGIN_KEY_PREFIX = "login:"
_LOGIN_TTL_SECONDS = 300  # 5 min — Keycloak login round-trip should fit


@dataclass(slots=True)
class SessionData:
    """Server-side state stored against a session cookie."""

    subject: str  # Keycloak `sub` claim, also our user UUID
    access_token: str
    refresh_token: str | None
    id_token: str | None
    access_expires_at: int  # unix seconds — when the access_token expires
    created_at: int  # unix seconds — for absolute-max lifetime check


@dataclass(slots=True)
class LoginState:
    """Transient state for the OIDC redirect round-trip."""

    pkce_verifier: str
    redirect_uri: str
    return_to: str  # frontend path to bounce back to after login


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


class SessionStore:
    def __init__(
        self,
        redis_url: str,
        *,
        ttl_seconds: int,
        idle_seconds: int,
        cipher: TokenCipher | None = None,
        _redis: aioredis.Redis | None = None,
    ) -> None:
        self._redis: aioredis.Redis = _redis or aioredis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds
        self._idle = idle_seconds
        # When set, token-bearing payloads are AES-256-GCM encrypted at rest.
        self._cipher = cipher

    # ------------------------------------------------------------------
    # Serialization (encrypt-at-rest when a cipher is configured)
    # ------------------------------------------------------------------

    def _dumps(self, payload: dict[str, Any]) -> str:
        blob = json.dumps(payload)
        return self._cipher.encrypt(blob) if self._cipher is not None else blob

    def _loads(self, raw: str | bytes) -> dict[str, Any] | None:
        """Decrypt+parse a stored value, tolerating legacy plaintext rows.

        Returns None on unrecoverable corruption (caller drops the key).
        """
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        blob = raw
        if self._cipher is not None:
            try:
                blob = self._cipher.decrypt(raw)
            except TokenCipherError:
                # Migration path: a row written before encryption was enabled is
                # plaintext JSON. Anything else is genuinely undecryptable.
                if not raw.lstrip().startswith("{"):
                    return None
                blob = raw
        try:
            return json.loads(blob)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create(self, data: SessionData) -> str:
        """Create a new session and return its id."""
        sid = generate_session_id()
        await self._redis.set(
            _SESSION_KEY_PREFIX + sid,
            self._dumps(asdict(data)),
            ex=self._idle,
        )
        return sid

    async def get(self, sid: str) -> SessionData | None:
        raw = await self._redis.get(_SESSION_KEY_PREFIX + sid)
        if raw is None:
            return None
        payload = self._loads(raw)
        if payload is None:
            logger.warning("session.corrupt_payload", sid_prefix=sid[:6])
            await self.delete(sid)
            return None

        # Enforce absolute maximum lifetime
        if payload.get("created_at", 0) + self._ttl < int(time.time()):
            await self.delete(sid)
            return None

        return SessionData(**payload)

    async def touch(self, sid: str) -> None:
        """Bump the idle-timeout TTL on a sliding-window basis."""
        await self._redis.expire(_SESSION_KEY_PREFIX + sid, self._idle)

    async def update(self, sid: str, data: SessionData) -> None:
        """Overwrite session payload (used after token refresh). Resets idle TTL."""
        await self._redis.set(
            _SESSION_KEY_PREFIX + sid,
            self._dumps(asdict(data)),
            ex=self._idle,
        )

    async def delete(self, sid: str) -> None:
        await self._redis.delete(_SESSION_KEY_PREFIX + sid)

    # ------------------------------------------------------------------
    # Login state (pre-callback OAuth scratchpad)
    # ------------------------------------------------------------------

    async def save_login_state(self, state: str, login: LoginState) -> None:
        await self._redis.set(
            _LOGIN_KEY_PREFIX + state,
            self._dumps(asdict(login)),
            ex=_LOGIN_TTL_SECONDS,
        )

    async def pop_login_state(self, state: str) -> LoginState | None:
        """Atomically fetch-and-delete login state. Single-use to prevent replay."""
        key = _LOGIN_KEY_PREFIX + state
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = await pipe.execute()
        if raw is None:
            return None
        payload = self._loads(raw)
        if payload is None:
            return None
        try:
            return LoginState(**payload)
        except TypeError:
            return None

    async def aclose(self) -> None:
        await self._redis.aclose()
