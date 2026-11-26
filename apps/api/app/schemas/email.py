"""Pydantic schemas for transactional email outbox and Brevo webhooks (T-09)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EmailQueueRequest(BaseModel):
    """Payload to queue an email for outbox processing."""

    model_config = ConfigDict(extra="forbid")

    recipient: str = Field(..., max_length=255, description="Recipient email address")
    recipient_name: str | None = Field(
        default=None, max_length=200, description="Optional recipient name"
    )
    subject: str = Field(..., min_length=1, max_length=500, description="Email subject")
    html_content: str = Field(..., min_length=1, description="HTML formatted email body")
    text_content: str = Field(..., min_length=1, description="Plain text fallback body")
    tags: list[str] = Field(default_factory=list, description="Categorization tags for Brevo")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional contextual metadata"
    )
    send_after: datetime | None = Field(
        default=None, description="Optional deferred delivery timestamp"
    )


class EmailOutboxResponse(BaseModel):
    """Public/Admin view of an email outbox record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    recipient: str
    recipient_name: str | None = None
    subject: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    send_after: datetime
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    provider_message_id: str | None = None
    metadata_payload: dict[str, Any] | None = Field(default=None, alias="metadata")
    created_at: datetime
    updated_at: datetime


class BrevoWebhookEvent(BaseModel):
    """Schema for incoming Brevo transactional webhook delivery events."""

    model_config = ConfigDict(extra="ignore")

    event: str = Field(..., description="Brevo event type: delivered, bounce, blocked, spam, etc.")
    email: str = Field(default="", description="Recipient email address")
    message_id: str = Field(
        ...,
        alias="message-id",
        description="Brevo provider message ID matching email_outbox.provider_message_id",
    )
    date: str | None = Field(default=None, description="ISO event timestamp")
    ts: int | None = Field(default=None, description="Unix event timestamp")
    reason: str | None = Field(default=None, description="Failure reason if applicable")


class BrevoWebhookResponse(BaseModel):
    """Response returned to Brevo webhook caller."""

    status: str = "ok"
    processed: int = 0
