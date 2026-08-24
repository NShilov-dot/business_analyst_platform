from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.crypto import TokenCipher

_WEAK_SECRETS = frozenset(
    {"", "change-me-in-prod", "changeme", "secret", "admin", "bap-backend-admin-dev-secret"}
)


def _split_csv(value: object) -> object:
    """Parse a comma-separated env string into a list.

    pydantic-settings JSON-decodes complex (list) fields from the environment by
    default, so ``CORS_ORIGINS=http://a,http://b`` would raise. Combined with the
    ``NoDecode`` annotation this validator accepts both a comma-separated string
    and a JSON array, which is what the .env.example / docker-compose actually ship.
    """
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):
            import json

            return json.loads(value)
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["local", "dev", "staging", "prod"] = "local"
    app_debug: bool = False
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: PostgresDsn
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_recycle_seconds: int = 1800
    database_statement_timeout_ms: int = 30_000

    redis_url: RedisDsn
    rate_limit_redis_url: RedisDsn | None = None
    keycloak_issuer: AnyHttpUrl
    keycloak_public_issuer: AnyHttpUrl | None = None
    keycloak_audience: str
    keycloak_jwks_cache_ttl: int = 3600
    keycloak_tenant_claim: str = "tenant_id"
    keycloak_roles_claim: str = "realm_access.roles"
    keycloak_leeway_seconds: int = 30
    keycloak_expected_token_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Bearer"]
    )
    keycloak_realm: str = "bap"
    keycloak_admin_client_id: str = "bap-backend-admin"
    keycloak_admin_client_secret: SecretStr = SecretStr("")
    oidc_client_id: str = "bap-backend"
    oidc_client_secret: SecretStr = SecretStr("change-me-in-prod")
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    frontend_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")
    session_cookie_name: str = "bap_session"
    session_ttl_seconds: int = 36_000
    session_idle_seconds: int = 1_800
    session_encryption_keys: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)
    rate_limit_global_per_minute: int = 120
    rate_limit_writes_per_minute: int = 30
    rate_limit_tenant_per_minute: int = 6_000
    rate_limit_auth_per_minute_per_ip: int = 20
    rate_limit_signup_per_hour_per_ip: int = 5
    rate_limit_signup_global_per_hour: int = 50
    signup_enabled: bool = True
    signup_require_email_verification: bool = False

    # AI intake (ai_structuring). Feature is fully disabled when the key is
    # unset — endpoints answer 503 LLM_UNAVAILABLE. NOTE: chat turns are sent
    # to the provider (PII egress) — see modules/ai_structuring/infrastructure.
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    openai_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")

    # Object storage (documents module). Feature is disabled when the access
    # key is unset — upload/download answer 503 OBJECT_STORE_UNAVAILABLE.
    # NOTE: document text is sent to OpenAI once at session start (PII egress,
    # same consideration as chat turns) — see modules/ai_structuring.
    s3_endpoint_url: AnyHttpUrl | None = None
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_region: str = "us-east-1"
    s3_bucket_prefix: str = "bap"

    # Voice transcription (ai_structuring). TEMPORARY Modal-hosted pilot — raw
    # voice audio (PII) egresses to Modal; audio is never persisted anywhere.
    transcription_url: AnyHttpUrl | None = None
    transcription_modal_key: SecretStr = SecretStr("")
    transcription_modal_secret: SecretStr = SecretStr("")

    max_body_size_bytes: int = 1_048_576
    trusted_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    cors_origins: Annotated[list[AnyHttpUrl], NoDecode] = Field(default_factory=list)

    _csv_fields = field_validator(
        "trusted_hosts",
        "cors_origins",
        "session_encryption_keys",
        "keycloak_expected_token_types",
        mode="before",
    )(_split_csv)

    @field_validator("transcription_url", mode="before")
    @classmethod
    def _empty_transcription_url_is_unset(cls, value: object) -> object:
        """An empty/whitespace string means "disabled", same as the scaffolded
        .env.example ships (`TRANSCRIPTION_URL=`) — pydantic-settings does not
        treat an empty env value as unset, so without this it fails AnyHttpUrl
        validation instead of degrading to the feature-disabled 503."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def keycloak_admin_enabled(self) -> bool:
        return bool(self.keycloak_admin_client_secret.get_secret_value())

    @property
    def ai_intake_enabled(self) -> bool:
        return bool(self.openai_api_key.get_secret_value())

    @property
    def s3_enabled(self) -> bool:
        return bool(self.s3_access_key.get_secret_value())

    @property
    def transcription_enabled(self) -> bool:
        return bool(
            self.transcription_url
            and self.transcription_modal_key.get_secret_value()
            and self.transcription_modal_secret.get_secret_value()
        )

    @property
    def keycloak_public_issuer_effective(self) -> str:
        """Browser-facing issuer; falls back to the internal one when unset."""
        return str(self.keycloak_public_issuer or self.keycloak_issuer)

    @property
    def cookies_secure(self) -> bool:
        """Set the Secure cookie flag whenever the backend is served over HTTPS.

        Derived from public_base_url's scheme (not app_env) so that any HTTPS
        deployment — including dev/staging — gets Secure cookies, closing the
        MITM session-cookie interception gap on non-prod TLS deployments.
        """
        return str(self.public_base_url).lower().startswith("https")

    @property
    def session_cookie_effective_name(self) -> str:
        """`__Host-` prefix over HTTPS for the browser's strongest cookie guarantee.

        `__Host-` requires Secure + Path=/ + no Domain, all of which we satisfy.
        Over plain HTTP (local dev) the prefix is invalid, so we fall back to the
        bare name.
        """
        if self.cookies_secure:
            return f"__Host-{self.session_cookie_name}"
        return self.session_cookie_name

    @property
    def csrf_allowed_origins(self) -> frozenset[str]:
        """Origins accepted on state-changing requests (Origin/Referer allowlist)."""
        origins = {
            str(self.frontend_base_url).rstrip("/"),
            str(self.public_base_url).rstrip("/"),
        }
        origins.update(str(o).rstrip("/") for o in self.cors_origins)
        return frozenset(origins)

    @property
    def rate_limit_redis_url_effective(self) -> str:
        return str(self.rate_limit_redis_url or self.redis_url)

    def build_token_cipher(self) -> TokenCipher | None:
        """Build the at-rest token cipher, or None if no keys are configured."""
        raw = [k.get_secret_value() for k in self.session_encryption_keys]
        raw = [k for k in raw if k]
        if not raw:
            return None
        return TokenCipher.from_raw_keys(raw)

    def validate_for_production(self) -> None:
        """Raise ValueError if any mandatory production-safety constraint is violated."""
        if not self.is_prod:
            return
        errors: list[str] = []
        if self.app_debug:
            errors.append("APP_DEBUG must be False in production")
        if not self.cors_origins:
            errors.append("CORS_ORIGINS must be set in production")
        if str(self.keycloak_issuer).startswith("http://"):
            errors.append("KEYCLOAK_ISSUER must use HTTPS in production")
        if not self.keycloak_public_issuer:
            errors.append(
                "KEYCLOAK_PUBLIC_ISSUER must be set explicitly in production: tokens carry the "
                "public issuer as `iss`, and the internal-issuer fallback fails verify_token "
                "(silent auth outage off the docker-compose path, e.g. Kubernetes)"
            )
        if self.keycloak_public_issuer and str(self.keycloak_public_issuer).startswith("http://"):
            errors.append("KEYCLOAK_PUBLIC_ISSUER must use HTTPS in production")
        if str(self.public_base_url).startswith("http://"):
            errors.append("PUBLIC_BASE_URL must use HTTPS in production")
        if str(self.frontend_base_url).startswith("http://"):
            errors.append("FRONTEND_BASE_URL must use HTTPS in production")
        if self.oidc_client_secret.get_secret_value() in _WEAK_SECRETS:
            errors.append("OIDC_CLIENT_SECRET must be set to a real secret in production")
        if not self.trusted_hosts:
            errors.append("TRUSTED_HOSTS must be set in production (Host-header validation)")
        if not self._redis_is_authenticated(self.redis_url):
            errors.append(
                "REDIS_URL must use a password or TLS (rediss://) in production — "
                "Redis holds live access/refresh tokens"
            )
        if self.rate_limit_redis_url is not None and not self._redis_is_authenticated(
            self.rate_limit_redis_url
        ):
            errors.append(
                "RATE_LIMIT_REDIS_URL must use a password or TLS (rediss://) in production"
            )
        if not self.session_encryption_keys:
            errors.append(
                "SESSION_ENCRYPTION_KEYS must be set in production to encrypt tokens at rest"
            )
        else:
            # Surface bad key material at startup rather than on first request.
            try:
                self.build_token_cipher()
            except ValueError as exc:
                errors.append(f"SESSION_ENCRYPTION_KEYS invalid: {exc}")
        if (
            self.keycloak_admin_enabled
            and self.keycloak_admin_client_secret.get_secret_value() in _WEAK_SECRETS
        ):
            errors.append(
                "KEYCLOAK_ADMIN_CLIENT_SECRET must be a real secret when Admin is enabled"
            )
        if self.ai_intake_enabled and str(self.openai_base_url).startswith("http://"):
            errors.append("OPENAI_BASE_URL must use HTTPS in production")
        if self.s3_enabled:
            if self.s3_endpoint_url is not None and str(self.s3_endpoint_url).startswith(
                "http://"
            ):
                errors.append("S3_ENDPOINT_URL must use HTTPS in production")
            if self.s3_secret_key.get_secret_value() in _WEAK_SECRETS:
                errors.append("S3_SECRET_KEY must be set to a real secret in production")
        if self.transcription_url is not None:
            # Keyed on the URL being set (not `transcription_enabled`) so a
            # half-configured prod deploy fails fast instead of silently
            # running with transcription disabled.
            if str(self.transcription_url).startswith("http://"):
                errors.append("TRANSCRIPTION_URL must use HTTPS in production")
            if self.transcription_modal_key.get_secret_value() in _WEAK_SECRETS:
                errors.append("TRANSCRIPTION_MODAL_KEY must be set to a real secret in production")
            if self.transcription_modal_secret.get_secret_value() in _WEAK_SECRETS:
                errors.append(
                    "TRANSCRIPTION_MODAL_SECRET must be set to a real secret in production"
                )
        if errors:
            raise ValueError("Production configuration errors: " + "; ".join(errors))

    @staticmethod
    def _redis_is_authenticated(url: RedisDsn) -> bool:
        return url.scheme == "rediss" or bool(url.password)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
