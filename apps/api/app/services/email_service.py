"""Email service for outbox queueing, Brevo API integration, and dev mode simulation (T-09)."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.email_outbox import EmailOutbox

logger = logging.getLogger(__name__)


class BrevoError(Exception):
    """Base exception for Brevo transactional email operations."""


class BrevoPermanentError(BrevoError):
    """Permanent error (e.g. HTTP 400 invalid payload, bad email address); do not retry."""


class BrevoAuthError(BrevoError):
    """Authentication error (e.g. HTTP 401 invalid API key); alert administrator."""


class BrevoTransientError(BrevoError):
    """Transient error (e.g. HTTP 429 rate limit, 5xx server error, network timeout); retry."""


def queue_email(
    session: AsyncSession,
    recipient: str,
    subject: str,
    html_content: str,
    text_content: str,
    recipient_name: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    send_after: datetime | None = None,
    max_attempts: int = 5,
) -> EmailOutbox:
    """Queue an email in the transactional outbox table.

    Does NOT commit the session so that email queueing is atomically bound to the
    enclosing business database transaction (e.g. signup, password reset).
    """
    clean_recipient = recipient.strip().lower()
    item = EmailOutbox(
        recipient=clean_recipient,
        recipient_name=recipient_name.strip() if recipient_name else None,
        subject=subject.strip(),
        html_content=html_content,
        text_content=text_content,
        tags=tags or [],
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        last_error=None,
        send_after=send_after or datetime.now(UTC),
        sent_at=None,
        delivered_at=None,
        failed_at=None,
        provider_message_id=None,
        metadata_payload=metadata,
    )
    session.add(item)
    return item


async def send_outbox_email(
    item: EmailOutbox,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Send an outbox email record via Brevo or simulated development dispatch.

    Returns the provider message ID.
    Updates outbox record fields in-place: status, provider_message_id, sent_at, last_error.

    Rules:
    - If in development mode (or no verified sender / api key is configured):
      Simulates dispatch, records admin-visible status, transitions to 'accepted_by_provider'.
      NEVER transitions to 'delivered' in dev mode without provider webhook confirmation.
    - If in live mode:
      Calls Brevo API POST /v3/smtp/email.
      201 Created -> transitions to 'accepted_by_provider' with messageId.
      400 Bad Request -> raises BrevoPermanentError.
      401 Unauthorized -> raises BrevoAuthError.
      429 / 5xx / Network -> raises BrevoTransientError.
    """
    now = datetime.now(UTC)

    # 1. Development Mode Simulation Fallback
    if settings.is_email_dev_mode:
        simulated_message_id = f"dev-{uuid4()}"
        dev_note = "[DEV MODE] Simulated dispatch - no verified sender or API key configured"
        logger.info(
            "Email dev mode dispatch: to=%s, subject=%s, tags=%s, sim_msg_id=%s",
            item.recipient,
            item.subject,
            item.tags,
            simulated_message_id,
        )
        item.status = "accepted_by_provider"
        item.provider_message_id = simulated_message_id
        item.sent_at = now
        item.last_error = dev_note
        return simulated_message_id

    # 2. Production / Live Brevo API Dispatch
    if not settings.brevo_api_key or not settings.brevo_api_key.strip():
        raise BrevoAuthError("BREVO_API_KEY is not configured for live email dispatch")

    if not settings.email_from_address or not settings.email_from_address.strip():
        raise BrevoPermanentError("EMAIL_FROM_ADDRESS is not configured for live email dispatch")

    headers = {
        "api-key": settings.brevo_api_key.strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "sender": {
            "name": settings.email_from_name,
            "email": settings.email_from_address.strip(),
        },
        "to": [
            {
                "email": item.recipient,
                "name": item.recipient_name or item.recipient,
            }
        ],
        "subject": item.subject,
        "htmlContent": item.html_content,
        "textContent": item.text_content,
        "tags": item.tags or [],
    }

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        own_client = True

    try:
        response = await client.post(
            settings.brevo_api_url,
            headers=headers,
            json=payload,
        )
    except httpx.RequestError as exc:
        raise BrevoTransientError(f"Brevo network connection error: {exc}") from exc
    finally:
        if own_client:
            await client.aclose()

    if response.status_code in (200, 201, 202):
        try:
            body = response.json()
            message_id = body.get("messageId") or str(uuid4())
        except Exception:
            message_id = str(uuid4())

        item.status = "accepted_by_provider"
        item.provider_message_id = str(message_id)
        item.sent_at = now
        item.last_error = None
        return str(message_id)

    if response.status_code == 400:
        error_msg = f"HTTP 400 Bad Request: {response.text}"
        logger.warning("Brevo permanent rejection for outbox item %s: %s", item.id, error_msg)
        raise BrevoPermanentError(error_msg)

    if response.status_code == 401:
        error_msg = f"HTTP 401 Unauthorized: {response.text}"
        logger.error("Brevo API key unauthorized for outbox item %s: %s", item.id, error_msg)
        raise BrevoAuthError(error_msg)

    if response.status_code == 429 or response.status_code >= 500:
        error_msg = f"HTTP {response.status_code}: {response.text}"
        logger.warning("Brevo transient failure for outbox item %s: %s", item.id, error_msg)
        raise BrevoTransientError(error_msg)

    # Any other 4xx client errors are treated as permanent
    error_msg = f"HTTP {response.status_code} Client Error: {response.text}"
    logger.warning("Brevo client error for outbox item %s: %s", item.id, error_msg)
    raise BrevoPermanentError(error_msg)
