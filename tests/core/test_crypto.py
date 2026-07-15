"""Unit tests for the at-rest token cipher (AES-256-GCM + key rotation)."""

from __future__ import annotations

import base64

import pytest

from app.core.crypto import TokenCipher, TokenCipherError, generate_key


def test_generate_key_is_32_bytes_base64() -> None:
    key = generate_key()
    assert len(base64.urlsafe_b64decode(key)) == 32


def test_round_trip() -> None:
    cipher = TokenCipher.from_raw_keys([generate_key()])
    secret = "eyJhbGciOi.payload.sig"
    assert cipher.decrypt(cipher.encrypt(secret)) == secret


def test_ciphertext_is_not_plaintext_and_nondeterministic() -> None:
    cipher = TokenCipher.from_raw_keys([generate_key()])
    a = cipher.encrypt("token")
    b = cipher.encrypt("token")
    assert "token" not in a
    assert a != b  # random nonce per encryption


def test_key_rotation_old_ciphertext_still_decrypts() -> None:
    old, new = generate_key(), generate_key()
    written = TokenCipher.from_raw_keys([old]).encrypt("legacy")
    # New primary key first, old retained as fallback.
    rotated = TokenCipher.from_raw_keys([new, old])
    assert rotated.decrypt(written) == "legacy"
    # New encryptions use the new primary and are NOT decryptable by old-only.
    fresh = rotated.encrypt("fresh")
    with pytest.raises(TokenCipherError):
        TokenCipher.from_raw_keys([old]).decrypt(fresh)


def test_tampered_ciphertext_is_rejected() -> None:
    cipher = TokenCipher.from_raw_keys([generate_key()])
    token = cipher.encrypt("token")
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(TokenCipherError):
        cipher.decrypt(tampered)


def test_unknown_key_cannot_decrypt() -> None:
    written = TokenCipher.from_raw_keys([generate_key()]).encrypt("token")
    with pytest.raises(TokenCipherError):
        TokenCipher.from_raw_keys([generate_key()]).decrypt(written)


def test_bad_key_length_rejected() -> None:
    short = base64.urlsafe_b64encode(b"too-short").decode()
    with pytest.raises(ValueError):
        TokenCipher.from_raw_keys([short])


def test_empty_keys_rejected() -> None:
    with pytest.raises(ValueError):
        TokenCipher([])
