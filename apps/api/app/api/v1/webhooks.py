"""Brevo transactional email webhook endpoint for delivery and failure events (T-09)."""

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models.email_outbox import EmailOutbox
from app.db.session import get_session
from app.schemas.email import BrevoWebhookResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Brevo terminal failure events that mark outbox record as 'failed'
BREVO_FAILURE_EVENTS = frozenset(
    {
        "bounce",
        "hard_bounce",
        "soft_bounce",
        "blocked",
        "spam",
        "invalid_email",
        "error",
    }
)


def verify_webhook_secret(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    token: str | None = Query(default=None),
    secret: str | None = Query(default=None),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    x_brevo_webhook_secret: str | None = Header(default=None, alias="X-Brevo-Webhook-Secret"),
    authorization: str | None = Header(default=None),
) -> None:
    """Verify incoming webhook authenticity using a secret token.

    Accepts secret token from:
    1. Query param: `?token=...` or `?secret=...`
    2. Header: `X-Webhook-Secret` or `X-Brevo-Webhook-Secret`
    3. Header: `Authorization: Bearer <secret>`

    Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks.
    """
    active_settings = getattr(request.app.state, "settings", None) or settings
    configured_secret = active_settings.brevo_webhook_secret

    # Determine supplied token
    candidate_token: str | None = None
    if x_webhook_secret:
        candidate_token = x_webhook_secret.strip()
    elif x_brevo_webhook_secret:
        candidate_token = x_brevo_webhook_secret.strip()
    elif authorization and authorization.startswith("Bearer "):
        candidate_token = authorization.split("Bearer ", 1)[1].strip()
    elif token:
        candidate_token = token.strip()
    elif secret:
        candidate_token = secret.strip()

    if configured_secret and configured_secret.strip():
        expected = configured_secret.strip()
        if not candidate_token or not hmac.compare_digest(candidate_token, expected):
            logger.warning("Rejected Brevo webhook request with invalid or missing secret")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="INVALID_WEBHOOK_SECRET",
            )
    else:
        # If no secret is configured on the server:
        if settings.app_env in ("production", "staging"):
            logger.error(
                "Brevo webhook received but BREVO_WEBHOOK_SECRET is not configured in %s",
                settings.app_env,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEBHOOK_SECRET_NOT_CONFIGURED",
            )
        # In development/test environments, allow if not configured
        if candidate_token:
            logger.info("Brevo webhook received token in dev mode: %s", candidate_token[:4] + "***")


@router.post(
    "/brevo",
    response_model=BrevoWebhookResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_webhook_secret)],
    summary="Brevo transactional email delivery event webhook",
)
async def brevo_webhook_handler(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> BrevoWebhookResponse:
    """Receive Brevo transactional email delivery and failure events.

    Updates matching email_outbox rows by provider_message_id:
    - 'delivered' -> transitions outbox status to 'delivered' and sets delivered_at
    - 'bounce', 'blocked', 'spam', 'invalid_email', 'error' -> transitions outbox status to 'failed'
    Returns HTTP 200 quickly.
    """
    try:
        raw_body = await request.json()
    except Exception as exc:
        logger.warning("Invalid JSON body received in Brevo webhook: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_JSON_BODY",
        ) from exc

    # Normalize single event or list of events
    events: list[dict[str, Any]]
    if isinstance(raw_body, list):
        events = [e for e in raw_body if isinstance(e, dict)]
    elif isinstance(raw_body, dict):
        events = [raw_body]
    else:
        return BrevoWebhookResponse(status="ok", processed=0)

    processed_count = 0
    now = datetime.now(UTC)

    for ev in events:
        event_name = str(ev.get("event") or "").strip().lower()
        raw_msg_id = ev.get("message-id") or ev.get("message_id") or ev.get("messageId")

        if not raw_msg_id or not event_name:
            continue

        raw_msg_id_str = str(raw_msg_id).strip()
        clean_msg_id = raw_msg_id_str.strip("<>")
        bracketed_msg_id = f"<{clean_msg_id}>"

        # Parse event timestamp if available
        event_dt = now
        date_str = ev.get("date")
        if date_str and isinstance(date_str, str):
            try:
                event_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                event_dt = now

        # Find matching outbox item by provider_message_id
        stmt = select(EmailOutbox).where(
            or_(
                EmailOutbox.provider_message_id == raw_msg_id_str,
                EmailOutbox.provider_message_id == clean_msg_id,
                EmailOutbox.provider_message_id == bracketed_msg_id,
            )
        )
        result = await db.execute(stmt)
        outbox_item = result.scalars().first()

        if not outbox_item:
            logger.info(
                "Brevo webhook received for unknown messageId: %s (event: %s)",
                raw_msg_id_str,
                event_name,
            )
            continue

        if event_name == "delivered":
            outbox_item.status = "delivered"
            outbox_item.delivered_at = event_dt
            outbox_item.last_error = None
            processed_count += 1
            logger.info("Outbox item %s marked 'delivered' by Brevo webhook", outbox_item.id)

        elif event_name in BREVO_FAILURE_EVENTS:
            reason = ev.get("reason") or ev.get("description") or f"Event: {event_name}"
            outbox_item.status = "failed"
            outbox_item.failed_at = event_dt
            outbox_item.last_error = f"Brevo delivery failure: {event_name} ({reason})"
            processed_count += 1
            logger.warning(
                "Outbox item %s marked 'failed' by Brevo webhook (%s: %s)",
                outbox_item.id,
                event_name,
                reason,
            )

    if processed_count > 0:
        await db.commit()

    return BrevoWebhookResponse(status="ok", processed=processed_count)
