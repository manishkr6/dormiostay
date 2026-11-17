"""Health and readiness endpoints."""

import asyncio

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings

router = APIRouter()


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def check_database(settings: Settings) -> None:
    connection = await asyncio.wait_for(
        asyncpg.connect(_asyncpg_url(settings.database_url)),
        timeout=settings.database_connect_timeout_seconds,
    )
    await connection.close()


@router.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Liveness check with no database or external dependency."""

    return {"status": "ok"}


@router.get("/readyz", tags=["health"], response_model=None)
async def readyz(request: Request) -> JSONResponse | dict[str, str]:
    """Readiness check that verifies the configured database is reachable."""

    settings: Settings = request.app.state.settings
    try:
        await check_database(settings)
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}
