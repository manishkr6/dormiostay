"""Offline Render staging metadata check. Accept only password-free inventory fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.migration_guard import APPROVED_STAGING_ENDPOINT_ID, APPROVED_STAGING_HOST
from app.db.session import verify_runtime_role


class RenderInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    app_env: Literal["staging"]
    role: Literal["dormiostay_app"]
    database: Literal["neondb"]
    port: Literal[5432]
    sslmode: Literal["verify-full"]
    host: str
    auth_endpoints_enabled: bool
    worker_enabled: bool
    api_proxy_enabled: bool
    migration_credentials_present: bool

    def check(self) -> None:
        expected_pool = APPROVED_STAGING_HOST.replace(
            APPROVED_STAGING_ENDPOINT_ID + ".", APPROVED_STAGING_ENDPOINT_ID + "-pooler.", 1
        )
        if self.host != expected_pool or any(
            (
                self.auth_endpoints_enabled,
                self.worker_enabled,
                self.api_proxy_enabled,
                self.migration_credentials_present,
            )
        ):
            raise ValueError("Staging runtime inventory rejected")


async def verify_runtime_session(session: AsyncSession) -> None:
    """Validate a supplied read-only runtime session during an authorized future inspection."""
    row = (
        await session.execute(
            text(
                "SELECT pg_catalog.current_database(),current_user,session_user,"
                "pg_catalog.current_setting('transaction_read_only')"
            )
        )
    ).one()
    if tuple(row) != ("neondb", "dormiostay_app", "dormiostay_app", "on"):
        raise RuntimeError("Unexpected Render staging database session")
    await verify_runtime_role(session)
