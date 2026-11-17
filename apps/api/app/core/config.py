"""Validated server-side configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnvironment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Environment-backed settings with fail-fast required infrastructure values."""

    model_config = SettingsConfigDict(
        env_file=(".env", "apps/api/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnvironment = Field(..., description="Runtime environment")
    database_url: str = Field(..., min_length=1, description="Pooled application database URL")
    frontend_origin: AnyHttpUrl = Field(..., description="Exact browser origin allowed by CORS")
    sentry_dsn: str | None = Field(default=None, min_length=1)
    request_id_header: str = Field(default="X-Request-ID", min_length=1)
    database_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once; validation errors stop application startup."""

    return Settings.model_validate({})
