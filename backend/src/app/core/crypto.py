"""Authenticated encryption for tokens at rest (AES-256-GCM with key rotation).

Session payloads in Redis carry live OIDC tokens (access / refresh / id). At rest
those ARE the real bearer credentials, so we wrap them with AES-256-GCM before they
ever touch Redis. A leaked RDB/AOF snapshot, a misconfigured replica, or an SSRF
that reaches Redis then yields ciphertext — not directly replayable tokens.

This is a defense-in-depth layer; the primary boundary remains the opaque HttpOnly
session cookie + server-side storage. Its value is in secondary-compromise scenarios
(captured backups, replicas, insider `redis-cli`) where the KEK lives outside Redis.

Key management:
  - Keys come from settings (SESSION_ENCRYPTION_KEYS), each a base64-encoded 32-byte key.
  - The first key is the *primary* — used for every new encryption.
  - All keys are tried on decrypt (newest-first), so a key is rotated by prepending a
    new one and keeping the old until existing sessions age out (≤ session TTL).

Wire format (base64url):  version(1B) || nonce(12B) || ciphertext+GCM-tag
"""

from __future__ import annotations

import base64
import binascii
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = 1
_NONCE_LEN = 12
_TAG_LEN = 16
_KEY_LEN = 32  # AES-256


class TokenCipherError(Exception):
    """Raised when a ciphertext cannot be decrypted with any configured key."""


def generate_key() -> str:
    """Return a fresh base64url-encoded 32-byte key (for SESSION_ENCRYPTION_KEYS).

    Usage::

        python -c "from app.core.crypto import generate_key; print(generate_key())"
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(_KEY_LEN)).decode("ascii")


def _decode_key(raw: str) -> bytes:
    raw = raw.strip()
    padded = raw + "=" * (-len(raw) % 4)
    try:
        if "-" in raw or "_" in raw:
            key = base64.urlsafe_b64decode(padded)
        else:
            key = base64.b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("SESSION_ENCRYPTION_KEYS entry is not valid base64") from exc
    if len(key) != _KEY_LEN:
        raise ValueError(
            f"Encryption key must decode to {_KEY_LEN} bytes (got {len(key)}). "
            "Generate one with app.core.crypto.generate_key()."
        )
    return key


class TokenCipher:
    """AES-256-GCM authenticated encryption with newest-first key rotation."""

    __slots__ = ("_ciphers",)

    def __init__(self, keys: list[bytes]) -> None:
        if not keys:
            raise ValueError("TokenCipher requires at least one key")
        self._ciphers = [AESGCM(k) for k in keys]

    @classmethod
    def from_raw_keys(cls, raw_keys: list[str]) -> TokenCipher:
        """Build from base64-encoded key strings (settings.SESSION_ENCRYPTION_KEYS)."""
        return cls([_decode_key(k) for k in raw_keys])

    def encrypt(self, plaintext: str) -> str:
        nonce = secrets.token_bytes(_NONCE_LEN)
        ct = self._ciphers[0].encrypt(nonce, plaintext.encode("utf-8"), None)
        blob = bytes([_VERSION]) + nonce + ct
        return base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            blob = base64.urlsafe_b64decode(token.encode("ascii"))
        except (binascii.Error, ValueError) as exc:
            raise TokenCipherError("ciphertext is not valid base64url") from exc
        if len(blob) < 1 + _NONCE_LEN + _TAG_LEN or blob[0] != _VERSION:
            raise TokenCipherError("ciphertext has an unexpected header or length")
        nonce = blob[1 : 1 + _NONCE_LEN]
        ct = blob[1 + _NONCE_LEN :]
        for cipher in self._ciphers:
            try:
                return cipher.decrypt(nonce, ct, None).decode("utf-8")
            except InvalidTag:
                continue
        raise TokenCipherError("no configured key could decrypt the ciphertext")
