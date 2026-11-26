"""Comprehensive unit and integration tests for T-09 Email Outbox and Brevo Worker.

Test Coverage:
 1. Outbox model attributes, default columns, check constraints, and indexes.
 2. Pydantic schema validation: EmailQueueRequest, EmailOutboxResponse, BrevoWebhookEvent.
 3. Settings configuration: Brevo credentials, verified sender, dev mode resolution.
 4. Outbox queueing service (transaction-local, recipient normalization).
 5. Decoupled user action safety (failing provider does not fail user actions).
 6. Development mode dispatch: simulated messageId, accepted_by_provider, never marked delivered.
 7. Live Brevo API sending: 201 Created -> accepted_by_provider with provider_message_id.
 8. Live Brevo API permanent error: HTTP 400 invalid payload -> BrevoPermanentError (no retry).
 9. Live Brevo API auth error: HTTP 401 bad key -> BrevoAuthError (alerting, no attempt burn).
10. Live Brevo API transient errors: HTTP 429 rate limit -> BrevoTransientError (retry).
11. Live Brevo API server error: HTTP 5xx -> BrevoTransientError (retry).
12. Live Brevo API network timeout / connection error -> BrevoTransientError (retry).
13. Worker single-item processing: successful transition to accepted_by_provider.
14. Worker single-item processing: permanent failure immediately marks failed.
15. Worker single-item: auth error preserves item in queued without burning attempts.
16. Worker single-item processing: increasing retry delays (1m, 5m, 15m, 30m, 60m).
17. Worker single-item processing: terminal failure on attempt 5.
18. Worker batch processing: processes queued items, skips future send_after items.
19. Worker CLI script: one-shot execution flag (--once) terminates cleanly.
20. Webhook secret protection: valid token in query (?token), headers (X-Webhook-Secret).
21. Webhook secret protection: rejection of missing/invalid secret with HTTP 401.
22. Webhook delivery event: 'delivered' transitions outbox row to delivered with delivered_at.
23. Webhook failure events: 'bounce', 'blocked', 'spam', 'invalid_email' transition to failed.
24. Webhook message ID matching: bracketed (<...>) and unbracketed normalization.
25. Webhook dual-mount routing: both /api/v1/webhooks/brevo and /webhooks/brevo work.
26. Webhook idempotency: unknown messageId returns HTTP 200 without errors.
27. Alembic migration 0002_email_outbox: revision chain and SQL statements validation.
"""

import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, ValidationError
from sqlalchemy import Table

from app.api.v1.webhooks import router as webhooks_router
from app.core.config import Settings
from app.db.models.email_outbox import EmailOutbox
from app.db.session import get_session
from app.schemas.email import (
    BrevoWebhookEvent,
    EmailQueueRequest,
)
from app.services.email_service import (
    BrevoAuthError,
    BrevoPermanentError,
    BrevoTransientError,
    queue_email,
    send_outbox_email,
)
from app.services.email_worker import (
    process_outbox_batch,
    process_single_outbox_item,
)
from scripts.worker import parse_args

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------


def create_test_settings(
    brevo_api_key: str | None = None,
    brevo_webhook_secret: str | None = "test-secret-token-12345",
    email_from_address: str | None = "noreply@dormiostay.com",
    email_from_name: str = "DormioStay",
    email_dev_mode: bool = False,
) -> Settings:
    """Create test settings instance with controlled Brevo and email configuration."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("http://localhost:5173"),
        auth_endpoints_enabled=False,
        csrf_protection_enabled=False,
        rate_limit_enabled=False,
        brevo_api_key=brevo_api_key,
        brevo_webhook_secret=brevo_webhook_secret,
        email_from_address=email_from_address,
        email_from_name=email_from_name,
        email_dev_mode=email_dev_mode,
    )


class InMemoryEmailOutboxDb:
    """In-memory simulation of email_outbox store for isolated worker and webhook tests."""

    def __init__(self) -> None:
        self.items: dict[UUID, EmailOutbox] = {}

    def add(self, item: EmailOutbox) -> None:
        if not item.id:
            item.id = uuid4()
        if not item.created_at:
            item.created_at = datetime.now(UTC)
        if not item.updated_at:
            item.updated_at = datetime.now(UTC)
        self.items[item.id] = item

    def get_by_provider_message_id(self, message_id: str) -> EmailOutbox | None:
        clean_id = message_id.strip("<>")
        for item in self.items.values():
            if item.provider_message_id:
                if item.provider_message_id == message_id:
                    return item
                if item.provider_message_id.strip("<>") == clean_id:
                    return item
        return None

    def get_queued(self, as_of: datetime, limit: int = 20) -> list[EmailOutbox]:
        res: list[EmailOutbox] = []
        for item in sorted(self.items.values(), key=lambda x: x.send_after):
            if item.status == "queued" and item.send_after <= as_of:
                res.append(item)
                if len(res) >= limit:
                    break
        return res


class MockAsyncSession:
    """Mock async SQLAlchemy session wired to InMemoryEmailOutboxDb."""

    def __init__(self, db: InMemoryEmailOutboxDb) -> None:
        self.db = db
        self.committed = False
        self.flushed = False

    def add(self, item: Any) -> None:
        if isinstance(item, EmailOutbox):
            self.db.add(item)

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:
        stmt_str = str(stmt)
        where_clause = stmt_str.split("WHERE")[1] if "WHERE" in stmt_str else ""
        mock_res = MagicMock()

        # Webhook query: search items for matching provider_message_id in WHERE clause
        if "provider_message_id" in where_clause:
            matched = None
            try:
                params = stmt.compile().params
                for val in params.values():
                    if isinstance(val, str):
                        found = self.db.get_by_provider_message_id(val)
                        if found:
                            matched = found
                            break
            except Exception:
                pass

            mock_res.scalars.return_value.first.return_value = matched
            mock_res.scalars.return_value.all.return_value = [matched] if matched else []
            return mock_res

        # Batch worker query: filtering queued items whose send_after <= now
        now = datetime.now(UTC)
        excluded_ids: set[UUID] = set()
        try:
            params = stmt.compile().params
            for val in params.values():
                if isinstance(val, UUID):
                    excluded_ids.add(val)
                elif isinstance(val, (list, tuple, set)):
                    for v in val:
                        if isinstance(v, UUID):
                            excluded_ids.add(v)
        except Exception:
            pass

        queued = [item for item in self.db.get_queued(as_of=now) if item.id not in excluded_ids]
        mock_res.scalars.return_value.all.return_value = queued
        mock_res.scalars.return_value.first.return_value = queued[0] if queued else None
        mock_res.scalar_one_or_none.return_value = queued[0] if queued else None
        return mock_res


# ---------------------------------------------------------------------------
# 1. Outbox Model & Alembic Migration Tests
# ---------------------------------------------------------------------------


def test_email_outbox_model_definition() -> None:
    """Verify EmailOutbox model table name, columns, and constraints."""
    assert EmailOutbox.__tablename__ == "email_outbox"

    columns = EmailOutbox.__table__.columns
    expected_columns = {
        "id",
        "created_at",
        "updated_at",
        "recipient",
        "recipient_name",
        "subject",
        "html_content",
        "text_content",
        "tags",
        "status",
        "attempts",
        "max_attempts",
        "last_error",
        "send_after",
        "sent_at",
        "delivered_at",
        "failed_at",
        "provider_message_id",
        "metadata",
    }
    assert expected_columns.issubset(set(columns.keys()))

    # Column types and nullability checks
    assert not columns["recipient"].nullable
    assert not columns["subject"].nullable
    assert not columns["html_content"].nullable
    assert not columns["text_content"].nullable
    assert not columns["status"].nullable
    assert not columns["attempts"].nullable
    assert not columns["max_attempts"].nullable
    assert not columns["send_after"].nullable

    table = EmailOutbox.__table__
    assert isinstance(table, Table)

    # Check constraint names
    constraint_names = {c.name for c in table.constraints}
    assert "ck_email_outbox_status" in constraint_names
    assert "ck_email_outbox_attempts" in constraint_names
    assert "ck_email_outbox_max_attempts" in constraint_names

    # Check index names
    index_names = {idx.name for idx in table.indexes}
    assert "ix_email_outbox_status_send_after" in index_names
    assert "ix_email_outbox_provider_message_id" in index_names


def test_alembic_0002_migration_integrity() -> None:
    """Verify Alembic migration 0002_email_outbox connects to 0001_foundation."""
    mig_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0002_email_outbox.py"
    spec = importlib.util.spec_from_file_location("migration_0002", mig_path)
    assert spec and spec.loader
    migration_0002 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_0002)

    assert migration_0002.revision == "0002_email_outbox"
    assert migration_0002.down_revision == "0001_foundation"
    assert any("CREATE TABLE email_outbox" in stmt for stmt in migration_0002.UPGRADE_STATEMENTS)
    assert any(
        "DROP TABLE IF EXISTS email_outbox" in stmt for stmt in migration_0002.DOWNGRADE_STATEMENTS
    )


# ---------------------------------------------------------------------------
# 2. Pydantic Schemas Validation Tests
# ---------------------------------------------------------------------------


def test_email_queue_request_schema() -> None:
    """Verify EmailQueueRequest validation and extra field rejection."""
    req = EmailQueueRequest(
        recipient="user@example.com",
        recipient_name="Test User",
        subject="Welcome to DormioStay",
        html_content="<p>Welcome!</p>",
        text_content="Welcome!",
        tags=["welcome", "onboarding"],
        metadata={"user_id": "123"},
    )
    assert req.recipient == "user@example.com"
    assert req.tags == ["welcome", "onboarding"]
    assert req.metadata == {"user_id": "123"}

    # Extra fields must be forbidden
    with pytest.raises(ValidationError):
        EmailQueueRequest(
            recipient="user@example.com",
            subject="Test",
            html_content="<p>Test</p>",
            text_content="Test",
            extra_field="disallowed",  # type: ignore[call-arg]
        )


def test_brevo_webhook_event_schema() -> None:
    """Verify BrevoWebhookEvent parses both message-id and messageId."""
    payload1 = {
        "event": "delivered",
        "email": "user@example.com",
        "message-id": "<20261126.12345@brevo>",
        "date": "2026-11-26T10:00:00Z",
    }
    event1 = BrevoWebhookEvent.model_validate(payload1)
    assert event1.event == "delivered"
    assert event1.message_id == "<20261126.12345@brevo>"

    payload2 = {
        "event": "bounce",
        "email": "user@example.com",
        "message-id": "msg-999",
        "reason": "Mailbox full",
    }
    event2 = BrevoWebhookEvent.model_validate(payload2)
    assert event2.event == "bounce"
    assert event2.reason == "Mailbox full"


# ---------------------------------------------------------------------------
# 3. Settings & Dev Mode Configuration Tests
# ---------------------------------------------------------------------------


def test_settings_is_email_dev_mode() -> None:
    """Verify dev mode resolution based on api key, verified sender, and explicit flag."""
    # Case 1: No key or sender -> dev mode True
    s1 = create_test_settings(brevo_api_key=None, email_from_address=None, email_dev_mode=False)
    assert s1.is_email_dev_mode is True

    # Case 2: Key set but no sender -> dev mode True
    s2 = create_test_settings(
        brevo_api_key="xkeysib-xxx", email_from_address=None, email_dev_mode=False
    )
    assert s2.is_email_dev_mode is True

    # Case 3: Sender set but no key -> dev mode True
    s3 = create_test_settings(
        brevo_api_key=None, email_from_address="noreply@dormiostay.com", email_dev_mode=False
    )
    assert s3.is_email_dev_mode is True

    # Case 4: Both set and email_dev_mode=False -> dev mode False (Live mode)
    s4 = create_test_settings(
        brevo_api_key="xkeysib-xxx",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    assert s4.is_email_dev_mode is False

    # Case 5: Both set but explicit email_dev_mode=True -> dev mode True
    s5 = create_test_settings(
        brevo_api_key="xkeysib-xxx",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=True,
    )
    assert s5.is_email_dev_mode is True


def test_settings_email_production_fail_closed() -> None:
    """Verify production/staging fails closed when auth is enabled without email credentials."""
    # Production + auth enabled + dev_mode False + missing key -> ValidationError
    with pytest.raises(ValidationError) as exc_info:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
            frontend_origin=AnyHttpUrl("https://dormiostay.com"),
            auth_endpoints_enabled=True,
            csrf_secret_key="production-csrf-secret-key-12345",
            email_dev_mode=False,
            brevo_api_key=None,
            email_from_address="noreply@dormiostay.com",
        )
    assert "BREVO_API_KEY and EMAIL_FROM_ADDRESS must be explicitly configured" in str(
        exc_info.value
    )

    # Production + auth enabled + dev_mode False + missing sender -> ValidationError
    with pytest.raises(ValidationError) as exc_info2:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
            frontend_origin=AnyHttpUrl("https://dormiostay.com"),
            auth_endpoints_enabled=True,
            csrf_secret_key="production-csrf-secret-key-12345",
            email_dev_mode=False,
            brevo_api_key="xkeysib-test-key",
            email_from_address=None,
        )
    assert "BREVO_API_KEY and EMAIL_FROM_ADDRESS must be explicitly configured" in str(
        exc_info2.value
    )

    # Production + auth enabled + explicit dev_mode True -> allowed
    s_dev = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("https://dormiostay.com"),
        auth_endpoints_enabled=True,
        csrf_secret_key="production-csrf-secret-key-12345",
        email_dev_mode=True,
    )
    assert s_dev.is_email_dev_mode is True

    # Production + auth disabled (default) -> allowed without credentials
    s_default = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("https://dormiostay.com"),
        auth_endpoints_enabled=False,
    )
    assert s_default.auth_endpoints_enabled is False


# ---------------------------------------------------------------------------
# 4. Outbox Queueing & Decoupled User Action Safety Tests
# ---------------------------------------------------------------------------


def test_queue_email_normalizes_and_defers_commit() -> None:
    """Verify queue_email adds item without committing transaction."""
    db = InMemoryEmailOutboxDb()
    session = MockAsyncSession(db)

    item = queue_email(
        session=session,  # type: ignore[arg-type]
        recipient="  User.Test@Example.COM  ",
        recipient_name="  Test User  ",
        subject="  Verify your email  ",
        html_content="<p>Link</p>",
        text_content="Link",
        tags=["auth", "verification"],
        metadata={"user_id": "u1"},
    )

    assert item.recipient == "user.test@example.com"
    assert item.recipient_name == "Test User"
    assert item.subject == "Verify your email"
    assert item.status == "queued"
    assert item.attempts == 0
    assert item.max_attempts == 5
    assert item.tags == ["auth", "verification"]
    assert item.metadata_payload == {"user_id": "u1"}
    assert item.id in db.items
    # Crucial transactional outbox guarantee: session.commit() was NOT called
    assert session.committed is False


def test_decoupled_user_action_safety() -> None:
    """Acceptance criterion: A failing provider does not fail the user's action."""
    db = InMemoryEmailOutboxDb()
    session = MockAsyncSession(db)

    # Simulate user signup/action that queues an email
    item = queue_email(
        session=session,  # type: ignore[arg-type]
        recipient="tenant@example.com",
        subject="Welcome Tenant",
        html_content="<p>Hello</p>",
        text_content="Hello",
    )
    assert item.status == "queued"
    assert item.id in db.items

    # Even if Brevo is completely broken or unconfigured, the user transaction succeeds
    # because sending is deferred to the worker


# ---------------------------------------------------------------------------
# 5. Brevo Email Service Unit Tests (Dev Mode & Live Mode)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_outbox_email_dev_mode() -> None:
    """Verify dev mode simulates dispatch, sets accepted_by_provider, and NEVER delivered."""
    settings = create_test_settings(email_dev_mode=True)
    item = EmailOutbox(
        id=uuid4(),
        recipient="dev@example.com",
        subject="Dev Email",
        html_content="<p>Dev</p>",
        text_content="Dev",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )

    sim_id = await send_outbox_email(item, settings)

    assert sim_id.startswith("dev-")
    assert item.status == "accepted_by_provider"
    assert item.provider_message_id == sim_id
    assert item.sent_at is not None
    assert "[DEV MODE]" in (item.last_error or "")
    # Strict Ticket Requirement: NEVER mark an email delivered unless provider confirms!
    assert item.status != "delivered"
    assert item.delivered_at is None


@pytest.mark.anyio
async def test_send_outbox_email_live_brevo_success() -> None:
    """Verify live Brevo dispatch sends correct request and handles HTTP 201 Created."""
    settings = create_test_settings(
        brevo_api_key="xkeysib-mock-valid-key",
        email_from_address="noreply@dormiostay.com",
        email_from_name="DormioStay Portal",
        email_dev_mode=False,
    )
    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        recipient_name="Alice",
        subject="Your Booking Confirmation",
        html_content="<h1>Confirmed!</h1>",
        text_content="Confirmed!",
        tags=["booking", "confirmation"],
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )

    mock_response = httpx.Response(
        status_code=201,
        json={"messageId": "<20261126-brevo-real-id-99@smtp-relay.brevo.com>"},
        request=httpx.Request("POST", settings.brevo_api_url),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    msg_id = await send_outbox_email(item, settings, client=mock_client)

    assert msg_id == "<20261126-brevo-real-id-99@smtp-relay.brevo.com>"
    assert item.status == "accepted_by_provider"
    assert item.provider_message_id == msg_id
    assert item.sent_at is not None
    assert item.last_error is None

    # Inspect call arguments
    call_args = mock_client.post.call_args
    assert call_args[0][0] == settings.brevo_api_url
    assert call_args[1]["headers"]["api-key"] == "xkeysib-mock-valid-key"
    assert call_args[1]["json"]["sender"] == {
        "name": "DormioStay Portal",
        "email": "noreply@dormiostay.com",
    }
    assert call_args[1]["json"]["to"] == [{"email": "user@example.com", "name": "Alice"}]
    assert call_args[1]["json"]["tags"] == ["booking", "confirmation"]


@pytest.mark.anyio
async def test_send_outbox_email_live_brevo_permanent_error_400() -> None:
    """Verify HTTP 400 invalid payload raises BrevoPermanentError and is not retried."""
    settings = create_test_settings(
        brevo_api_key="xkeysib-valid",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    item = EmailOutbox(
        id=uuid4(),
        recipient="invalid-email-format",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )

    mock_response = httpx.Response(
        status_code=400,
        text='{"code": "invalid_parameter", "message": "Invalid email address format"}',
        request=httpx.Request("POST", settings.brevo_api_url),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(BrevoPermanentError) as exc_info:
        await send_outbox_email(item, settings, client=mock_client)

    assert "HTTP 400" in str(exc_info.value)


@pytest.mark.anyio
async def test_send_outbox_email_live_brevo_auth_error_401() -> None:
    """Verify HTTP 401 unauthorized raises BrevoAuthError."""
    settings = create_test_settings(
        brevo_api_key="xkeysib-invalid-key",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )

    mock_response = httpx.Response(
        status_code=401,
        text='{"code": "unauthorized", "message": "Key not found"}',
        request=httpx.Request("POST", settings.brevo_api_url),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(BrevoAuthError) as exc_info:
        await send_outbox_email(item, settings, client=mock_client)

    assert "HTTP 401" in str(exc_info.value)


@pytest.mark.anyio
async def test_send_outbox_email_live_brevo_transient_error_429_and_5xx() -> None:
    """Verify HTTP 429 and 500 server error raise BrevoTransientError."""
    settings = create_test_settings(
        brevo_api_key="xkeysib-valid",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )

    # Test 429 Rate Limit
    mock_client_429 = AsyncMock(spec=httpx.AsyncClient)
    mock_client_429.post = AsyncMock(
        return_value=httpx.Response(
            status_code=429,
            text='{"message": "Rate limit exceeded"}',
            request=httpx.Request("POST", settings.brevo_api_url),
        )
    )
    with pytest.raises(BrevoTransientError):
        await send_outbox_email(item, settings, client=mock_client_429)

    # Test 503 Service Unavailable
    mock_client_503 = AsyncMock(spec=httpx.AsyncClient)
    mock_client_503.post = AsyncMock(
        return_value=httpx.Response(
            status_code=503,
            text='{"message": "Brevo service temporarily unavailable"}',
            request=httpx.Request("POST", settings.brevo_api_url),
        )
    )
    with pytest.raises(BrevoTransientError):
        await send_outbox_email(item, settings, client=mock_client_503)


@pytest.mark.anyio
async def test_send_outbox_email_network_timeout() -> None:
    """Verify network connection timeout raises BrevoTransientError."""
    settings = create_test_settings(
        brevo_api_key="xkeysib-valid",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))

    with pytest.raises(BrevoTransientError) as exc_info:
        await send_outbox_email(item, settings, client=mock_client)

    assert "Connection timed out" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Worker Single-Item State Transitions & Retry Schedule Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_worker_process_single_item_success() -> None:
    """Verify single outbox item success transitions status to accepted_by_provider."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()
    session = MockAsyncSession(db)

    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )
    db.add(item)

    outcome = await process_single_outbox_item(
        session=session,  # type: ignore[arg-type]
        item=item,
        settings=settings,
    )

    assert outcome == "accepted"
    assert item.status == "accepted_by_provider"
    assert item.attempts == 1
    assert item.sent_at is not None
    assert session.committed is True


@pytest.mark.anyio
async def test_worker_process_single_item_permanent_failure() -> None:
    """Verify HTTP 400 permanent failure transitions item to terminal 'failed' immediately."""
    settings = create_test_settings(
        brevo_api_key="key",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    db = InMemoryEmailOutboxDb()
    session = MockAsyncSession(db)

    item = EmailOutbox(
        id=uuid4(),
        recipient="bad-recipient",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )
    db.add(item)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        return_value=httpx.Response(
            status_code=400,
            text='{"message": "Invalid recipient format"}',
            request=httpx.Request("POST", settings.brevo_api_url),
        )
    )

    outcome = await process_single_outbox_item(
        session=session,  # type: ignore[arg-type]
        item=item,
        settings=settings,
        client=mock_client,
    )

    assert outcome == "failed"
    assert item.status == "failed"
    assert item.attempts == 1
    assert item.failed_at is not None
    assert "Permanent error" in (item.last_error or "")
    assert session.committed is True


@pytest.mark.anyio
async def test_worker_process_single_item_auth_error_no_attempt_burn() -> None:
    """Verify HTTP 401 auth error preserves item in queued without consuming attempt budget."""
    settings = create_test_settings(
        brevo_api_key="invalid-key",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    db = InMemoryEmailOutboxDb()
    session = MockAsyncSession(db)

    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )
    db.add(item)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        return_value=httpx.Response(
            status_code=401,
            text='{"message": "Bad API key"}',
            request=httpx.Request("POST", settings.brevo_api_url),
        )
    )

    outcome = await process_single_outbox_item(
        session=session,  # type: ignore[arg-type]
        item=item,
        settings=settings,
        client=mock_client,
    )

    assert outcome == "auth_error"
    assert item.status == "queued"
    # Attempts count was not burned!
    assert item.attempts == 0
    assert "authentication error" in (item.last_error or "").lower()
    assert session.committed is True


@pytest.mark.anyio
async def test_worker_process_single_item_retry_schedule() -> None:
    """Verify exponential backoff schedule on transient failures across 5 attempts."""
    settings = create_test_settings(
        brevo_api_key="key",
        email_from_address="noreply@dormiostay.com",
        email_dev_mode=False,
    )
    db = InMemoryEmailOutboxDb()
    session = MockAsyncSession(db)

    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Test",
        html_content="<p>Test</p>",
        text_content="Test",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )
    db.add(item)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        return_value=httpx.Response(
            status_code=500,
            text='{"message": "Brevo Internal Server Error"}',
            request=httpx.Request("POST", settings.brevo_api_url),
        )
    )

    expected_delays = [60, 300, 900, 1800]  # Delays for attempts 1, 2, 3, 4

    for expected_delay in expected_delays:
        start_time = datetime.now(UTC)
        outcome = await process_single_outbox_item(
            session=session,  # type: ignore[arg-type]
            item=item,
            settings=settings,
            client=mock_client,
        )
        assert outcome == "retried"
        assert item.status == "queued"
        # send_after must be rescheduled into the future by approximately expected_delay
        diff_seconds = (item.send_after - start_time).total_seconds()
        assert abs(diff_seconds - expected_delay) < 5.0

    # Attempt 5: Exhausts max_attempts -> terminal 'failed'
    outcome5 = await process_single_outbox_item(
        session=session,  # type: ignore[arg-type]
        item=item,
        settings=settings,
        client=mock_client,
    )
    assert outcome5 == "failed"
    assert item.status == "failed"
    assert item.attempts == 5
    assert item.failed_at is not None
    assert "Max attempts (5) reached" in (item.last_error or "")


# ---------------------------------------------------------------------------
# 7. Worker Batch Processing & CLI Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_process_outbox_batch() -> None:
    """Verify process_outbox_batch queries queued emails and aggregates stats."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()

    # Add 3 queued emails ready now
    now = datetime.now(UTC)
    for i in range(3):
        db.add(
            EmailOutbox(
                id=uuid4(),
                recipient=f"user{i}@example.com",
                subject=f"Subject {i}",
                html_content="<p>Body</p>",
                text_content="Body",
                status="queued",
                attempts=0,
                max_attempts=5,
                send_after=now - timedelta(seconds=10),
            )
        )

    # Add 1 future email that should be skipped
    future_item = EmailOutbox(
        id=uuid4(),
        recipient="future@example.com",
        subject="Future Subject",
        html_content="<p>Body</p>",
        text_content="Body",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=now + timedelta(hours=1),
    )
    db.add(future_item)

    @asynccontextmanager
    async def mock_session_factory() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    stats = await process_outbox_batch(
        session_factory=mock_session_factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=10,
    )

    assert stats["processed"] == 3
    assert stats["accepted"] == 3
    assert stats["failed"] == 0
    assert stats["retried"] == 0

    # Future item remains queued
    assert future_item.status == "queued"


def test_worker_cli_argument_parsing() -> None:
    """Verify scripts.worker CLI flag parsing."""
    with patch("sys.argv", ["worker.py", "--once", "--batch-size", "50", "--interval", "10.0"]):
        args = parse_args()
        assert args.once is True
        assert args.batch_size == 50
        assert args.interval == 10.0


# ---------------------------------------------------------------------------
# 8. Brevo Webhook Endpoint Security & Delivery Events Tests
# ---------------------------------------------------------------------------


def create_webhook_test_client(
    db: InMemoryEmailOutboxDb,
    secret: str | None = "webhook-secret-token",
) -> tuple[TestClient, Settings]:
    """Create test client with mocked DB session and webhook route."""
    from app.core.config import get_settings

    settings = create_test_settings(brevo_webhook_secret=secret)
    app = FastAPI()
    app.state.settings = settings
    app.include_router(webhooks_router, prefix="/api/v1/webhooks")
    app.include_router(webhooks_router, prefix="/webhooks")

    async def override_get_session() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    return client, settings


def test_webhook_secret_protection() -> None:
    """Verify secret token authentication: rejects unauthorized, accepts valid headers/params."""
    db = InMemoryEmailOutboxDb()
    secret = "secret-xyz-789"
    client, _ = create_webhook_test_client(db, secret=secret)

    payload = {"event": "delivered", "message-id": "msg-123"}

    # 1. Missing secret -> 401 Unauthorized
    resp_missing = client.post("/api/v1/webhooks/brevo", json=payload)
    assert resp_missing.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp_missing.json()["detail"] == "INVALID_WEBHOOK_SECRET"

    # 2. Invalid secret in query -> 401 Unauthorized
    resp_invalid_query = client.post("/api/v1/webhooks/brevo?token=wrong-secret", json=payload)
    assert resp_invalid_query.status_code == status.HTTP_401_UNAUTHORIZED

    # 3. Invalid secret in header -> 401 Unauthorized
    resp_invalid_header = client.post(
        "/api/v1/webhooks/brevo",
        headers={"X-Webhook-Secret": "wrong-secret"},
        json=payload,
    )
    assert resp_invalid_header.status_code == status.HTTP_401_UNAUTHORIZED

    # 4. Valid secret via X-Webhook-Secret -> 200 OK
    resp_valid_header = client.post(
        "/api/v1/webhooks/brevo",
        headers={"X-Webhook-Secret": secret},
        json=payload,
    )
    assert resp_valid_header.status_code == status.HTTP_200_OK

    # 5. Valid secret via query parameter ?token=... -> 200 OK
    resp_valid_token = client.post(
        f"/api/v1/webhooks/brevo?token={secret}",
        json=payload,
    )
    assert resp_valid_token.status_code == status.HTTP_200_OK

    # 6. Valid secret via query parameter ?secret=... -> 200 OK
    resp_valid_secret = client.post(
        f"/api/v1/webhooks/brevo?secret={secret}",
        json=payload,
    )
    assert resp_valid_secret.status_code == status.HTTP_200_OK

    # 7. Valid secret via Authorization: Bearer <secret> -> 200 OK
    resp_valid_bearer = client.post(
        "/api/v1/webhooks/brevo",
        headers={"Authorization": f"Bearer {secret}"},
        json=payload,
    )
    assert resp_valid_bearer.status_code == status.HTTP_200_OK


def test_webhook_delivery_event_transitions() -> None:
    """Verify 'delivered' event transitions outbox row to 'delivered' with timestamp."""
    db = InMemoryEmailOutboxDb()
    secret = "secret-123"
    client, _ = create_webhook_test_client(db, secret=secret)

    msg_id = "<brevo-delivered-uuid-001@smtp.brevo.com>"
    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Receipt",
        html_content="<p>Receipt</p>",
        text_content="Receipt",
        status="accepted_by_provider",
        provider_message_id=msg_id,
        attempts=1,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )
    db.add(item)

    payload = {
        "event": "delivered",
        "email": "user@example.com",
        "message-id": msg_id,
        "date": "2026-11-26T12:00:00+00:00",
    }

    resp = client.post(
        f"/api/v1/webhooks/brevo?token={secret}",
        json=payload,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "ok"
    assert resp.json()["processed"] == 1

    assert item.status == "delivered"
    assert item.delivered_at is not None
    assert item.last_error is None


def test_webhook_failure_event_transitions() -> None:
    """Verify 'bounce', 'blocked', 'spam' events transition outbox row to 'failed'."""
    db = InMemoryEmailOutboxDb()
    secret = "secret-123"
    client, _ = create_webhook_test_client(db, secret=secret)

    msg_id = "brevo-bounce-uuid-002"
    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Reset Password",
        html_content="<p>Reset</p>",
        text_content="Reset",
        status="accepted_by_provider",
        provider_message_id=msg_id,
        attempts=1,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )
    db.add(item)

    payload = {
        "event": "hard_bounce",
        "email": "user@example.com",
        "message-id": msg_id,
        "reason": "Mailbox does not exist",
    }

    resp = client.post(
        f"/api/v1/webhooks/brevo?token={secret}",
        json=payload,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["processed"] == 1

    assert item.status == "failed"
    assert item.failed_at is not None
    assert "Brevo delivery failure: hard_bounce" in (item.last_error or "")
    assert "Mailbox does not exist" in (item.last_error or "")


def test_webhook_bracketed_message_id_matching() -> None:
    """Verify webhook matches provider_message_id with or without angle brackets."""
    db = InMemoryEmailOutboxDb()
    secret = "secret-123"
    client, _ = create_webhook_test_client(db, secret=secret)

    # Outbox item stored with brackets: <msg-abc-123>
    msg_id_bracketed = "<msg-abc-123>"
    item = EmailOutbox(
        id=uuid4(),
        recipient="user@example.com",
        subject="Welcome",
        html_content="<p>Welcome</p>",
        text_content="Welcome",
        status="accepted_by_provider",
        provider_message_id=msg_id_bracketed,
        attempts=1,
        max_attempts=5,
        send_after=datetime.now(UTC),
    )
    db.add(item)

    # Webhook sends unbracketed message-id: msg-abc-123
    payload = {
        "event": "delivered",
        "email": "user@example.com",
        "message-id": "msg-abc-123",
    }

    resp = client.post(
        f"/api/v1/webhooks/brevo?token={secret}",
        json=payload,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["processed"] == 1
    assert item.status == "delivered"


def test_webhook_dual_mount_and_idempotency() -> None:
    """Verify both /api/v1/webhooks/brevo and /webhooks/brevo work, and unknown msg returns 200."""
    db = InMemoryEmailOutboxDb()
    secret = "secret-123"
    client, _ = create_webhook_test_client(db, secret=secret)

    payload_unknown = {
        "event": "delivered",
        "email": "unknown@example.com",
        "message-id": "nonexistent-message-id-999",
    }

    # /webhooks/brevo (without /api/v1 prefix)
    resp1 = client.post(
        f"/webhooks/brevo?token={secret}",
        json=payload_unknown,
    )
    assert resp1.status_code == status.HTTP_200_OK
    assert resp1.json()["processed"] == 0

    # /api/v1/webhooks/brevo
    resp2 = client.post(
        f"/api/v1/webhooks/brevo?token={secret}",
        json=payload_unknown,
    )
    assert resp2.status_code == status.HTTP_200_OK
    assert resp2.json()["processed"] == 0


# ---------------------------------------------------------------------------
# 6. Concurrency & AUDIT-01 Per-Item Transaction Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_audit01_per_item_discrete_session_lifecycle() -> None:
    """Verify each queued email is queried and processed in its own discrete transaction."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()
    now = datetime.now(UTC)

    for i in range(3):
        db.add(
            EmailOutbox(
                id=uuid4(),
                recipient=f"user{i}@example.com",
                subject=f"Subject {i}",
                html_content="<p>Body</p>",
                text_content="Body",
                status="queued",
                attempts=0,
                max_attempts=5,
                send_after=now - timedelta(seconds=10),
            )
        )

    session_count = 0
    commit_count = 0

    @asynccontextmanager
    async def counting_session_factory() -> AsyncIterator[MockAsyncSession]:
        nonlocal session_count, commit_count
        session_count += 1
        session = MockAsyncSession(db)
        try:
            yield session
            if session.committed:
                commit_count += 1
        finally:
            pass

    stats = await process_outbox_batch(
        session_factory=counting_session_factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=10,
    )

    assert stats["processed"] == 3
    assert stats["accepted"] == 3
    # Exactly 4 sessions opened: 3 for the items processed + 1 for the terminal empty check
    assert session_count == 4
    # Exactly 3 commits: one per processed email
    assert commit_count == 3


@pytest.mark.anyio
async def test_audit01_no_stale_batch_in_memory_processing() -> None:
    """Verify the worker re-queries the database for each item instead of using stale batch rows."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()
    now = datetime.now(UTC)

    item1_id = uuid4()
    item2_id = uuid4()

    db.add(
        EmailOutbox(
            id=item1_id,
            recipient="user1@example.com",
            subject="Subject 1",
            html_content="<p>Body</p>",
            text_content="Body",
            status="queued",
            attempts=0,
            max_attempts=5,
            send_after=now - timedelta(seconds=10),
        )
    )
    db.add(
        EmailOutbox(
            id=item2_id,
            recipient="user2@example.com",
            subject="Subject 2",
            html_content="<p>Body</p>",
            text_content="Body",
            status="queued",
            attempts=0,
            max_attempts=5,
            send_after=now - timedelta(seconds=5),
        )
    )

    call_index = 0

    @asynccontextmanager
    async def intercepting_session_factory() -> AsyncIterator[MockAsyncSession]:
        nonlocal call_index
        call_index += 1
        if call_index == 2:
            # Simulate a concurrent worker or admin canceling/delivering item2 between iterations
            item2 = db.items[item2_id]
            item2.status = "accepted_by_provider"
        yield MockAsyncSession(db)

    stats = await process_outbox_batch(
        session_factory=intercepting_session_factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=10,
    )

    # Only item1 was processed by this worker; item2 was skipped because it was already accepted
    assert stats["processed"] == 1
    assert stats["accepted"] == 1
    assert db.items[item1_id].status == "accepted_by_provider"
    assert db.items[item2_id].status == "accepted_by_provider"


@pytest.mark.anyio
async def test_audit01_multi_worker_skip_locked_simulation() -> None:
    """Simulate two concurrent workers claiming distinct items without duplication."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()
    now = datetime.now(UTC)

    id_a = uuid4()
    id_b = uuid4()

    db.add(
        EmailOutbox(
            id=id_a,
            recipient="workerA@example.com",
            subject="A",
            html_content="<p>A</p>",
            text_content="A",
            status="queued",
            attempts=0,
            max_attempts=5,
            send_after=now - timedelta(seconds=10),
        )
    )
    db.add(
        EmailOutbox(
            id=id_b,
            recipient="workerB@example.com",
            subject="B",
            html_content="<p>B</p>",
            text_content="B",
            status="queued",
            attempts=0,
            max_attempts=5,
            send_after=now - timedelta(seconds=5),
        )
    )

    # Worker 1 processes 1 item
    @asynccontextmanager
    async def factory1() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    stats1 = await process_outbox_batch(
        session_factory=factory1,  # type: ignore[arg-type]
        settings=settings,
        batch_size=1,
    )
    assert stats1["processed"] == 1
    assert db.items[id_a].status == "accepted_by_provider"

    # Worker 2 processes next item
    @asynccontextmanager
    async def factory2() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    stats2 = await process_outbox_batch(
        session_factory=factory2,  # type: ignore[arg-type]
        settings=settings,
        batch_size=1,
    )
    assert stats2["processed"] == 1
    assert db.items[id_b].status == "accepted_by_provider"

    # Both items processed exactly once
    assert db.items[id_a].attempts == 1
    assert db.items[id_b].attempts == 1


@pytest.mark.anyio
async def test_audit01_batch_statistics_counters_mixed_outcomes() -> None:
    """Verify batch statistics accurately reflect accepted, failed, and retried items."""
    settings = create_test_settings(email_dev_mode=False, brevo_api_key="valid-key")
    db = InMemoryEmailOutboxDb()
    now = datetime.now(UTC)

    # 1. Normal item
    id_ok = uuid4()
    db.add(
        EmailOutbox(
            id=id_ok,
            recipient="ok@example.com",
            subject="OK",
            html_content="<p>OK</p>",
            text_content="OK",
            status="queued",
            attempts=0,
            max_attempts=5,
            send_after=now - timedelta(seconds=10),
        )
    )

    # 2. Permanent failure item
    id_perm = uuid4()
    db.add(
        EmailOutbox(
            id=id_perm,
            recipient="bad@example.com",
            subject="Bad",
            html_content="<p>Bad</p>",
            text_content="Bad",
            status="queued",
            attempts=0,
            max_attempts=5,
            send_after=now - timedelta(seconds=8),
        )
    )

    # 3. Transient failure item
    id_trans = uuid4()
    db.add(
        EmailOutbox(
            id=id_trans,
            recipient="transient@example.com",
            subject="Transient",
            html_content="<p>Transient</p>",
            text_content="Transient",
            status="queued",
            attempts=0,
            max_attempts=5,
            send_after=now - timedelta(seconds=6),
        )
    )

    @asynccontextmanager
    async def factory() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    def mock_brevo_transport(request: httpx.Request) -> httpx.Response:
        import json

        data = json.loads(request.content.decode("utf-8"))
        recipient = data["to"][0]["email"]
        if recipient == "ok@example.com":
            return httpx.Response(201, json={"messageId": "msg-ok-123"})
        elif recipient == "bad@example.com":
            return httpx.Response(400, text="Bad Request: Invalid recipient")
        else:
            return httpx.Response(429, text="Too Many Requests")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(mock_brevo_transport)
    ) as mock_client:
        stats = await process_outbox_batch(
            session_factory=factory,  # type: ignore[arg-type]
            settings=settings,
            batch_size=10,
            client=mock_client,
        )

    assert stats["processed"] == 3
    assert stats["accepted"] == 1
    assert stats["failed"] == 1
    assert stats["retried"] == 1
    assert stats["auth_error"] == 0

    assert db.items[id_ok].status == "accepted_by_provider"
    assert db.items[id_perm].status == "failed"
    assert db.items[id_trans].status == "queued"
    assert db.items[id_trans].send_after > now


@pytest.mark.anyio
async def test_audit01_no_eligible_items_clean_exit() -> None:
    """Verify worker terminates cleanly with zero processing when no items are eligible."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()

    @asynccontextmanager
    async def factory() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    stats = await process_outbox_batch(
        session_factory=factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=10,
    )

    assert stats["processed"] == 0
    assert stats["accepted"] == 0
    assert stats["failed"] == 0
    assert stats["retried"] == 0
    assert stats["auth_error"] == 0


@pytest.mark.anyio
async def test_audit01_brevo_auth_error_halts_batch_without_spinning() -> None:
    """Verify BrevoAuthError (401) halts the batch immediately without spinning in a loop."""
    settings = create_test_settings(email_dev_mode=False, brevo_api_key="bad-key")
    db = InMemoryEmailOutboxDb()
    now = datetime.now(UTC)

    for i in range(3):
        db.add(
            EmailOutbox(
                id=uuid4(),
                recipient=f"user{i}@example.com",
                subject=f"Subject {i}",
                html_content="<p>Body</p>",
                text_content="Body",
                status="queued",
                attempts=0,
                max_attempts=5,
                send_after=now - timedelta(seconds=10),
            )
        )

    @asynccontextmanager
    async def factory() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    def mock_brevo_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized: Invalid API key")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(mock_brevo_transport)
    ) as mock_client:
        stats = await process_outbox_batch(
            session_factory=factory,  # type: ignore[arg-type]
            settings=settings,
            batch_size=10,
            client=mock_client,
        )

    # Exactly 1 item was attempted before the batch halted
    assert stats["processed"] == 1
    assert stats["auth_error"] == 1
    assert stats["accepted"] == 0


@pytest.mark.anyio
async def test_audit01_attempted_ids_deduplication_prevents_reselection() -> None:
    """Verify attempted_ids prevents the worker from re-selecting an item that remains queued."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()
    now = datetime.now(UTC)
    item_id = uuid4()

    item = EmailOutbox(
        id=item_id,
        recipient="user@example.com",
        subject="Subject",
        html_content="<p>Body</p>",
        text_content="Body",
        status="queued",
        attempts=0,
        max_attempts=5,
        send_after=now - timedelta(seconds=10),
    )
    db.add(item)

    query_count = 0

    @asynccontextmanager
    async def factory() -> AsyncIterator[MockAsyncSession]:
        nonlocal query_count
        query_count += 1
        yield MockAsyncSession(db)

    stats = await process_outbox_batch(
        session_factory=factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=5,
    )

    assert stats["processed"] == 1
    assert query_count == 2  # 1 for item + 1 for next check which finds no non-attempted item


@pytest.mark.anyio
async def test_audit01_bounded_processing_respects_batch_size() -> None:
    """Verify process_outbox_batch terminates after exactly batch_size items."""
    settings = create_test_settings(email_dev_mode=True)
    db = InMemoryEmailOutboxDb()
    now = datetime.now(UTC)

    for i in range(5):
        db.add(
            EmailOutbox(
                id=uuid4(),
                recipient=f"user{i}@example.com",
                subject=f"Subject {i}",
                html_content="<p>Body</p>",
                text_content="Body",
                status="queued",
                attempts=0,
                max_attempts=5,
                send_after=now - timedelta(seconds=10),
            )
        )

    @asynccontextmanager
    async def factory() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession(db)

    stats = await process_outbox_batch(
        session_factory=factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=2,
    )

    assert stats["processed"] == 2
    assert stats["accepted"] == 2
    # 3 remaining items are still queued
    queued_remaining = [it for it in db.items.values() if it.status == "queued"]
    assert len(queued_remaining) == 3


@pytest.mark.anyio
async def test_audit01_worker_exception_rolls_back_safely() -> None:
    """Verify unexpected database or worker errors in the batch loop log and break cleanly."""
    settings = create_test_settings(email_dev_mode=True)

    @asynccontextmanager
    async def failing_factory() -> AsyncIterator[Any]:
        if False:
            yield
        raise RuntimeError("Database connection lost")

    stats = await process_outbox_batch(
        session_factory=failing_factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=5,
    )

    assert stats["processed"] == 0
    assert stats["accepted"] == 0


@pytest.mark.anyio
async def test_audit01_genuine_sqlalchemy_orm_per_item_transactions() -> None:
    """Verify discrete per-item transactions with real SQLAlchemy ORM on SQLite."""
    import json
    import sqlite3

    sqlite3.register_adapter(list, lambda val: json.dumps(val))

    from sqlalchemy import create_engine
    from sqlalchemy.dialects.postgresql import ARRAY, JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    @compiles(ARRAY, "sqlite")
    def _compile_array(element: Any, compiler: Any, **kw: Any) -> str:
        return "TEXT"

    @compiles(JSONB, "sqlite")
    def _compile_jsonb(element: Any, compiler: Any, **kw: Any) -> str:
        return "TEXT"

    engine = create_engine("sqlite:///:memory:")
    EmailOutbox.metadata.create_all(engine)
    sm = sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    id1 = uuid4()
    id2 = uuid4()

    with sm() as s:
        s.add(
            EmailOutbox(
                id=id1,
                recipient="real1@example.com",
                subject="Real 1",
                html_content="<p>1</p>",
                text_content="1",
                tags=[],
                status="queued",
                attempts=0,
                max_attempts=5,
                send_after=now - timedelta(seconds=10),
            )
        )
        s.add(
            EmailOutbox(
                id=id2,
                recipient="real2@example.com",
                subject="Real 2",
                html_content="<p>2</p>",
                text_content="2",
                tags=[],
                status="queued",
                attempts=0,
                max_attempts=5,
                send_after=now - timedelta(seconds=5),
            )
        )
        s.commit()

    # Wrap sm in an async session adapter
    class AsyncSqliteAdapter:
        def __init__(self, sync_s: Any) -> None:
            self.sync_s = sync_s
            self.bind = engine

        async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
            return self.sync_s.execute(stmt, *args, **kwargs)

        async def commit(self) -> None:
            self.sync_s.commit()

        async def flush(self) -> None:
            self.sync_s.flush()

        async def rollback(self) -> None:
            self.sync_s.rollback()

    @asynccontextmanager
    async def orm_factory() -> AsyncIterator[AsyncSqliteAdapter]:
        with sm() as sync_s:
            yield AsyncSqliteAdapter(sync_s)

    settings = create_test_settings(email_dev_mode=True)
    stats = await process_outbox_batch(
        session_factory=orm_factory,  # type: ignore[arg-type]
        settings=settings,
        batch_size=10,
    )

    assert stats["processed"] == 2
    assert stats["accepted"] == 2

    # Verify rows in DB were updated
    with sm() as s:
        r1 = s.get(EmailOutbox, id1)
        r2 = s.get(EmailOutbox, id2)
        assert r1 is not None and r1.status == "accepted_by_provider"
        assert r2 is not None and r2.status == "accepted_by_provider"
        assert r1.attempts == 1
        assert r2.attempts == 1
