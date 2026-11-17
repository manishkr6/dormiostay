"""Optional Sentry initialisation."""

import sentry_sdk

from app.core.config import Settings


def initialise_sentry(settings: Settings) -> None:
    """Initialise Sentry only when a server-side DSN is configured."""

    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
