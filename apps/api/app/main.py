"""FastAPI application factory and T-02 runtime wiring."""

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.router import router as api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.core.sentry import initialise_sentry


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the configured API application."""

    resolved_settings = settings or get_settings()
    configure_logging()
    initialise_sentry(resolved_settings)

    application = FastAPI(title="DormioStay API", version="0.1.0")
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
