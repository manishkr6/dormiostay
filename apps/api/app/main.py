"""FastAPI application factory and T-02 runtime wiring."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.router import router as api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.core.sentry import initialise_sentry
from app.db.session import check_runtime_database


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    try:
        await asyncio.wait_for(
            check_runtime_database(application.state.settings.database_url),
            timeout=application.state.settings.database_connect_timeout_seconds,
        )
    except Exception:
        raise RuntimeError("DATABASE_STARTUP_CHECK_FAILED") from None
    yield
    if hasattr(application.state, "database_engine"):
        await application.state.database_engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the configured API application."""

    configure_logging()
    resolved_settings = settings or get_settings()
    initialise_sentry(resolved_settings)

    application = FastAPI(title="DormioStay API", version="0.1.0", lifespan=lifespan)
    application.state.settings = resolved_settings
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[str(resolved_settings.frontend_origin).rstrip("/")],
        allow_credentials=True,
        allow_methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Accept", "Authorization", "Content-Type", "Origin", "X-Request-ID"],
    )
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
