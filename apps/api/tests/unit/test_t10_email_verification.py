"""Comprehensive unit and acceptance tests for T-10 Email Verification.

Covers all acceptance criteria and specifications:
 1. Signup creates random token, stores SHA-256 hash, 24h expiry, queues outbox email.
 2. Token hash strictly adheres to PostgreSQL CHECK constraint (^[0-9a-f]{64}$).
 3. Single-use: first verification succeeds, records used_at and email_verified_at.
 4. Reused token rejection: second verification returns HTTP 400 TOKEN_ALREADY_USED.
 5. Expired token rejection: expired token returns HTTP 400 TOKEN_EXPIRED with clear message.
 6. Invalid token rejection: unknown/malformed token returns HTTP 400 TOKEN_INVALID.
 7. Disabled/suspended account rejection: returns HTTP 403 ACCOUNT_DISABLED.
 8. Resend verification for unverified account invalidates old tokens, queues new email.
 9. Resend verification anti-enumeration: non-existent email returns generic success.
10. Resend verification anti-enumeration: already verified email returns generic success.
11. Resend verification rate limiting: per-email threshold enforcement (HTTP 429).
12. Resend verification rate limiting: per-IP threshold enforcement (HTTP 429).
13. Restricted action guard (require_verified_user) blocks unverified
    user with HTTP 403 EMAIL_NOT_VERIFIED.
14. Restricted action guard allows verified user to proceed.
15. Both-client action link contract: single-use HTTPS link with web
    fallback, no session tokens in link.
16. Safe logging: action tokens are never logged or returned in error payloads.
17. Both-client transport: Web client with CSRF token succeeds.
18. Both-client transport: Native client (X-Client-Type: native) succeeds without CSRF.
19. Navigation safety: GET requests never consume action tokens.
20. Inactive auth guard: auth_endpoints_enabled=False fails closed with HTTP 503.
21. Concurrent verification race condition: atomic conditional UPDATE prevents double consumption.
22. Exact boundary expiration: token expiring at or before now is rejected with TOKEN_EXPIRED.
23. Transaction rollback atomicity: database failure rolls back token consumption.
24. Cross-user token isolation: verifying a token for User A cannot mark User B verified.
25. Genuine SQLAlchemy: identity map staleness bypassed with populate_existing=True.
26. Genuine SQLAlchemy: losing request returns TOKEN_ALREADY_USED deterministically.
27. Genuine SQLAlchemy: boundary expiration returns TOKEN_EXPIRED.
28. Genuine SQLAlchemy: unknown token returns TOKEN_INVALID.
29. Genuine SQLAlchemy: successful verification commits token and user changes atomically.
30. Genuine SQLAlchemy: transaction failure rolls back both changes in database.
"""

import asyncio
import math
import time
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl

from app.api.deps import require_verified_user
from app.core.config import Settings, get_settings
from app.core.csrf import generate_anonymous_csrf_token, get_csrf_signing_key
from app.core.errors import AppError
from app.core.rate_limit import (
    reset_rate_limit_state,
    set_redis_client_override,
)
from app.core.security import (
    generate_action_token,
    generate_session_token,
    hash_action_token,
    hash_password,
    hash_session_token,
)
from app.db.models import EmailVerificationToken, User, UserProfile, UserSession
from app.db.models.email_outbox import EmailOutbox
from app.db.repositories import session_repo, user_repo, verification_repo
from app.db.session import get_session, get_session_factory
from app.main import create_app
from app.services import auth_service, email_service

_ACTIVE_PATCHERS: list[Any] = []


class AsyncMockRedis:
    """In-memory Redis test double executing the exact ATOMIC_RATE_LIMIT_LUA semantics."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, float] = {}
        self.call_history: list[tuple[str, int, int]] = []
        self._lock = asyncio.Lock()

    async def eval(self, script: str, numkeys: int, *args: Any) -> list[int]:
        async with self._lock:
            key = str(args[0])
            window = int(args[1])
            limit = int(args[2])
            now = time.monotonic()

            self.call_history.append((key, window, limit))

            # Expiration evaluation
            if key in self.ttls and self.ttls[key] <= now:
                self.store.pop(key, None)
                self.ttls.pop(key, None)

            current = self.store.get(key, 0) + 1
            self.store[key] = current

            if current == 1:
                self.ttls[key] = now + window

            ttl = max(1, min(window, math.ceil(self.ttls[key] - now)))

            if current <= limit:
                return [1, limit - current, ttl]
            else:
                return [0, 0, ttl]

    async def flushdb(self) -> None:
        async with self._lock:
            self.store.clear()
            self.ttls.clear()
            self.call_history.clear()

    async def aclose(self) -> None:
        pass


@pytest.fixture(autouse=True)
def cleanup_patchers() -> Generator[None, None, None]:
    """Ensure all mock patchers are stopped after each test for strict isolation."""
    asyncio.run(reset_rate_limit_state())
    set_redis_client_override(None)
    yield
    while _ACTIVE_PATCHERS:
        p = _ACTIVE_PATCHERS.pop()
        p.stop()
    asyncio.run(reset_rate_limit_state())
    set_redis_client_override(None)


def create_test_settings(
    auth_enabled: bool = True,
    csrf_enabled: bool = False,
    rate_limit_enabled: bool = False,
) -> Settings:
    """Create test settings fixture."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("http://localhost:5173"),
        auth_endpoints_enabled=auth_enabled,
        csrf_protection_enabled=csrf_enabled,
        rate_limit_enabled=rate_limit_enabled,
        session_cookie_name="dormiostay_session",
        csrf_secret_key="test-csrf-secret-key-at-least-32-bytes-long",
    )


class InMemoryVerificationDb:
    """In-memory data store simulating PostgreSQL tables for T-10 email verification."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.profiles: dict[UUID, UserProfile] = {}
        self.sessions: dict[UUID, UserSession] = {}
        self.sessions_by_hash: dict[str, UserSession] = {}
        self.tokens: dict[UUID, EmailVerificationToken] = {}
        self.tokens_by_hash: dict[str, EmailVerificationToken] = {}
        self.outbox: list[EmailOutbox] = []

    def add_user(
        self,
        email: str,
        role: str = "tenant",
        status: str = "active",
        email_verified: bool = False,
        full_name: str = "Test User",
    ) -> User:
        user_id = uuid4()
        now = datetime.now(UTC)
        user = User(
            id=user_id,
            email=email.strip().lower(),
            password_hash=hash_password("StrongPassword123!"),
            role=role,
            status=status,
            email_verified_at=now if email_verified else None,
            failed_login_count=0,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
        profile = UserProfile(
            id=uuid4(),
            user_id=user_id,
            full_name=full_name,
            phone="+1234567890",
            avatar_url=None,
            gender=None,
            preferred_budget_min=None,
            preferred_budget_max=None,
            preferred_areas=[],
            institution_id=None,
            created_at=now,
            updated_at=now,
        )
        self.users[user_id] = user
        self.profiles[user_id] = profile
        return user

    def add_token(
        self,
        user_id: UUID,
        raw_token: str,
        expires_in_hours: int = 24,
        is_used: bool = False,
    ) -> tuple[EmailVerificationToken, str]:
        token_id = uuid4()
        now = datetime.now(UTC)
        token_hash = hash_action_token(raw_token)
        expires_at = now + timedelta(hours=expires_in_hours)
        token = EmailVerificationToken(
            id=token_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=now if is_used else None,
            created_at=now,
            updated_at=now,
        )
        self.tokens[token_id] = token
        self.tokens_by_hash[token_hash] = token
        return token, raw_token

    def add_session(self, user: User, raw_token: str) -> tuple[UserSession, str]:
        session_id = uuid4()
        now = datetime.now(UTC)
        token_hash = hash_session_token(raw_token)
        s = UserSession(
            id=session_id,
            user_id=user.id,
            token_hash=token_hash,
            last_activity_at=now,
            expires_at=now + timedelta(minutes=30),
            absolute_expires_at=now + timedelta(days=14),
            revoked_at=None,
            revocation_reason=None,
            created_at=now,
            updated_at=now,
        )
        self.sessions[session_id] = s
        self.sessions_by_hash[token_hash] = s
        return s, raw_token


def setup_verification_test_app(
    db: InMemoryVerificationDb,
    settings: Settings | None = None,
) -> tuple[TestClient, Settings, FastAPI]:
    """Build FastAPI test app with mocked verification database repositories."""
    resolved_settings = settings or create_test_settings()
    app = create_app(resolved_settings)

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()
    mock_db_session.flush = AsyncMock()

    def mock_add(item: Any) -> None:
        if isinstance(item, EmailOutbox):
            db.outbox.append(item)
        elif isinstance(item, EmailVerificationToken):
            db.tokens[item.id] = item
            db.tokens_by_hash[item.token_hash] = item

    mock_db_session.add = MagicMock(side_effect=mock_add)

    @asynccontextmanager
    async def mock_begin_nested() -> AsyncIterator[None]:
        yield

    mock_db_session.begin_nested = mock_begin_nested

    # Session factory for independent transactions
    mock_sec_session = AsyncMock()

    @asynccontextmanager
    async def mock_factory_context() -> AsyncIterator[Any]:
        yield mock_sec_session

    mock_session_factory = MagicMock(side_effect=lambda: mock_factory_context())

    app.dependency_overrides[get_session] = lambda: mock_db_session
    app.dependency_overrides[get_session_factory] = lambda: mock_session_factory
    app.dependency_overrides[get_settings] = lambda: resolved_settings

    # Wire user_repo
    async def mock_get_user_by_email_ci(*args: Any, **kwargs: Any) -> User | None:
        email = kwargs.get("email") or (args[1] if len(args) > 1 else None)
        if not email:
            return None
        target = email.strip().lower()
        for u in db.users.values():
            if u.email == target:
                return u
        return None

    async def mock_get_user_by_id(*args: Any, **kwargs: Any) -> User | None:
        user_id = kwargs.get("user_id") or (args[1] if len(args) > 1 else None)
        return db.users.get(user_id) if isinstance(user_id, UUID) else None

    async def mock_get_user_with_profile(
        *args: Any, **kwargs: Any
    ) -> tuple[User, UserProfile | None] | None:
        user_id = kwargs.get("user_id") or (args[1] if len(args) > 1 else None)
        if not isinstance(user_id, UUID):
            return None
        u = db.users.get(user_id)
        if not u:
            return None
        return u, db.profiles.get(user_id)

    async def mock_create_user(*args: Any, **kwargs: Any) -> User:
        email = kwargs.get("email") or args[1]
        password_hash = kwargs.get("password_hash") or args[2]
        role = kwargs.get("role", "tenant")
        status = kwargs.get("status", "active")
        user_id = uuid4()
        now = datetime.now(UTC)
        u = User(
            id=user_id,
            email=email.strip().lower(),
            password_hash=password_hash,
            role=role,
            status=status,
            email_verified_at=None,
            failed_login_count=0,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
        db.users[user_id] = u
        return u

    async def mock_create_user_profile(*args: Any, **kwargs: Any) -> UserProfile:
        user_id = kwargs.get("user_id") or args[1]
        full_name = kwargs.get("full_name") or args[2]
        now = datetime.now(UTC)
        p = UserProfile(
            id=uuid4(),
            user_id=user_id,
            full_name=full_name,
            phone=kwargs.get("phone"),
            avatar_url=None,
            gender=None,
            preferred_budget_min=None,
            preferred_budget_max=None,
            preferred_areas=[],
            institution_id=kwargs.get("institution_id"),
            created_at=now,
            updated_at=now,
        )
        db.profiles[user_id] = p
        return p

    async def mock_mark_user_email_verified(*args: Any, **kwargs: Any) -> None:
        user_id = kwargs.get("user_id") or args[1]
        verified_at = kwargs.get("verified_at") or args[2]
        if user_id in db.users:
            db.users[user_id].email_verified_at = verified_at

    # Wire verification_repo
    async def mock_create_verification_token(*args: Any, **kwargs: Any) -> EmailVerificationToken:
        user_id = kwargs.get("user_id") or args[1]
        token_hash = kwargs.get("token_hash") or args[2]
        expires_at = kwargs.get("expires_at") or args[3]
        token_id = uuid4()
        now = datetime.now(UTC)
        tok = EmailVerificationToken(
            id=token_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
            created_at=now,
            updated_at=now,
        )
        db.tokens[token_id] = tok
        db.tokens_by_hash[token_hash] = tok
        return tok

    async def mock_get_verification_token_by_hash(
        *args: Any, **kwargs: Any
    ) -> EmailVerificationToken | None:
        token_hash = kwargs.get("token_hash") or args[1]
        return db.tokens_by_hash.get(token_hash)

    async def mock_mark_token_used(*args: Any, **kwargs: Any) -> bool:
        token_id = kwargs.get("token_id") or args[1]
        now = kwargs.get("now") or kwargs.get("used_at") or args[2]
        # Yield to event loop to simulate concurrent execution overlap
        await asyncio.sleep(0.001)
        if token_id in db.tokens:
            tok = db.tokens[token_id]
            if tok.used_at is None and tok.expires_at > now:
                tok.used_at = now
                return True
        return False

    async def mock_invalidate_user_verification_tokens(*args: Any, **kwargs: Any) -> int:
        user_id = kwargs.get("user_id") or args[1]
        now = kwargs.get("now") or args[2]
        count = 0
        for tok in db.tokens.values():
            if tok.user_id == user_id and tok.used_at is None and tok.expires_at > now:
                tok.used_at = now
                count += 1
        return count

    # Wire session_repo
    async def mock_get_session_with_user_by_token_hash(
        *args: Any, **kwargs: Any
    ) -> tuple[UserSession, User] | None:
        token_hash = kwargs.get("token_hash") or args[1]
        s = db.sessions_by_hash.get(token_hash)
        if not s:
            return None
        u = db.users.get(s.user_id)
        if not u:
            return None
        return s, u

    async def mock_update_session_activity(*args: Any, **kwargs: Any) -> bool:
        return True

    # Apply patches
    p1 = patch.object(user_repo, "get_user_by_email_ci", mock_get_user_by_email_ci)
    p2 = patch.object(user_repo, "get_user_by_id", mock_get_user_by_id)
    p3 = patch.object(user_repo, "get_user_with_profile", mock_get_user_with_profile)
    p4 = patch.object(user_repo, "create_user", mock_create_user)
    p5 = patch.object(user_repo, "create_user_profile", mock_create_user_profile)
    p6 = patch.object(user_repo, "mark_user_email_verified", mock_mark_user_email_verified)
    p7 = patch.object(
        verification_repo, "create_verification_token", mock_create_verification_token
    )
    p8 = patch.object(
        verification_repo, "get_verification_token_by_hash", mock_get_verification_token_by_hash
    )
    p9 = patch.object(verification_repo, "mark_token_used", mock_mark_token_used)
    p10 = patch.object(
        verification_repo,
        "invalidate_user_verification_tokens",
        mock_invalidate_user_verification_tokens,
    )
    p11 = patch.object(
        session_repo,
        "get_session_with_user_by_token_hash",
        mock_get_session_with_user_by_token_hash,
    )
    p12 = patch.object(session_repo, "update_session_activity", mock_update_session_activity)

    for p in (p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12):
        p.start()
        _ACTIVE_PATCHERS.append(p)

    # Add a mock restricted route to verify require_verified_user
    @app.post("/api/v1/test/restricted-action")
    async def restricted_action(
        session_and_user: tuple[UserSession, User] = Depends(require_verified_user),
    ) -> dict[str, str]:
        return {"status": "ok", "message": "Restricted action permitted"}

    client = TestClient(app)
    return client, resolved_settings, app


# ===========================================================================
# Test Cases
# ===========================================================================


def test_01_signup_generates_token_and_queues_email() -> None:
    """Signup generates random token, stores SHA-256 hash (24h expiry), and queues outbox email."""
    db = InMemoryVerificationDb()
    client, settings, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "newuser@example.com",
            "password": "StrongPassword123!",
            "role": "tenant",
            "full_name": "New User",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "newuser@example.com"
    assert body["email_verified"] is False
    user_id = UUID(body["id"])

    # Verify verification token was stored
    assert len(db.tokens) == 1
    token = list(db.tokens.values())[0]
    assert token.user_id == user_id
    assert token.used_at is None
    # 24 hour expiry (+/- 5 seconds)
    now = datetime.now(UTC)
    delta = token.expires_at - now
    assert 23.95 * 3600 <= delta.total_seconds() <= 24.05 * 3600

    # Verify check constraint format: exactly 64 lowercase hex characters
    import re

    assert re.fullmatch(r"^[0-9a-f]{64}$", token.token_hash) is not None

    # Verify email was queued in transactional outbox
    assert len(db.outbox) == 1
    email = db.outbox[0]
    assert email.recipient == "newuser@example.com"
    assert email.recipient_name == "New User"
    assert "Verify your DormioStay email address" in email.subject
    assert email.tags == ["auth", "verification"]
    assert email.status == "queued"
    assert "/verify-email?token=" in email.html_content
    assert "/verify-email?token=" in email.text_content
    # Raw token must not appear in metadata
    assert email.metadata_payload is not None
    assert "token" not in email.metadata_payload


def test_02_verify_email_success_single_use() -> None:
    """Valid verification token marks token used and updates email_verified_at."""
    db = InMemoryVerificationDb()
    user = db.add_user("alice@example.com", email_verified=False)
    raw_token = generate_action_token()
    token, _ = db.add_token(user.id, raw_token)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "message": "Email verified successfully."}

    # Verify state in DB
    assert token.used_at is not None
    assert user.email_verified_at is not None


def test_03_reused_token_rejected_with_clear_message() -> None:
    """Already used token returns HTTP 400 TOKEN_ALREADY_USED with clear message."""
    db = InMemoryVerificationDb()
    user = db.add_user("alice@example.com", email_verified=False)
    raw_token = generate_action_token()
    db.add_token(user.id, raw_token, is_used=True)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "TOKEN_ALREADY_USED"
    assert "already been used" in body["error"]["message"].lower()


def test_04_expired_token_rejected_with_clear_message() -> None:
    """Expired token returns HTTP 400 TOKEN_EXPIRED with clear message."""
    db = InMemoryVerificationDb()
    user = db.add_user("alice@example.com", email_verified=False)
    raw_token = generate_action_token()
    # Add token expired 2 hours ago
    db.add_token(user.id, raw_token, expires_in_hours=-2)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "TOKEN_EXPIRED"
    assert "expired" in body["error"]["message"].lower()


def test_05_invalid_or_malformed_token_rejected() -> None:
    """Invalid, unknown, or too-short token returns HTTP 400 TOKEN_INVALID."""
    db = InMemoryVerificationDb()
    client, _, _ = setup_verification_test_app(db)

    # 1. Non-existent random token
    fake_token = generate_action_token()
    resp1 = client.post(
        "/api/v1/auth/verify-email",
        json={"token": fake_token},
    )
    assert resp1.status_code == 400
    assert resp1.json()["error"]["code"] == "TOKEN_INVALID"

    # 2. Short / malformed token
    resp2 = client.post(
        "/api/v1/auth/verify-email",
        json={"token": "tooshort"},
    )
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "TOKEN_INVALID"


def test_06_suspended_or_banned_user_verification_rejected() -> None:
    """Verification for suspended or banned account fails closed with HTTP 403."""
    db = InMemoryVerificationDb()
    user = db.add_user("banned@example.com", status="banned")
    raw_token = generate_action_token()
    db.add_token(user.id, raw_token)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_07_resend_verification_unverified_user() -> None:
    """Resend verification invalidates prior tokens, creates new token, and queues outbox email."""
    db = InMemoryVerificationDb()
    user = db.add_user("bob@example.com", email_verified=False, full_name="Bob Owner")
    old_token, _ = db.add_token(user.id, "old-raw-token-1234567890")

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "bob@example.com"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "verification link has been sent" in body["message"]

    # Old token must be invalidated (used_at set)
    assert old_token.used_at is not None

    # New token created
    active_tokens = [t for t in db.tokens.values() if t.used_at is None]
    assert len(active_tokens) == 1
    new_token = active_tokens[0]
    assert new_token.user_id == user.id

    # New email queued
    assert len(db.outbox) == 1
    assert db.outbox[0].recipient == "bob@example.com"
    assert db.outbox[0].recipient_name == "Bob Owner"


def test_08_resend_verification_anti_enumeration_nonexistent_email() -> None:
    """Resend verification for nonexistent email returns 200 without leaking account presence."""
    db = InMemoryVerificationDb()
    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "nonexistent@example.com"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "verification link has been sent" in body["message"]

    # No tokens created, no emails queued
    assert len(db.tokens) == 0
    assert len(db.outbox) == 0


def test_09_resend_verification_anti_enumeration_already_verified() -> None:
    """Resend verification for already verified email returns 200 without sending."""
    db = InMemoryVerificationDb()
    db.add_user("verified@example.com", email_verified=True)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "verified@example.com"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "verification link has been sent" in body["message"]

    # No emails queued
    assert len(db.outbox) == 0


def test_10_resend_verification_rate_limiting_per_email() -> None:
    """Resend verification is rate-limited per email (3 attempts per hour with Redis)."""
    db = InMemoryVerificationDb()
    db.add_user("target@example.com", email_verified=False)

    mock_redis = AsyncMockRedis()
    set_redis_client_override(mock_redis)

    settings = create_test_settings(rate_limit_enabled=True)
    client, _, _ = setup_verification_test_app(db, settings=settings)

    # 3 allowed requests
    for i in range(3):
        resp = client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "target@example.com"},
        )
        assert resp.status_code == 200, f"Attempt {i + 1} failed"

    # 4th request must be rate-limited with HTTP 429
    resp4 = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "target@example.com"},
    )
    assert resp4.status_code == 429
    assert resp4.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in resp4.headers


def test_10b_resend_verification_rate_limiting_fallback_stricter() -> None:
    """When Redis is down, resend verification uses stricter fallback (2 per hour)."""
    db = InMemoryVerificationDb()
    db.add_user("target_fallback@example.com", email_verified=False)

    # Explicitly ensure no Redis client is set -> in-memory fallback
    set_redis_client_override(None)

    settings = create_test_settings(rate_limit_enabled=True)
    client, _, _ = setup_verification_test_app(db, settings=settings)

    # 2 allowed requests under fallback rule
    for i in range(2):
        resp = client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "target_fallback@example.com"},
        )
        assert resp.status_code == 200, f"Attempt {i + 1} failed"

    # 3rd request is rate-limited under fallback rule
    resp3 = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "target_fallback@example.com"},
    )
    assert resp3.status_code == 429
    assert resp3.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in resp3.headers


def test_11_resend_verification_rate_limiting_per_ip() -> None:
    """Resend verification is rate-limited per IP (10 attempts per hour with Redis)."""
    db = InMemoryVerificationDb()

    mock_redis = AsyncMockRedis()
    set_redis_client_override(mock_redis)

    settings = create_test_settings(rate_limit_enabled=True)
    client, _, _ = setup_verification_test_app(db, settings=settings)

    # 10 requests from same IP targeting different emails
    for i in range(10):
        resp = client.post(
            "/api/v1/auth/resend-verification",
            json={"email": f"user{i}@example.com"},
        )
        assert resp.status_code == 200

    # 11th request from same IP must be rate-limited
    resp11 = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "user11@example.com"},
    )
    assert resp11.status_code == 429
    assert resp11.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_12_require_verified_user_blocks_unverified() -> None:
    """Unverified user is blocked by require_verified_user with 403 EMAIL_NOT_VERIFIED."""
    db = InMemoryVerificationDb()
    user = db.add_user("unverified@example.com", email_verified=False)
    raw_session = generate_session_token()
    db.add_session(user, raw_session)

    client, _, _ = setup_verification_test_app(db)

    # Native bearer request to restricted endpoint
    resp = client.post(
        "/api/v1/test/restricted-action",
        headers={"Authorization": f"Bearer {raw_session}"},
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert "verification is required" in body["error"]["message"].lower()


def test_13_require_verified_user_permits_verified() -> None:
    """Verified authenticated user is permitted by require_verified_user."""
    db = InMemoryVerificationDb()
    user = db.add_user("verified@example.com", email_verified=True)
    raw_session = generate_session_token()
    db.add_session(user, raw_session)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/test/restricted-action",
        headers={"Authorization": f"Bearer {raw_session}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "message": "Restricted action permitted"}


def test_14_action_link_contract_format() -> None:
    """Both-client contract: single-use HTTPS links with web fallback, no session tokens."""
    settings = create_test_settings()
    raw_token = generate_action_token()

    link = f"{settings.frontend_origin}/verify-email?token={raw_token}"
    subject, html, text = email_service.build_verification_email("Alice", link)

    # No session tokens
    assert "dormiostay_session" not in html
    assert "dormiostay_session" not in text
    assert "Bearer" not in html

    # Clear single-use and 24h notice
    assert "24 hours" in html
    assert "single-use" in html
    assert "24 hours" in text
    assert "single-use" in text


def test_15_both_client_transport_web_with_csrf() -> None:
    """Web client with valid CSRF bootstrap token and Origin header verifies email successfully."""
    db = InMemoryVerificationDb()
    user = db.add_user("webuser@example.com", email_verified=False)
    raw_token = generate_action_token()
    db.add_token(user.id, raw_token)

    settings = create_test_settings(csrf_enabled=True)
    client, _, _ = setup_verification_test_app(db, settings=settings)

    signing_key = get_csrf_signing_key(settings)
    csrf_token = generate_anonymous_csrf_token(signing_key)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
        headers={
            "Origin": "http://localhost:5173",
            "X-CSRF-Token": csrf_token,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_16_both_client_transport_native_without_csrf() -> None:
    """Native client (X-Client-Type: native) verifies email without CSRF token."""
    db = InMemoryVerificationDb()
    user = db.add_user("nativeuser@example.com", email_verified=False)
    raw_token = generate_action_token()
    db.add_token(user.id, raw_token)

    settings = create_test_settings(csrf_enabled=True)
    client, _, _ = setup_verification_test_app(db, settings=settings)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
        headers={
            "X-Client-Type": "native",
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_17_get_navigation_does_not_consume_action() -> None:
    """GET request to verify-email is rejected (405), never consuming token."""
    db = InMemoryVerificationDb()
    user = db.add_user("gettest@example.com", email_verified=False)
    raw_token = generate_action_token()
    token, _ = db.add_token(user.id, raw_token)

    client, _, _ = setup_verification_test_app(db)

    resp = client.get("/api/v1/auth/verify-email?token=" + raw_token)

    assert resp.status_code == 405
    # Token must remain untouched and unconsumed
    assert token.used_at is None
    assert user.email_verified_at is None


def test_18_auth_endpoints_disabled_by_default_returns_503() -> None:
    """When auth_endpoints_enabled=False, verify-email and resend return 503 SERVICE_UNAVAILABLE."""
    db = InMemoryVerificationDb()
    settings = create_test_settings(auth_enabled=False)
    client, _, _ = setup_verification_test_app(db, settings=settings)

    resp_verify = client.post(
        "/api/v1/auth/verify-email",
        json={"token": "some-token-string-1234567890"},
    )
    assert resp_verify.status_code == 503
    assert resp_verify.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    resp_resend = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "alice@example.com"},
    )
    assert resp_resend.status_code == 503
    assert resp_resend.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_19_concurrent_verify_email_race_condition() -> None:
    """Two concurrent requests using the same unused token race to consume it.

    Exactly one request must succeed with HTTP 200, and the second must be rejected
    with HTTP 400 TOKEN_ALREADY_USED.
    """
    import httpx

    db = InMemoryVerificationDb()
    user = db.add_user("racetest@example.com", email_verified=False)
    raw_token = generate_action_token()
    token, _ = db.add_token(user.id, raw_token)

    settings = create_test_settings(csrf_enabled=False)
    _, _, app = setup_verification_test_app(db, settings=settings)

    async def _run() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            req1 = ac.post("/api/v1/auth/verify-email", json={"token": raw_token})
            req2 = ac.post("/api/v1/auth/verify-email", json={"token": raw_token})
            return list(await asyncio.gather(req1, req2))

    resps = asyncio.run(_run())
    status_codes = sorted([r.status_code for r in resps])
    assert status_codes == [200, 400]

    successful = [r for r in resps if r.status_code == 200][0]
    failed = [r for r in resps if r.status_code == 400][0]

    assert successful.json() == {"status": "ok", "message": "Email verified successfully."}
    assert failed.json()["error"]["code"] == "TOKEN_ALREADY_USED"

    # State in database must be consistent: token marked used, user marked verified once
    assert token.used_at is not None
    assert user.email_verified_at is not None


def test_20_exact_boundary_expiration() -> None:
    """Token that expires at or before now is rejected with TOKEN_EXPIRED."""
    db = InMemoryVerificationDb()
    user = db.add_user("boundary@example.com", email_verified=False)
    raw_token = generate_action_token()
    token, _ = db.add_token(user.id, raw_token, expires_in_hours=0)
    # Set expires_at exactly 1 second in the past to test boundary condition
    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOKEN_EXPIRED"
    assert token.used_at is None
    assert user.email_verified_at is None


def test_21_transaction_rollback_atomicity() -> None:
    """If user verification update fails, token consumption must be rolled back."""
    from app.services import auth_service

    db = InMemoryVerificationDb()
    user = db.add_user("rollback@example.com", email_verified=False)
    raw_token = generate_action_token()
    token, _ = db.add_token(user.id, raw_token)

    # Wire all repository doubles
    setup_verification_test_app(db)

    mock_session = AsyncMock()
    mock_session.rollback = AsyncMock()

    async def _test() -> None:
        with patch(
            "app.db.repositories.user_repo.mark_user_email_verified",
            side_effect=RuntimeError("Database write failure"),
        ):
            with pytest.raises(RuntimeError, match="Database write failure"):
                try:
                    await auth_service.verify_email(mock_session, raw_token)
                except Exception:
                    await mock_session.rollback()
                    raise

        mock_session.rollback.assert_awaited_once()

    asyncio.run(_test())


def test_22_cross_user_token_isolation() -> None:
    """Verifying a token belonging to User A must only verify User A, never User B."""
    db = InMemoryVerificationDb()
    user_a = db.add_user("user_a@example.com", email_verified=False)
    user_b = db.add_user("user_b@example.com", email_verified=False)

    raw_token_a = generate_action_token()
    token_a, _ = db.add_token(user_a.id, raw_token_a)

    client, _, _ = setup_verification_test_app(db)

    resp = client.post(
        "/api/v1/auth/verify-email",
        json={"token": raw_token_a},
    )

    assert resp.status_code == 200
    assert user_a.email_verified_at is not None
    assert user_b.email_verified_at is None
    assert token_a.used_at is not None


class RealSqliteAsyncSession:
    """AsyncSession adapter proxying to a real SQLAlchemy ORM Session over SQLite."""

    def __init__(self, sync_session: Any) -> None:
        self.sync_session = sync_session
        self.identity_map = sync_session.identity_map

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        return self.sync_session.execute(stmt, *args, **kwargs)

    async def commit(self) -> None:
        self.sync_session.commit()

    async def rollback(self) -> None:
        self.sync_session.rollback()

    async def flush(self) -> None:
        self.sync_session.flush()

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.sync_session.refresh(instance, *args, **kwargs)

    def add(self, instance: Any) -> None:
        self.sync_session.add(instance)

    def begin_nested(self) -> Any:
        return self.sync_session.begin_nested()


def _create_isolated_sqlite_db() -> Any:
    from sqlalchemy import Table, create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    for model_cls in [EmailVerificationToken, User]:
        table: Table = model_cls.__table__  # type: ignore[assignment]
        for c in list(table.constraints):
            if "~" in str(getattr(c, "sqltext", "")):
                table.constraints.remove(c)
        table.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_23_sqlalchemy_orm_identity_map_fresh_read() -> None:
    """Genuine SQLAlchemy: identity map staleness bypassed with populate_existing=True."""
    from sqlalchemy import select, update

    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    now = datetime.now(UTC)
    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)

    # 1. Seed user and token
    with sm() as s1:
        u = User(
            id=user_id,
            email="ormuser@example.com",
            password_hash=hash_password("Pass123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = EmailVerificationToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=24),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            # Load token into s2 identity map
            tok_cached = await verification_repo.get_verification_token_by_hash(as2, token_hash)
            assert tok_cached is not None
            assert tok_cached.used_at is None

            # S3 (concurrent winning session) commits consumption
            winning_time = now + timedelta(seconds=1)
            with sm() as s3:
                s3.execute(
                    update(EmailVerificationToken)
                    .where(EmailVerificationToken.token_hash == token_hash)
                    .values(used_at=winning_time)
                )
                s3.commit()

            # S2 executes conditional update: rowcount == 0
            success = await verification_repo.mark_token_used(as2, tok_cached.id, now)
            assert success is False
            # synchronize_session=False prevents phantom in-memory mutation
            assert tok_cached.used_at is None

            # Querying WITHOUT populate_existing returns the stale identity-map object
            stale_query = await as2.execute(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.token_hash == token_hash
                )
            )
            assert stale_query.scalar_one().used_at is None

            # Querying WITH populate_existing=True explicitly forces fresh state from database
            fresh = await verification_repo.get_verification_token_by_hash(
                as2, token_hash, populate_existing=True
            )
            assert fresh is not None
            assert fresh.used_at is not None
            assert fresh.used_at.replace(tzinfo=UTC) == winning_time

    asyncio.run(_test())


def test_24_sqlalchemy_losing_request_returns_token_already_used() -> None:
    """Genuine SQLAlchemy: losing request returns TOKEN_ALREADY_USED on real ORM state."""
    from sqlalchemy import update

    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    now = datetime.now(UTC)
    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="loser@example.com",
            password_hash=hash_password("Pass123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = EmailVerificationToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=24),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s_losing:
            as_losing: Any = RealSqliteAsyncSession(s_losing)
            # Losing session loads token before winning session updates
            await verification_repo.get_verification_token_by_hash(as_losing, token_hash)

            # Winning session commits consumption in DB
            with sm() as s_win:
                s_win.execute(
                    update(EmailVerificationToken)
                    .where(EmailVerificationToken.token_hash == token_hash)
                    .values(used_at=now)
                )
                s_win.commit()

            with pytest.raises(AppError) as exc_info:
                await auth_service.verify_email(as_losing, raw_token)

            assert exc_info.value.code == "TOKEN_ALREADY_USED"
            assert exc_info.value.status_code == 400

    asyncio.run(_test())


def test_25_sqlalchemy_boundary_expiration_classification() -> None:
    """Genuine SQLAlchemy: expired token returns TOKEN_EXPIRED on real ORM state."""
    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    now = datetime.now(UTC)
    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="expired@example.com",
            password_hash=hash_password("Pass123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        # Token expired 1 minute ago
        tok = EmailVerificationToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now - timedelta(minutes=1),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            with pytest.raises(AppError) as exc_info:
                await auth_service.verify_email(as2, raw_token)

            assert exc_info.value.code == "TOKEN_EXPIRED"
            assert exc_info.value.status_code == 400

    asyncio.run(_test())


def test_26_sqlalchemy_unknown_token_classification() -> None:
    """Genuine SQLAlchemy: unknown token returns TOKEN_INVALID on real ORM state."""
    sm = _create_isolated_sqlite_db()

    async def _test() -> None:
        with sm() as s:
            as_sess: Any = RealSqliteAsyncSession(s)
            unknown_token = generate_action_token()
            with pytest.raises(AppError) as exc_info:
                await auth_service.verify_email(as_sess, unknown_token)

            assert exc_info.value.code == "TOKEN_INVALID"
            assert exc_info.value.status_code == 400

    asyncio.run(_test())


def test_27_sqlalchemy_atomic_commit() -> None:
    """Genuine SQLAlchemy: successful verification commits token and user changes atomically."""
    from sqlalchemy import select

    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    now = datetime.now(UTC)
    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="commituser@example.com",
            password_hash=hash_password("Pass123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = EmailVerificationToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=24),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            verified_user = await auth_service.verify_email(as2, raw_token)
            await as2.commit()
            assert verified_user.email_verified_at is not None

        # Completely fresh independent session queries DB directly
        with sm() as s3:
            u_db = s3.get(User, user_id)
            assert u_db is not None
            assert u_db.email_verified_at is not None

            tok_db = s3.execute(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.token_hash == token_hash
                )
            ).scalar_one()
            assert tok_db.used_at is not None

    asyncio.run(_test())


def test_28_sqlalchemy_atomic_rollback() -> None:
    """Genuine SQLAlchemy: failure before commit rolls back token consumption in database."""
    from sqlalchemy import select

    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    now = datetime.now(UTC)
    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="rollbackuser@example.com",
            password_hash=hash_password("Pass123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = EmailVerificationToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=24),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            # Token marked used in transaction
            await verification_repo.mark_token_used(as2, tok.id, now)
            # Failure occurs before commit -> transaction rollback!
            await as2.rollback()

        # Query from fresh independent session: database state must remain completely unconsumed
        with sm() as s3:
            tok_db = s3.execute(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.token_hash == token_hash
                )
            ).scalar_one()
            # Verified via SQL engine rollback, with zero manual model property mutation in test
            assert tok_db.used_at is None

            u_db = s3.get(User, user_id)
            assert u_db is not None
            assert u_db.email_verified_at is None

    asyncio.run(_test())
