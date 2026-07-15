"""Tests for security-relevant Settings behavior: CSV parsing, prod gates, cookies."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core.crypto import generate_key

_BASE = dict(
    database_url="postgresql+asyncpg://app:app@db:5432/app",
    keycloak_audience="bap-backend",
    keycloak_issuer="https://kc/realms/bap",
)


def _prod(**overrides: object) -> Settings:
    cfg: dict[str, object] = dict(
        app_env="prod",
        app_debug=False,
        redis_url="rediss://:pw@redis:6379/0",
        public_base_url="https://api.x",
        frontend_base_url="https://fe.x",
        oidc_client_secret="a-real-secret",
        cors_origins=["https://fe.x"],
        trusted_hosts=["api.x"],
        session_encryption_keys=[generate_key()],
        keycloak_public_issuer="https://kc/realms/bap",
        **_BASE,
    )
    cfg.update(overrides)
    return Settings(**cfg)  # type: ignore[arg-type]


def test_comma_separated_lists_parse_from_strings() -> None:
    # This is the format .env.example / docker-compose actually ship.
    s = Settings(
        cors_origins="https://a.x,https://b.x",  # type: ignore[arg-type]
        trusted_hosts="a.x, b.x",  # type: ignore[arg-type]
        **_BASE,
        redis_url="redis://r:6379/0",
    )
    assert [str(o).rstrip("/") for o in s.cors_origins] == ["https://a.x", "https://b.x"]
    assert s.trusted_hosts == ["a.x", "b.x"]


def test_valid_prod_config_passes() -> None:
    _prod().validate_for_production()  # must not raise


@pytest.mark.parametrize(
    "override, needle",
    [
        ({"frontend_base_url": "http://fe.x"}, "FRONTEND_BASE_URL"),
        ({"trusted_hosts": []}, "TRUSTED_HOSTS"),
        ({"redis_url": "redis://redis:6379/0"}, "REDIS_URL"),
        ({"session_encryption_keys": []}, "SESSION_ENCRYPTION_KEYS"),
        ({"oidc_client_secret": "change-me-in-prod"}, "OIDC_CLIENT_SECRET"),
        ({"public_base_url": "http://api.x"}, "PUBLIC_BASE_URL"),
        ({"keycloak_public_issuer": None}, "KEYCLOAK_PUBLIC_ISSUER"),
    ],
)
def test_prod_validation_flags_each_gap(override: dict[str, object], needle: str) -> None:
    with pytest.raises(ValueError) as exc:
        _prod(**override).validate_for_production()
    assert needle in str(exc.value)


def test_redis_with_password_is_accepted() -> None:
    _prod(redis_url="redis://:strongpw@redis:6379/0").validate_for_production()


def test_cookie_name_and_secure_track_scheme() -> None:
    https = Settings(public_base_url="https://api.x", redis_url="redis://r:6379/0", **_BASE)
    assert https.cookies_secure is True
    assert https.session_cookie_effective_name == "__Host-bap_session"

    http = Settings(public_base_url="http://localhost:8000", redis_url="redis://r:6379/0", **_BASE)
    assert http.cookies_secure is False
    assert http.session_cookie_effective_name == "bap_session"


def test_csrf_allowed_origins_includes_frontend_and_cors() -> None:
    s = Settings(
        frontend_base_url="https://fe.x",
        public_base_url="https://api.x",
        cors_origins="https://extra.x",  # type: ignore[arg-type]
        redis_url="redis://r:6379/0",
        **_BASE,
    )
    assert s.csrf_allowed_origins == frozenset({"https://fe.x", "https://api.x", "https://extra.x"})


def test_local_env_skips_validation() -> None:
    # Local dev: plaintext redis, no keys, http — must NOT raise.
    Settings(redis_url="redis://r:6379/0", **_BASE).validate_for_production()
