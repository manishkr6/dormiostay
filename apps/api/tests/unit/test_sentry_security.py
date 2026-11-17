from unittest.mock import patch

from app.core.config import Settings
from app.core.sentry import initialise_sentry, sanitize_event


def test_sentry_initialization_installs_privacy_boundary() -> None:
    settings = Settings.model_validate(
        {
            "sentry_dsn": "https://public@example.invalid/1",
        }
    )
    with patch("app.core.sentry.sentry_sdk.init") as init:
        initialise_sentry(settings)
    assert init.call_args.kwargs["before_send"] is sanitize_event
    assert init.call_args.kwargs["send_default_pii"] is False
    assert init.call_args.kwargs["default_integrations"] is False
    assert init.call_args.kwargs["include_local_variables"] is False
    assert init.call_args.kwargs["max_request_body_size"] == "never"
