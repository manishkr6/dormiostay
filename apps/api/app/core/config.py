"""Validated server-side configuration."""

from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnvironment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Environment-backed settings with fail-fast required infrastructure values."""

    model_config = SettingsConfigDict(
        env_file=(".env", "apps/api/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_env: AppEnvironment = Field(..., description="Runtime environment")
    database_url: str = Field(
        ..., min_length=1, repr=False, description="Pooled application database URL"
    )
    frontend_origin: AnyHttpUrl = Field(..., description="Exact browser origin allowed by CORS")
    sentry_dsn: str | None = Field(default=None, min_length=1)
    request_id_header: str = Field(default="X-Request-ID", min_length=1)
    database_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # T-06 Authentication & Session Configuration
    auth_endpoints_enabled: bool = Field(
        default=False,
        description="Activation guard: enable /api/v1/auth endpoints once T-07/T-08 are active",
    )
    session_cookie_name: str = Field(
        default="dormiostay_session",
        min_length=1,
        description="Name of the HttpOnly session cookie",
    )
    session_idle_timeout_minutes: int = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices("session_idle_timeout_minutes", "SESSION_IDLE_MINUTES"),
        description="Idle session timeout in minutes (Tenant/Owner)",
    )
    session_absolute_timeout_days: int = Field(
        default=14,
        gt=0,
        validation_alias=AliasChoices("session_absolute_timeout_days", "SESSION_ABSOLUTE_DAYS"),
        description="Absolute session timeout in days (Tenant/Owner)",
    )
    admin_session_idle_timeout_minutes: int = Field(
        default=15,
        gt=0,
        validation_alias=AliasChoices(
            "admin_session_idle_timeout_minutes", "ADMIN_SESSION_IDLE_MINUTES"
        ),
        description="Idle session timeout in minutes (Admin)",
    )
    admin_session_absolute_timeout_hours: int = Field(
        default=8, gt=0, description="Absolute session timeout in hours (Admin)"
    )

    # T-07 CSRF & Origin Protection Configuration
    csrf_protection_enabled: bool = Field(
        default=True,
        description="Enable CSRF and Origin protection on state-changing requests",
    )
    csrf_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("csrf_secret_key", "CSRF_SECRET", "CSRF_SECRET_KEY"),
        description="Secret key for signing CSRF tokens; derived from database URL if empty",
    )
    csrf_token_expire_seconds: int = Field(
        default=3600,
        gt=0,
        description="Expiration time in seconds for CSRF tokens",
    )

    # T-08 Rate Limiting & Abuse Prevention Configuration
    rate_limit_enabled: bool = Field(
        default=True,
        description="Master switch for rate limiting",
    )
    redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("redis_url", "REDIS_URL"),
        description="Redis connection URL (e.g. redis://127.0.0.1:6380/0)",
    )
    upstash_redis_rest_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("upstash_redis_rest_url", "UPSTASH_REDIS_REST_URL"),
        description="Upstash Redis REST API URL (alternative to redis_url)",
    )
    upstash_redis_rest_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("upstash_redis_rest_token", "UPSTASH_REDIS_REST_TOKEN"),
        repr=False,
        description="Upstash Redis REST API token",
    )
    rate_limit_connect_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=10.0,
        description="Redis operation timeout in seconds before falling back to in-memory limits",
    )
    trusted_proxies: list[str] = Field(
        default=["127.0.0.1", "::1", "testclient"],
        validation_alias=AliasChoices("trusted_proxies", "TRUSTED_PROXIES"),
        description="Trusted reverse proxy IPs or CIDR blocks for X-Forwarded-For resolution",
    )

    # T-09 Email Outbox & Brevo Configuration
    brevo_api_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("brevo_api_key", "BREVO_API_KEY"),
        description="Server-only Brevo transactional email credential",
    )
    brevo_webhook_secret: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("brevo_webhook_secret", "BREVO_WEBHOOK_SECRET"),
        description="Secret token for authenticating Brevo webhooks",
    )
    email_from_address: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email_from_address", "EMAIL_FROM_ADDRESS"),
        description="Verified email sender address",
    )
    email_from_name: str = Field(
        default="DormioStay",
        validation_alias=AliasChoices("email_from_name", "EMAIL_FROM_NAME"),
        description="Email sender display name",
    )
    brevo_api_url: str = Field(
        default="https://api.brevo.com/v3/smtp/email",
        validation_alias=AliasChoices("brevo_api_url", "BREVO_API_URL"),
        description="Brevo transactional email API URL",
    )
    email_worker_batch_size: int = Field(
        default=20,
        gt=0,
        le=100,
        validation_alias=AliasChoices("email_worker_batch_size", "EMAIL_WORKER_BATCH_SIZE"),
        description="Max emails processed per worker batch",
    )
    email_dev_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices("email_dev_mode", "EMAIL_DEV_MODE"),
        description="Explicit email development simulation mode",
    )

    @property
    def is_email_dev_mode(self) -> bool:
        """Dev mode is active if explicitly enabled or if no sender/key is set."""
        return bool(self.email_dev_mode or not (self.brevo_api_key and self.email_from_address))

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def parse_trusted_proxies(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(part).strip() for part in value if str(part).strip()]
        return ["127.0.0.1", "::1", "testclient"]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def validate_csrf_secret_for_production(self) -> "Settings":
        if self.auth_endpoints_enabled and self.app_env in ("production", "staging"):
            if not self.csrf_secret_key or not self.csrf_secret_key.strip():
                raise ValueError(
                    "CSRF_SECRET (csrf_secret_key) must be explicitly configured when "
                    "authentication endpoints are enabled in production or staging environments."
                )
        return self

    @model_validator(mode="after")
    def validate_email_credentials_for_production(self) -> "Settings":
        if self.auth_endpoints_enabled and self.app_env in ("production", "staging"):
            if not self.email_dev_mode and not (
                self.brevo_api_key
                and self.brevo_api_key.strip()
                and self.email_from_address
                and self.email_from_address.strip()
            ):
                raise ValueError(
                    "BREVO_API_KEY and EMAIL_FROM_ADDRESS must be explicitly configured "
                    "when authentication endpoints are enabled in production or staging, "
                    "unless EMAIL_DEV_MODE is explicitly enabled."
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once; validation errors stop application startup."""

    return Settings.model_validate({})
