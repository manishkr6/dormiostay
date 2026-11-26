"""Email service for outbox queueing, Brevo API integration, and dev mode simulation (T-09)."""

import inspect
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

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
    add_fn: Any = getattr(session, "add", None)
    if callable(add_fn):
        res: Any = add_fn(item)
        if inspect.iscoroutine(res):
            res.close()
    return item


def build_verification_email(
    recipient_name: str | None,
    action_link: str,
) -> tuple[str, str, str]:
    """Generate subject, HTML content, and plaintext content for email verification.

    Contract:
    - Subject: Verify your DormioStay email address
    - Action link uses verified HTTPS app-link routes with Web fallback.
    - Single-use action link, 24-hour expiration notice.
    - No session token or sensitive credential ever placed in email.
    """
    display_name = recipient_name.strip() if recipient_name and recipient_name.strip() else "there"
    subject = "Verify your DormioStay email address"

    text_content = f"""Hello {display_name},

Thank you for registering with DormioStay!

Please verify your email address by following this link:
{action_link}

This verification link is single-use and will expire in 24 hours.

If you did not create a DormioStay account, please ignore this email.

Best regards,
The DormioStay Team
"""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Verify your DormioStay email address</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background-color: #f8fafc;
      margin: 0;
      padding: 24px;
      color: #1e293b;
    }}
    .container {{
      max-width: 560px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 8px;
      border: 1px solid #e2e8f0;
      padding: 32px;
    }}
    .logo {{ font-size: 24px; font-weight: bold; color: #0284c7; margin-bottom: 24px; }}
    .button {{
      display: inline-block;
      background-color: #0284c7;
      color: #ffffff !important;
      padding: 12px 24px;
      text-decoration: none;
      border-radius: 6px;
      font-weight: 600;
      margin: 20px 0;
    }}
    .footer {{
      margin-top: 32px;
      font-size: 13px;
      color: #64748b;
      border-top: 1px solid #e2e8f0;
      padding-top: 16px;
    }}
    .break-url {{ word-break: break-all; color: #0284c7; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">DormioStay</div>
    <h2>Verify your email address</h2>
    <p>Hello {display_name},</p>
    <p>Thank you for signing up with DormioStay!</p>
    <p>Please confirm your email address by clicking the button below:</p>
    <p style="text-align: center;">
      <a href="{action_link}" class="button" target="_blank" rel="noopener noreferrer">
        Verify Email Address
      </a>
    </p>
    <p>Or copy and paste this link into your browser or mobile app:</p>
    <p class="break-url"><a href="{action_link}">{action_link}</a></p>
    <p style="font-size: 13px; color: #64748b;">
      This link is single-use and will expire in 24 hours.
    </p>
    <div class="footer">
      <p>If you did not create a DormioStay account, you can safely ignore this email.</p>
      <p>&copy; 2026 DormioStay. All rights reserved.</p>
    </div>
  </div>
</body>
</html>"""

    return subject, html_content, text_content


def queue_verification_email(
    session: AsyncSession,
    recipient: str,
    recipient_name: str | None,
    raw_token: str,
    settings: Settings,
    user_id: UUID | None = None,
) -> EmailOutbox:
    """Format and queue a verification email in the transactional outbox."""
    frontend_base = str(settings.frontend_origin).rstrip("/")
    action_link = f"{frontend_base}/verify-email?token={raw_token}"
    subject, html_content, text_content = build_verification_email(
        recipient_name=recipient_name,
        action_link=action_link,
    )
    metadata: dict[str, Any] = {
        "action": "email_verification",
    }
    if user_id is not None:
        metadata["user_id"] = str(user_id)

    return queue_email(
        session=session,
        recipient=recipient,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        recipient_name=recipient_name,
        tags=["auth", "verification"],
        metadata=metadata,
    )


def build_password_reset_email(
    recipient_name: str | None,
    action_link: str,
) -> tuple[str, str, str]:
    """Build subject, HTML, and text content for password reset email (T-11).

    Acceptance criteria:
    - Clear 30-minute single-use expiration notice.
    - Security advice regarding unrecognized requests.
    - No session token or sensitive credential ever placed in email.
    """
    display_name = recipient_name or "there"
    subject = "Reset your DormioStay password"

    text_content = f"""Hello {display_name},

We received a request to reset your password for your DormioStay account.

To reset your password, please open the following link in your browser or mobile app:
{action_link}

This link is single-use and will expire in 30 minutes.

If you did not request a password reset, you can safely ignore this email.
Your password will not change until you access the link above and create a new one.

Best regards,
The DormioStay Team
"""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Reset your DormioStay password</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background-color: #f8fafc;
      margin: 0;
      padding: 24px;
      color: #1e293b;
    }}
    .container {{
      max-width: 560px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 8px;
      border: 1px solid #e2e8f0;
      padding: 32px;
    }}
    .logo {{ font-size: 24px; font-weight: bold; color: #0284c7; margin-bottom: 24px; }}
    .button {{
      display: inline-block;
      background-color: #0284c7;
      color: #ffffff !important;
      padding: 12px 24px;
      text-decoration: none;
      border-radius: 6px;
      font-weight: 600;
      margin: 20px 0;
    }}
    .footer {{
      margin-top: 32px;
      font-size: 13px;
      color: #64748b;
      border-top: 1px solid #e2e8f0;
      padding-top: 16px;
    }}
    .break-url {{ word-break: break-all; color: #0284c7; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">DormioStay</div>
    <h2>Reset your password</h2>
    <p>Hello {display_name},</p>
    <p>We received a request to reset your password for your DormioStay account.</p>
    <p>Please click the button below to choose a new password:</p>
    <p style="text-align: center;">
      <a href="{action_link}" class="button" target="_blank" rel="noopener noreferrer">
        Reset Password
      </a>
    </p>
    <p>Or copy and paste this link into your browser or mobile app:</p>
    <p class="break-url"><a href="{action_link}">{action_link}</a></p>
    <p style="font-size: 13px; color: #64748b;">
      This link is single-use and will expire in 30 minutes.
    </p>
    <div class="footer">
      <p>If you did not request a password reset, you can safely ignore this email.</p>
      <p>Your password will not change until you access the link above and create a new one.</p>
      <p>&copy; 2026 DormioStay. All rights reserved.</p>
    </div>
  </div>
</body>
</html>"""

    return subject, html_content, text_content


def queue_password_reset_email(
    session: AsyncSession,
    recipient: str,
    recipient_name: str | None,
    raw_token: str,
    settings: Settings,
    user_id: UUID | None = None,
) -> EmailOutbox:
    """Format and queue a password reset email in the transactional outbox (T-11)."""
    frontend_base = str(settings.frontend_origin).rstrip("/")
    action_link = f"{frontend_base}/reset-password?token={raw_token}"
    subject, html_content, text_content = build_password_reset_email(
        recipient_name=recipient_name,
        action_link=action_link,
    )
    metadata: dict[str, Any] = {
        "action": "password_reset",
    }
    if user_id is not None:
        metadata["user_id"] = str(user_id)

    return queue_email(
        session=session,
        recipient=recipient,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        recipient_name=recipient_name,
        tags=["auth", "password_reset"],
        metadata=metadata,
    )


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
