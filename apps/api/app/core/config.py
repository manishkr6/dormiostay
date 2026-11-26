"""Validated server-side configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, model_validator
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once; validation errors stop application startup."""

    return Settings.model_validate({})
