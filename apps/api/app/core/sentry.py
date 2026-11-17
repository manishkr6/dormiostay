"""Optional Sentry initialisation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import sentry_sdk

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint

from app.core.config import Settings


def sanitize_event(event: Event, hint: Hint) -> Event:
    """Allowlist only safe diagnostics; discard exceptions, requests and breadcrumbs."""
    safe: Event = {
        "level": "error",
        "message": "Application error",
        "tags": {"code": "INTERNAL_SERVER_ERROR"},
    }
    request_id = event.get("tags", {}).get("request_id")
    if isinstance(request_id, str) and re.fullmatch(r"[0-9a-f-]{36}", request_id):
        safe["tags"] = {"code": "INTERNAL_SERVER_ERROR", "request_id": request_id}
    return safe


def initialise_sentry(settings: Settings) -> None:
    """Initialise Sentry only when a server-side DSN is configured."""

    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        send_default_pii=False,
        traces_sample_rate=0.0,
        default_integrations=False,
        auto_enabling_integrations=False,
        include_local_variables=False,
        max_request_body_size="never",
        before_send=sanitize_event,
    )
