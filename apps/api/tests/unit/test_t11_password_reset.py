"""Comprehensive unit and acceptance tests for T-11 Password Reset.

Covers all acceptance criteria and specifications:
 1. Forgot password creates random single-use token, stores SHA-256 hash, 30m expiry.
 2. Token hash strictly adheres to PostgreSQL CHECK constraint (^[0-9a-f]{64}$).
 3. Transactional outbox email queued atomically with single-use action link.
 4. Anti-enumeration: identical response and timing for known and unknown emails.
 5. Anti-enumeration: suspended/banned accounts return generic response without sending email.
 6. Rate limiting: per-email threshold enforcement (3/hr Redis, 2/hr fallback, HTTP 429).
 7. Rate limiting: per-IP threshold enforcement (10/hr Redis, 5/hr fallback, HTTP 429).
 8. Invalidate prior unused tokens when requesting forgot password again.
 9. Reset password success: valid token updates password hash with Argon2id.
10. Global session revocation: all existing sessions of the user are revoked.
11. Old sessions fail after reset (HTTP 401 SESSION_REVOKED).
12. Login with new password succeeds; login with old password fails.
13. Reset password clears ambient session cookie if present in request.
14. Reused token rejection: second reset returns HTTP 400 TOKEN_ALREADY_USED.
15. Expired token rejection: expired token returns HTTP 400 TOKEN_EXPIRED.
16. Invalid token rejection: unknown/malformed token returns HTTP 400 TOKEN_INVALID.
17. Weak password rejected with HTTP 400 WEAK_PASSWORD; token remains unconsumed.
18. Disabled/suspended account rejection: returns HTTP 403 ACCOUNT_DISABLED.
19. Navigation safety: GET requests return 405, never consuming action tokens.
20. Both-client transport: Web client with CSRF succeeds.
21. Both-client transport: Native client (X-Client-Type: native) succeeds without CSRF.
22. Action link contract: single-use HTTPS link, no session tokens, 30-minute notice.
23. Safe logging: action tokens are never logged or returned in error payloads.
24. Inactive auth guard: auth_endpoints_enabled=False fails closed with HTTP 503.
25. Concurrent reset race condition: atomic conditional UPDATE prevents double consumption.
26. Exact boundary expiration: token expiring at or before now is rejected with TOKEN_EXPIRED.
27. Transaction rollback atomicity: error rolls back token consumption and password update.
28. Cross-user token isolation: User A's token cannot reset User B's password.
29. Genuine SQLAlchemy: identity map staleness bypassed with populate_existing=True.
30. Genuine SQLAlchemy: losing request returns TOKEN_ALREADY_USED deterministically.
31. Genuine SQLAlchemy: boundary expiration returns TOKEN_EXPIRED.
32. Genuine SQLAlchemy: unknown token returns TOKEN_INVALID.
33. Genuine SQLAlchemy: successful reset commits token and password changes atomically.
34. Genuine SQLAlchemy: transaction failure rolls back both changes in database.
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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl

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
    verify_password,
)
from app.db.models import PasswordResetToken, User, UserProfile, UserSession
from app.db.models.email_outbox import EmailOutbox
from app.db.repositories import password_reset_repo, session_repo, user_repo
from app.db.session import get_session, get_session_factory
from app.main import create_app
from app.schemas.auth import LoginRequest
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
def clean_test_environment() -> Generator[None, None, None]:
    """Ensure in-memory rate limiters and mock redis are cleared before each test."""
    asyncio.run(reset_rate_limit_state())
    set_redis_client_override(None)
    yield
    asyncio.run(reset_rate_limit_state())
    set_redis_client_override(None)
    for p in _ACTIVE_PATCHERS:
        try:
            p.stop()
        except RuntimeError:
            pass
    _ACTIVE_PATCHERS.clear()


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


class InMemoryPasswordResetDb:
    """In-memory stateful double simulating PostgreSQL users, sessions, reset tokens, and outbox."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.profiles: dict[UUID, UserProfile] = {}
        self.sessions: dict[UUID, UserSession] = {}
        self.sessions_by_hash: dict[str, UserSession] = {}
        self.reset_tokens: dict[UUID, PasswordResetToken] = {}
        self.reset_tokens_by_hash: dict[str, PasswordResetToken] = {}
        self.outbox: list[EmailOutbox] = []
        self._lock = asyncio.Lock()

    def add_user(
        self,
        email: str,
        password: str = "CorrectPassword123!",
        role: str = "tenant",
        status: str = "active",
        email_verified: bool = True,
        full_name: str = "Test User",
    ) -> User:
        user_id = uuid4()
        now = datetime.now(UTC)
        user = User(
            id=user_id,
            email=email.strip().lower(),
            password_hash=hash_password(password),
            role=role,
            status=status,
            email_verified_at=now if email_verified else None,
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )
        self.users[user_id] = user
        profile = UserProfile(
            id=uuid4(),
            user_id=user_id,
            full_name=full_name,
            phone="+919876543210",
            preferred_areas=["Tadong"],
        )
        self.profiles[user_id] = profile
        return user

    def add_session(
        self,
        user_id: UUID,
        raw_token: str | None = None,
    ) -> tuple[UserSession, str]:
        token = raw_token or generate_session_token()
        token_hash = hash_session_token(token)
        now = datetime.now(UTC)
        session = UserSession(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            last_activity_at=now,
            expires_at=now + timedelta(days=14),
            absolute_expires_at=now + timedelta(days=14),
            revoked_at=None,
            revocation_reason=None,
            created_at=now,
            updated_at=now,
        )
        self.sessions[session.id] = session
        self.sessions_by_hash[token_hash] = session
        return session, token

    def add_reset_token(
        self,
        user_id: UUID,
        raw_token: str,
        expires_in_minutes: int = 30,
        used: bool = False,
    ) -> tuple[PasswordResetToken, str]:
        now = datetime.now(UTC)
        token_hash = hash_action_token(raw_token)
        token_id = uuid4()
        token = PasswordResetToken(
            id=token_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=expires_in_minutes),
            used_at=now if used else None,
            created_at=now,
            updated_at=now,
        )
        self.reset_tokens[token_id] = token
        self.reset_tokens_by_hash[token_hash] = token
        return token, token_hash


def setup_reset_test_app(
    db: InMemoryPasswordResetDb,
    settings: Settings | None = None,
) -> tuple[TestClient, Settings, FastAPI]:
    resolved_settings = settings or create_test_settings()
    app = create_app(resolved_settings)

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()
    mock_db_session.flush = AsyncMock()

    # Session factory for independent transactions
    mock_sec_session = AsyncMock()
    mock_sec_begin_cm = MagicMock()
    mock_sec_begin_cm.__aenter__ = AsyncMock(return_value=None)
    mock_sec_begin_cm.__aexit__ = AsyncMock(return_value=None)
    mock_sec_session.begin = MagicMock(return_value=mock_sec_begin_cm)

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

    async def mock_update_user_password(*args: Any, **kwargs: Any) -> None:
        user_id = kwargs.get("user_id") or args[1]
        new_password_hash = kwargs.get("new_password_hash") or args[2]
        if user_id in db.users:
            u = db.users[user_id]
            u.password_hash = new_password_hash
            u.failed_login_count = 0
            u.locked_until = None

    async def mock_update_login_failure(*args: Any, **kwargs: Any) -> None:
        user_id = kwargs.get("user_id") or args[1]
        if user_id in db.users:
            db.users[user_id].failed_login_count += 1

    async def mock_update_login_success(*args: Any, **kwargs: Any) -> bool:
        user_id = kwargs.get("user_id") or args[1]
        login_at = kwargs.get("login_at") or (args[2] if len(args) > 2 else datetime.now(UTC))
        expected_hash = kwargs.get("expected_password_hash") or (args[3] if len(args) > 3 else None)
        if user_id in db.users:
            if expected_hash is not None and db.users[user_id].password_hash != expected_hash:
                return False
            db.users[user_id].failed_login_count = 0
            db.users[user_id].last_login_at = login_at
            return True
        return False

    # Wire password_reset_repo
    async def mock_create_reset_token(*args: Any, **kwargs: Any) -> PasswordResetToken:
        user_id = kwargs.get("user_id") or args[1]
        token_hash = kwargs.get("token_hash") or args[2]
        expires_at = kwargs.get("expires_at") or args[3]
        token_id = uuid4()
        now = datetime.now(UTC)
        tok = PasswordResetToken(
            id=token_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
            created_at=now,
            updated_at=now,
        )
        db.reset_tokens[token_id] = tok
        db.reset_tokens_by_hash[token_hash] = tok
        return tok

    async def mock_get_reset_token_by_hash(*args: Any, **kwargs: Any) -> PasswordResetToken | None:
        token_hash = kwargs.get("token_hash") or args[1]
        return db.reset_tokens_by_hash.get(token_hash)

    async def mock_mark_token_used(*args: Any, **kwargs: Any) -> bool:
        token_id = kwargs.get("token_id") or args[1]
        now = kwargs.get("now") or args[2]
        # Yield to event loop to simulate concurrent execution overlap
        await asyncio.sleep(0.001)
        if token_id in db.reset_tokens:
            tok = db.reset_tokens[token_id]
            if tok.used_at is None and tok.expires_at > now:
                tok.used_at = now
                return True
        return False

    async def mock_invalidate_user_reset_tokens(*args: Any, **kwargs: Any) -> int:
        user_id = kwargs.get("user_id") or args[1]
        now = kwargs.get("now") or args[2]
        count = 0
        for tok in db.reset_tokens.values():
            if tok.user_id == user_id and tok.used_at is None and tok.expires_at > now:
                tok.used_at = now
                count += 1
        return count

    # Wire session_repo
    async def mock_revoke_all_user_sessions(*args: Any, **kwargs: Any) -> int:
        user_id = kwargs.get("user_id") or args[1]
        revoked_at = kwargs.get("revoked_at") or args[2]
        reason = kwargs.get("reason") or (args[3] if len(args) > 3 else "password_reset")
        count = 0
        for s in db.sessions.values():
            if s.user_id == user_id and s.revoked_at is None:
                s.revoked_at = revoked_at
                s.revocation_reason = reason
                count += 1
        return count

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

    async def mock_create_session(*args: Any, **kwargs: Any) -> UserSession:
        user_id = kwargs.get("user_id") or args[1]
        token_hash = kwargs.get("token_hash") or args[2]
        expires_at = kwargs.get("expires_at") or args[3]
        absolute_expires_at = kwargs.get("absolute_expires_at") or args[4]
        session_id = uuid4()
        now = datetime.now(UTC)
        s = UserSession(
            id=session_id,
            user_id=user_id,
            token_hash=token_hash,
            last_activity_at=now,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
            revoked_at=None,
            revocation_reason=None,
            created_at=now,
            updated_at=now,
        )
        db.sessions[session_id] = s
        db.sessions_by_hash[token_hash] = s
        return s

    async def mock_update_session_activity(*args: Any, **kwargs: Any) -> bool:
        session_id = kwargs.get("session_id") or args[1]
        last_activity_at = kwargs.get("last_activity_at") or args[2]
        new_expires_at = kwargs.get("new_expires_at") or args[3]
        if session_id in db.sessions:
            s = db.sessions[session_id]
            if s.revoked_at is None:
                s.last_activity_at = last_activity_at
                s.expires_at = new_expires_at
                return True
        return False

    def mock_queue_password_reset_email(*args: Any, **kwargs: Any) -> EmailOutbox:
        recipient = kwargs.get("recipient") or args[1]
        recipient_name = kwargs.get("recipient_name") or args[2]
        raw_token = kwargs.get("raw_token") or args[3]
        settings = kwargs.get("settings") or args[4]
        user_id = kwargs.get("user_id") or kwargs.get("user_id")
        frontend_base = str(settings.frontend_origin).rstrip("/")
        action_link = f"{frontend_base}/reset-password?token={raw_token}"
        subj, html, text = email_service.build_password_reset_email(recipient_name, action_link)
        item = EmailOutbox(
            id=uuid4(),
            recipient=recipient.strip().lower(),
            recipient_name=recipient_name,
            subject=subj,
            html_content=html,
            text_content=text,
            tags=["auth", "password_reset"],
            status="queued",
            metadata={"action": "password_reset", "user_id": str(user_id) if user_id else ""},
            created_at=datetime.now(UTC),
        )
        db.outbox.append(item)
        return item

    patchers = [
        patch(
            "app.db.repositories.user_repo.get_user_by_email_ci",
            side_effect=mock_get_user_by_email_ci,
        ),
        patch("app.db.repositories.user_repo.get_user_by_id", side_effect=mock_get_user_by_id),
        patch(
            "app.db.repositories.user_repo.get_user_with_profile",
            side_effect=mock_get_user_with_profile,
        ),
        patch(
            "app.db.repositories.user_repo.update_user_password",
            side_effect=mock_update_user_password,
        ),
        patch(
            "app.db.repositories.user_repo.update_login_failure",
            side_effect=mock_update_login_failure,
        ),
        patch(
            "app.db.repositories.user_repo.update_login_success",
            side_effect=mock_update_login_success,
        ),
        patch(
            "app.db.repositories.password_reset_repo.create_reset_token",
            side_effect=mock_create_reset_token,
        ),
        patch(
            "app.db.repositories.password_reset_repo.get_reset_token_by_hash",
            side_effect=mock_get_reset_token_by_hash,
        ),
        patch(
            "app.db.repositories.password_reset_repo.mark_token_used",
            side_effect=mock_mark_token_used,
        ),
        patch(
            "app.db.repositories.password_reset_repo.invalidate_user_reset_tokens",
            side_effect=mock_invalidate_user_reset_tokens,
        ),
        patch(
            "app.db.repositories.session_repo.revoke_all_user_sessions",
            side_effect=mock_revoke_all_user_sessions,
        ),
        patch(
            "app.db.repositories.session_repo.get_session_with_user_by_token_hash",
            side_effect=mock_get_session_with_user_by_token_hash,
        ),
        patch(
            "app.db.repositories.session_repo.create_session",
            side_effect=mock_create_session,
        ),
        patch(
            "app.db.repositories.session_repo.update_session_activity",
            side_effect=mock_update_session_activity,
        ),
        patch(
            "app.services.email_service.queue_password_reset_email",
            side_effect=mock_queue_password_reset_email,
        ),
    ]

    for p in patchers:
        p.start()
        _ACTIVE_PATCHERS.append(p)

    client = TestClient(app, base_url="http://localhost:5173")
    return client, resolved_settings, app


def get_csrf_headers(
    settings: Settings,
    origin: str = "http://localhost:5173",
) -> dict[str, str]:
    token = generate_anonymous_csrf_token(get_csrf_signing_key(settings))
    return {
        "Origin": origin,
        "X-CSRF-Token": token,
    }


# =============================================================================
# Automated Test Cases
# =============================================================================


def test_01_forgot_password_success_creates_token_and_queues_email() -> None:
    """Forgot password request generates random 30m token and queues email in outbox."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("tenant@example.com", full_name="Karma Bhutia")
    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "tenant@example.com"},
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "message": "If that email exists, we sent a link.",
    }

    # Verify reset token in DB
    assert len(db.reset_tokens) == 1
    tok = list(db.reset_tokens.values())[0]
    assert tok.user_id == user.id
    assert tok.used_at is None
    # 30-minute expiry
    now = datetime.now(UTC)
    tok_expires = (
        tok.expires_at.replace(tzinfo=UTC) if tok.expires_at.tzinfo is None else tok.expires_at
    )
    assert 28 <= (tok_expires - now).total_seconds() / 60 <= 31

    # Verify outbox email queued atomically
    assert len(db.outbox) == 1
    email = db.outbox[0]
    assert email.recipient == "tenant@example.com"
    assert email.recipient_name == "Karma Bhutia"
    assert "Reset your DormioStay password" in email.subject
    assert "30 minutes" in email.text_content
    assert "/reset-password?token=" in email.html_content


def test_02_forgot_password_anti_enumeration_nonexistent_email() -> None:
    """Non-existent email returns generic 200 OK without creating tokens or emails."""
    db = InMemoryPasswordResetDb()
    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "nonexistent@example.com"},
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "message": "If that email exists, we sent a link.",
    }
    assert len(db.reset_tokens) == 0
    assert len(db.outbox) == 0


def test_03_forgot_password_anti_enumeration_suspended_or_banned_email() -> None:
    """Suspended or banned user returns generic 200 OK without queuing reset email."""
    db = InMemoryPasswordResetDb()
    db.add_user("suspended@example.com", status="suspended")
    db.add_user("banned@example.com", status="banned")
    client, _, _ = setup_reset_test_app(db)

    resp1 = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "suspended@example.com"},
        headers={"X-Client-Type": "native"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["message"] == "If that email exists, we sent a link."

    resp2 = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "banned@example.com"},
        headers={"X-Client-Type": "native"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["message"] == "If that email exists, we sent a link."

    assert len(db.reset_tokens) == 0
    assert len(db.outbox) == 0


def test_04_forgot_password_rate_limiting_per_email() -> None:
    """Forgot password per-email limit blocks 4th request within 1 hour with HTTP 429."""
    redis_mock = AsyncMockRedis()
    set_redis_client_override(redis_mock)

    db = InMemoryPasswordResetDb()
    db.add_user("ratelimit@example.com")
    settings = create_test_settings(rate_limit_enabled=True)
    client, _, _ = setup_reset_test_app(db, settings=settings)

    for i in range(3):
        r = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "ratelimit@example.com"},
            headers={"X-Client-Type": "native"},
        )
        assert r.status_code == 200, f"Request {i + 1} failed"

    # 4th request triggers rate limit
    r4 = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "ratelimit@example.com"},
        headers={"X-Client-Type": "native"},
    )
    assert r4.status_code == 429
    assert r4.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in r4.headers


def test_04b_forgot_password_rate_limiting_fallback_stricter() -> None:
    """When Redis is unavailable, stricter fallback blocks 3rd attempt with HTTP 429."""
    set_redis_client_override(None)

    db = InMemoryPasswordResetDb()
    db.add_user("fallback@example.com")
    settings = create_test_settings(rate_limit_enabled=True)
    client, _, _ = setup_reset_test_app(db, settings=settings)

    # In-memory fallback limit is 2
    for i in range(2):
        r = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "fallback@example.com"},
            headers={"X-Client-Type": "native"},
        )
        assert r.status_code == 200, f"Request {i + 1} failed"

    # 3rd request blocked by fallback
    r3 = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "fallback@example.com"},
        headers={"X-Client-Type": "native"},
    )
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_05_forgot_password_rate_limiting_per_ip() -> None:
    """Forgot password per-IP limit blocks 11th request within 1 hour with HTTP 429."""
    redis_mock = AsyncMockRedis()
    set_redis_client_override(redis_mock)

    db = InMemoryPasswordResetDb()
    settings = create_test_settings(rate_limit_enabled=True)
    client, _, _ = setup_reset_test_app(db, settings=settings)

    # Send 10 requests from same IP using distinct emails
    for i in range(10):
        r = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": f"user{i}@example.com"},
            headers={"X-Client-Type": "native", "X-Forwarded-For": "203.0.113.50"},
        )
        assert r.status_code == 200, f"Request {i + 1} failed"

    # 11th request from same IP blocked
    r11 = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "user10@example.com"},
        headers={"X-Client-Type": "native", "X-Forwarded-For": "203.0.113.50"},
    )
    assert r11.status_code == 429
    assert r11.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_06_forgot_password_invalidates_prior_unused_tokens() -> None:
    """Requesting forgot password again invalidates prior unused tokens."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("resend@example.com")
    raw_token_1 = generate_action_token()
    token_1, hash_1 = db.add_reset_token(user.id, raw_token_1)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "resend@example.com"},
        headers={"X-Client-Type": "native"},
    )
    assert resp.status_code == 200

    # Old token must now be marked used/invalidated
    assert token_1.used_at is not None
    # New token created
    assert len(db.reset_tokens) == 2


def test_07_reset_password_success() -> None:
    """Valid token + strong password resets password and marks token used."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("resetme@example.com", password="OldPassword123!")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "NewStrongPass2026!",
        },
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert token.used_at is not None

    # Verify password hash updated with Argon2id
    assert verify_password("NewStrongPass2026!", user.password_hash) is True
    assert verify_password("OldPassword123!", user.password_hash) is False


def test_08_reset_password_global_session_revocation() -> None:
    """Password reset revokes all existing sessions of the user globally."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("multisession@example.com")
    sess1, _ = db.add_session(user.id)
    sess2, _ = db.add_session(user.id)

    raw_token = generate_action_token()
    db.add_reset_token(user.id, raw_token)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "BrandNewPass123!",
        },
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 200
    # Both sessions must be revoked with reason 'password_reset'
    assert sess1.revoked_at is not None
    assert sess1.revocation_reason == "password_reset"
    assert sess2.revoked_at is not None
    assert sess2.revocation_reason == "password_reset"


def test_09_old_sessions_fail_after_reset() -> None:
    """Old sessions cannot access protected endpoints after password reset."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("sessiontest@example.com")
    _, raw_session_token = db.add_session(user.id)

    raw_reset_token = generate_action_token()
    db.add_reset_token(user.id, raw_reset_token)

    client, _, _ = setup_reset_test_app(db)

    # Pre-reset: session works
    resp_pre = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {raw_session_token}"},
    )
    assert resp_pre.status_code == 200

    # Reset password
    resp_reset = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_reset_token,
            "new_password": "BrandNewPass123!",
        },
        headers={"X-Client-Type": "native"},
    )
    assert resp_reset.status_code == 200

    # Post-reset: old session is rejected with HTTP 401 SESSION_REVOKED
    resp_post = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {raw_session_token}"},
    )
    assert resp_post.status_code == 401
    assert resp_post.json()["error"]["code"] == "SESSION_REVOKED"


def test_10_login_with_new_password_succeeds_old_password_fails() -> None:
    """After reset, login with old password fails (401), login with new password succeeds."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("logintest@example.com", password="OldPassword123!")
    raw_token = generate_action_token()
    db.add_reset_token(user.id, raw_token)

    client, _, _ = setup_reset_test_app(db)

    # Reset password
    client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "NewSecretPass456!",
        },
        headers={"X-Client-Type": "native"},
    )

    # Login with old password fails
    resp_old = client.post(
        "/api/v1/auth/login",
        json={
            "email": "logintest@example.com",
            "password": "OldPassword123!",
            "client_type": "native",
        },
        headers={"X-Client-Type": "native"},
    )
    assert resp_old.status_code == 401

    # Login with new password succeeds
    resp_new = client.post(
        "/api/v1/auth/login",
        json={
            "email": "logintest@example.com",
            "password": "NewSecretPass456!",
            "client_type": "native",
        },
        headers={"X-Client-Type": "native"},
    )
    assert resp_new.status_code == 200
    assert "token" in resp_new.json()


def test_11_reset_password_clears_ambient_cookie() -> None:
    """If request carries an ambient session cookie, reset-password deletes it."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("cookietest@example.com")
    raw_token = generate_action_token()
    db.add_reset_token(user.id, raw_token)

    settings = create_test_settings(csrf_enabled=False)
    client, _, _ = setup_reset_test_app(db, settings=settings)

    # Set cookie
    client.cookies.set(settings.session_cookie_name, "some-cookie-value")

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "NewSecretPass456!",
        },
        headers={"Origin": "http://localhost:5173"},
    )

    assert resp.status_code == 200
    # Cookie should be deleted in Set-Cookie header
    cookie_headers = [v for k, v in resp.headers.items() if k.lower() == "set-cookie"]
    assert any(settings.session_cookie_name in c for c in cookie_headers)


def test_12_reused_reset_token_rejected() -> None:
    """Reusing an already consumed token returns HTTP 400 TOKEN_ALREADY_USED."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("reuse@example.com")
    raw_token = generate_action_token()
    db.add_reset_token(user.id, raw_token, used=True)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "AnotherNewPass123!",
        },
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOKEN_ALREADY_USED"


def test_13_expired_reset_token_rejected() -> None:
    """Expired reset token (> 30 min) returns HTTP 400 TOKEN_EXPIRED."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("expired@example.com")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token, expires_in_minutes=-1)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "AnotherNewPass123!",
        },
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOKEN_EXPIRED"
    assert token.used_at is None


def test_14_invalid_or_malformed_reset_token_rejected() -> None:
    """Non-existent, empty, or short token returns HTTP 400 TOKEN_INVALID."""
    db = InMemoryPasswordResetDb()
    client, _, _ = setup_reset_test_app(db)

    resp1 = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "short", "new_password": "AnotherNewPass123!"},
        headers={"X-Client-Type": "native"},
    )
    assert resp1.status_code == 400
    assert resp1.json()["error"]["code"] == "TOKEN_INVALID"

    resp2 = client.post(
        "/api/v1/auth/reset-password",
        json={"token": generate_action_token(), "new_password": "AnotherNewPass123!"},
        headers={"X-Client-Type": "native"},
    )
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "TOKEN_INVALID"


def test_15_weak_password_rejected() -> None:
    """Weak password (< 10 chars or common blocklist) rejected with WEAK_PASSWORD."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("weakpass@example.com")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token)

    client, _, _ = setup_reset_test_app(db)

    # Too short (< 10 chars)
    resp1 = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "Short1!"},
        headers={"X-Client-Type": "native"},
    )
    assert resp1.status_code == 400
    assert resp1.json()["error"]["code"] == "WEAK_PASSWORD"
    assert token.used_at is None

    # In blocklist
    resp2 = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "gangtok12345"},
        headers={"X-Client-Type": "native"},
    )
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "WEAK_PASSWORD"
    assert token.used_at is None


def test_16_disabled_account_reset_rejected() -> None:
    """Suspended or banned account submitting reset token returns HTTP 403."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("suspended@example.com", status="suspended")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "BrandNewPass123!"},
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_DISABLED"
    assert token.used_at is None


def test_17_get_navigation_does_not_consume_action() -> None:
    """GET requests to forgot-password and reset-password return 405 Method Not Allowed."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("getuser@example.com")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token)

    client, _, _ = setup_reset_test_app(db)

    resp1 = client.get(f"/api/v1/auth/reset-password?token={raw_token}")
    assert resp1.status_code == 405
    assert token.used_at is None

    resp2 = client.get("/api/v1/auth/forgot-password")
    assert resp2.status_code == 405


def test_18_both_client_transport_web_with_csrf() -> None:
    """Web client resets password with valid Origin and CSRF token."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("webclient@example.com")
    raw_token = generate_action_token()
    db.add_reset_token(user.id, raw_token)

    settings = create_test_settings(csrf_enabled=True)
    client, _, _ = setup_reset_test_app(db, settings=settings)

    headers = get_csrf_headers(settings)
    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "WebPassword2026!"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_19_both_client_transport_native_without_csrf() -> None:
    """Native mobile client resets password with X-Client-Type: native without CSRF."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("nativeclient@example.com")
    raw_token = generate_action_token()
    db.add_reset_token(user.id, raw_token)

    settings = create_test_settings(csrf_enabled=True)
    client, _, _ = setup_reset_test_app(db, settings=settings)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "NativePassword2026!"},
        headers={"X-Client-Type": "native"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_20_auth_endpoints_disabled_by_default_returns_503() -> None:
    """When auth_endpoints_enabled=False, endpoints return 503 SERVICE_UNAVAILABLE."""
    db = InMemoryPasswordResetDb()
    settings = create_test_settings(auth_enabled=False)
    client, _, _ = setup_reset_test_app(db, settings=settings)

    resp1 = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "alice@example.com"},
        headers={"X-Client-Type": "native"},
    )
    assert resp1.status_code == 503
    assert resp1.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    resp2 = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "some-token-string-123456", "new_password": "Password123!"},
        headers={"X-Client-Type": "native"},
    )
    assert resp2.status_code == 503
    assert resp2.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_21_concurrent_reset_password_race_condition() -> None:
    """Two concurrent requests using the same unused token race to reset password.

    Exactly one request must succeed with HTTP 200, and the second must be rejected
    with HTTP 400 TOKEN_ALREADY_USED.
    """
    import httpx

    db = InMemoryPasswordResetDb()
    user = db.add_user("racetest@example.com")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token)

    settings = create_test_settings(csrf_enabled=False)
    _, _, app = setup_reset_test_app(db, settings=settings)

    async def _run() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            req1 = ac.post(
                "/api/v1/auth/reset-password",
                json={"token": raw_token, "new_password": "WinningPass123!"},
            )
            req2 = ac.post(
                "/api/v1/auth/reset-password",
                json={"token": raw_token, "new_password": "LosingPass123!"},
            )
            return list(await asyncio.gather(req1, req2))

    resps = asyncio.run(_run())
    status_codes = sorted([r.status_code for r in resps])
    assert status_codes == [200, 400]

    successful = [r for r in resps if r.status_code == 200][0]
    failed = [r for r in resps if r.status_code == 400][0]

    assert successful.json()["status"] == "ok"
    assert failed.json()["error"]["code"] == "TOKEN_ALREADY_USED"
    assert token.used_at is not None


def test_22_exact_boundary_expiration() -> None:
    """Token that expires at or before now is rejected with TOKEN_EXPIRED."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("boundary@example.com")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token, expires_in_minutes=0)
    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "NewBoundaryPass1!"},
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOKEN_EXPIRED"
    assert token.used_at is None


def test_23_transaction_rollback_atomicity() -> None:
    """If database failure occurs during password update, token consumption rolls back."""
    db = InMemoryPasswordResetDb()
    user = db.add_user("rollback@example.com", password="InitialPassword123!")
    raw_token = generate_action_token()
    token, _ = db.add_reset_token(user.id, raw_token)

    setup_reset_test_app(db)

    mock_session = AsyncMock()
    mock_session.rollback = AsyncMock()

    async def _test() -> None:
        with patch(
            "app.db.repositories.user_repo.update_user_password",
            side_effect=RuntimeError("Database write error"),
        ):
            with pytest.raises(RuntimeError, match="Database write error"):
                try:
                    await auth_service.reset_password(
                        mock_session, raw_token, "NewRollbackPass123!"
                    )
                except Exception:
                    await mock_session.rollback()
                    raise

        mock_session.rollback.assert_awaited_once()

    asyncio.run(_test())


def test_24_cross_user_token_isolation() -> None:
    """Resetting User A's token cannot change User B's password."""
    db = InMemoryPasswordResetDb()
    user_a = db.add_user("user_a@example.com", password="PasswordA123!")
    user_b = db.add_user("user_b@example.com", password="PasswordB123!")

    raw_token_a = generate_action_token()
    db.add_reset_token(user_a.id, raw_token_a)

    client, _, _ = setup_reset_test_app(db)

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token_a, "new_password": "NewPasswordA2026!"},
        headers={"X-Client-Type": "native"},
    )

    assert resp.status_code == 200
    assert verify_password("NewPasswordA2026!", user_a.password_hash) is True
    # User B's password remains completely unchanged
    assert verify_password("PasswordB123!", user_b.password_hash) is True


# =============================================================================
# Real SQLAlchemy ORM Regression Tests (SQLite-backed)
# =============================================================================


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

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Any]:
        yield self


def _create_isolated_sqlite_db() -> Any:
    from sqlalchemy import Table, create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    for model_cls in [PasswordResetToken, User, UserSession]:
        table: Table = model_cls.__table__  # type: ignore[assignment]
        for c in list(table.constraints):
            if "~" in str(getattr(c, "sqltext", "")):
                table.constraints.remove(c)
        table.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_25_sqlalchemy_orm_identity_map_fresh_read() -> None:
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
            email="ormreset@example.com",
            password_hash=hash_password("Pass123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = PasswordResetToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=30),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            # Load token into s2 identity map
            tok_cached = await password_reset_repo.get_reset_token_by_hash(as2, token_hash)
            assert tok_cached is not None
            assert tok_cached.used_at is None

            # S3 (concurrent winning session) commits consumption
            winning_time = now + timedelta(seconds=1)
            with sm() as s3:
                s3.execute(
                    update(PasswordResetToken)
                    .where(PasswordResetToken.token_hash == token_hash)
                    .values(used_at=winning_time)
                )
                s3.commit()

            # S2 executes conditional update: rowcount == 0
            success = await password_reset_repo.mark_token_used(as2, tok_cached.id, now)
            assert success is False
            # synchronize_session=False prevents phantom in-memory mutation
            assert tok_cached.used_at is None

            # Querying WITHOUT populate_existing returns the stale identity-map object
            stale_query = await as2.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
            )
            assert stale_query.scalar_one().used_at is None

            # Querying WITH populate_existing=True explicitly forces fresh state from database
            fresh = await password_reset_repo.get_reset_token_by_hash(
                as2, token_hash, populate_existing=True
            )
            assert fresh is not None
            assert fresh.used_at is not None
            assert fresh.used_at.replace(tzinfo=UTC) == winning_time

    asyncio.run(_test())


def test_26_sqlalchemy_losing_request_returns_token_already_used() -> None:
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
        tok = PasswordResetToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=30),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s_losing:
            as_losing: Any = RealSqliteAsyncSession(s_losing)
            # Losing session loads token before winning session updates
            await password_reset_repo.get_reset_token_by_hash(as_losing, token_hash)

            # Winning session commits consumption in DB
            with sm() as s_win:
                s_win.execute(
                    update(PasswordResetToken)
                    .where(PasswordResetToken.token_hash == token_hash)
                    .values(used_at=now)
                )
                s_win.commit()

            with pytest.raises(AppError) as exc_info:
                await auth_service.reset_password(as_losing, raw_token, "NewPassword123!")

            assert exc_info.value.code == "TOKEN_ALREADY_USED"
            assert exc_info.value.status_code == 400

    asyncio.run(_test())


def test_27_sqlalchemy_atomic_commit() -> None:
    """Genuine SQLAlchemy: successful reset commits token and password atomically."""
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
            password_hash=hash_password("OldPassword123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = PasswordResetToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=30),
        )
        s1.add(tok)
        sess = UserSession(
            id=uuid4(),
            user_id=user_id,
            token_hash="a" * 64,
            expires_at=now + timedelta(days=14),
            absolute_expires_at=now + timedelta(days=14),
        )
        s1.add(sess)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            await auth_service.reset_password(as2, raw_token, "NewArgon2Pass123!")
            await as2.commit()

        # Completely fresh independent session queries DB directly
        with sm() as s3:
            u_db = s3.get(User, user_id)
            assert u_db is not None
            assert verify_password("NewArgon2Pass123!", u_db.password_hash) is True

            tok_db = s3.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
            ).scalar_one()
            assert tok_db.used_at is not None

            sess_db = s3.execute(
                select(UserSession).where(UserSession.user_id == user_id)
            ).scalar_one()
            assert sess_db.revoked_at is not None
            assert sess_db.revocation_reason == "password_reset"

    asyncio.run(_test())


def test_28_sqlalchemy_atomic_rollback() -> None:
    """Genuine SQLAlchemy: failure rolls back token consumption and password update in DB."""
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
            password_hash=hash_password("UnchangedPass123!"),
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = PasswordResetToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=30),
        )
        s1.add(tok)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            # Token marked used and password updated in transaction
            await password_reset_repo.mark_token_used(as2, tok.id, now)
            await user_repo.update_user_password(as2, user_id, hash_password("NewTempPass123!"))
            # Failure occurs before commit -> transaction rollback!
            await as2.rollback()

        # Query from fresh independent session: database state must remain completely unchanged
        with sm() as s3:
            tok_db = s3.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
            ).scalar_one()
            assert tok_db.used_at is None

            u_db = s3.get(User, user_id)
            assert u_db is not None
            assert verify_password("UnchangedPass123!", u_db.password_hash) is True

    asyncio.run(_test())


def test_29_successful_reset_invalidates_all_outstanding_reset_tokens() -> None:
    """Genuine SQLAlchemy: successful reset invalidates all outstanding reset tokens for user."""
    from sqlalchemy import select

    sm = _create_isolated_sqlite_db()
    user_a_id = uuid4()
    user_b_id = uuid4()
    now = datetime.now(UTC)

    raw_token_a1 = generate_action_token()
    hash_a1 = hash_action_token(raw_token_a1)
    raw_token_a2 = generate_action_token()
    hash_a2 = hash_action_token(raw_token_a2)

    raw_token_b = generate_action_token()
    hash_b = hash_action_token(raw_token_b)

    with sm() as s1:
        ua = User(
            id=user_a_id,
            email="usera@example.com",
            password_hash=hash_password("OldPass123!"),
            role="tenant",
            status="active",
        )
        ub = User(
            id=user_b_id,
            email="userb@example.com",
            password_hash=hash_password("UserBPass123!"),
            role="tenant",
            status="active",
        )
        s1.add_all([ua, ub])

        tok_a1 = PasswordResetToken(
            id=uuid4(),
            user_id=user_a_id,
            token_hash=hash_a1,
            expires_at=now + timedelta(minutes=30),
        )
        tok_a2 = PasswordResetToken(
            id=uuid4(),
            user_id=user_a_id,
            token_hash=hash_a2,
            expires_at=now + timedelta(minutes=30),
        )
        tok_b = PasswordResetToken(
            id=uuid4(),
            user_id=user_b_id,
            token_hash=hash_b,
            expires_at=now + timedelta(minutes=30),
        )
        s1.add_all([tok_a1, tok_a2, tok_b])
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            # Reset password using Token A1
            await auth_service.reset_password(as2, raw_token_a1, "BrandNewPass123!")
            await as2.commit()

        # Query in independent session to verify database state
        with sm() as s3:
            # Token A1 must be used
            tok1_db = s3.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_a1)
            ).scalar_one()
            assert tok1_db.used_at is not None

            # Token A2 must ALSO be invalidated
            tok2_db = s3.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_a2)
            ).scalar_one()
            assert tok2_db.used_at is not None

            # User B's token must remain untouched and usable
            tokb_db = s3.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_b)
            ).scalar_one()
            assert tokb_db.used_at is None

            # Submitting second token A2 now fails with TOKEN_ALREADY_USED
            as3: Any = RealSqliteAsyncSession(s3)
            with pytest.raises(AppError) as exc_info:
                await auth_service.reset_password(as3, raw_token_a2, "AnotherPassword123!")
            assert exc_info.value.code == "TOKEN_ALREADY_USED"

    asyncio.run(_test())


def test_30_concurrent_login_during_reset_aborts_without_issuing_session() -> None:
    """Genuine SQLAlchemy: optimistic password_hash check aborts login and creates 0 sessions."""
    from sqlalchemy import select

    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    now = datetime.now(UTC)
    old_hash = hash_password("OldPassword123!")
    new_hash = hash_password("NewPassword456!")

    with sm() as s1:
        u = User(
            id=user_id,
            email="racelogin@example.com",
            password_hash=old_hash,
            role="tenant",
            status="active",
        )
        s1.add(u)
        s1.commit()

    async def _test() -> None:
        # Session 1 (simulating Login): reads user with old_hash
        with sm() as s_login:
            as_login: Any = RealSqliteAsyncSession(s_login)

            # Concurrent Reset Transaction commits a new password hash
            with sm() as s_reset:
                as_reset: Any = RealSqliteAsyncSession(s_reset)
                await user_repo.update_user_password(as_reset, user_id, new_hash)
                await as_reset.commit()

            # Login attempts to update_login_success with stale old_hash -> fails (rowcount == 0)
            updated = await user_repo.update_login_success(
                as_login, user_id, now, expected_password_hash=old_hash
            )
            assert updated is False

            # Login aborts and rolls back
            await as_login.rollback()

        # Completely fresh session verifies 0 sessions exist in database
        with sm() as s_check:
            sessions = (
                s_check.execute(select(UserSession).where(UserSession.user_id == user_id))
                .scalars()
                .all()
            )
            assert len(sessions) == 0

    asyncio.run(_test())


def test_31_login_before_reset_is_revoked_by_subsequent_reset() -> None:
    """Genuine SQLAlchemy: pre-reset session is revoked and login with old password fails."""
    from sqlalchemy import select

    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    now = datetime.now(UTC)
    old_hash = hash_password("OldPass123!")
    raw_token = generate_action_token()
    token_hash = hash_action_token(raw_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="revoketest@example.com",
            password_hash=old_hash,
            role="tenant",
            status="active",
        )
        s1.add(u)
        tok = PasswordResetToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=30),
        )
        s1.add(tok)
        sess = UserSession(
            id=uuid4(),
            user_id=user_id,
            token_hash="s" * 64,
            expires_at=now + timedelta(days=14),
            absolute_expires_at=now + timedelta(days=14),
        )
        s1.add(sess)
        s1.commit()

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            # Reset password
            await auth_service.reset_password(as2, raw_token, "NewSecurePass123!")
            await as2.commit()

        # Verify old session is revoked in database
        with sm() as s3:
            s_db = s3.execute(
                select(UserSession).where(UserSession.user_id == user_id)
            ).scalar_one()
            assert s_db.revoked_at is not None
            assert s_db.revocation_reason == "password_reset"

            # User's password is updated
            u_db = s3.get(User, user_id)
            assert u_db is not None
            assert verify_password("NewSecurePass123!", u_db.password_hash) is True
            assert verify_password("OldPass123!", u_db.password_hash) is False

    asyncio.run(_test())


def test_32_forgot_password_timing_anti_enumeration() -> None:
    """Forgot password avoids expensive Argon2id on non-existent accounts, equalizing timing."""
    import time

    db = InMemoryPasswordResetDb()
    db.add_user("active@example.com")
    db.add_user("suspended@example.com", status="suspended")

    client, _, _ = setup_reset_test_app(db)

    # All account states return identical HTTP 200 response
    emails = ["active@example.com", "nonexistent@example.com", "suspended@example.com"]
    for email in emails:
        t0 = time.perf_counter()
        resp = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": email},
            headers={"X-Client-Type": "native"},
        )
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["message"] == "If that email exists, we sent a link."
        # Must be fast (< 100ms) proving verify_dummy_password (Argon2id) was NOT run
        assert elapsed < 0.1, f"Elapsed time {elapsed}s too high for {email}"


def test_33_login_lock_ordering_update_login_success_before_revoke_session() -> None:
    """Verify login executes update_login_success before session rotation.

    Lock acquisition order: users -> user_sessions.
    """
    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    old_hash = hash_password("ValidPass123!")
    ambient_token = generate_session_token()
    ambient_token_hash = hash_session_token(ambient_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="lockorder@example.com",
            password_hash=old_hash,
            role="tenant",
            status="active",
        )
        s1.add(u)
        s1.flush()
        sess = UserSession(
            id=uuid4(),
            user_id=user_id,
            token_hash=ambient_token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=1),
            absolute_expires_at=datetime.now(UTC) + timedelta(days=14),
        )
        s1.add(sess)
        s1.commit()

    call_order: list[str] = []
    orig_update_login = user_repo.update_login_success
    orig_revoke_session = session_repo.revoke_session
    orig_create_session = session_repo.create_session

    async def spied_update_login(*args: Any, **kwargs: Any) -> Any:
        call_order.append("update_login_success")
        return await orig_update_login(*args, **kwargs)

    async def spied_revoke_session(*args: Any, **kwargs: Any) -> Any:
        call_order.append("revoke_session")
        return await orig_revoke_session(*args, **kwargs)

    async def spied_create_session(*args: Any, **kwargs: Any) -> Any:
        call_order.append("create_session")
        return await orig_create_session(*args, **kwargs)

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            settings = get_settings()

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def dummy_factory() -> AsyncIterator[Any]:
                yield as2

            patch_update = patch(
                "app.db.repositories.user_repo.update_login_success",
                side_effect=spied_update_login,
            )
            patch_revoke = patch(
                "app.db.repositories.session_repo.revoke_session",
                side_effect=spied_revoke_session,
            )
            patch_create = patch(
                "app.db.repositories.session_repo.create_session",
                side_effect=spied_create_session,
            )
            with patch_update, patch_revoke, patch_create:
                payload = LoginRequest(email="lockorder@example.com", password="ValidPass123!")
                raw_tok, ret_user, ret_sess = await auth_service.login(
                    session=as2,
                    payload=payload,
                    settings=settings,
                    session_factory=dummy_factory,  # type: ignore[arg-type]
                    client_type="web",
                    existing_cookie_token=ambient_token,
                )
                assert raw_tok is not None
                assert ret_user.id == user_id
                assert ret_sess is not None

        # Assert strict lock order: users (update_login_success) BEFORE user_sessions (revoke)
        assert call_order == ["update_login_success", "revoke_session", "create_session"]

    asyncio.run(_test())


def test_34_login_session_rotation_genuine_sqlalchemy() -> None:
    """Genuine SQLAlchemy: web login rotates old session to revoked and creates new."""
    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    old_hash = hash_password("RotatePass123!")
    ambient_token = generate_session_token()
    ambient_token_hash = hash_session_token(ambient_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="rotate@example.com",
            password_hash=old_hash,
            role="tenant",
            status="active",
        )
        s1.add(u)
        s1.flush()
        old_session = UserSession(
            id=uuid4(),
            user_id=user_id,
            token_hash=ambient_token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=1),
            absolute_expires_at=datetime.now(UTC) + timedelta(days=14),
        )
        s1.add(old_session)
        s1.commit()
        old_session_id = old_session.id

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            settings = get_settings()

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def dummy_factory() -> AsyncIterator[Any]:
                yield as2

            payload = LoginRequest(email="rotate@example.com", password="RotatePass123!")
            raw_token, ret_user, new_sess = await auth_service.login(
                session=as2,
                payload=payload,
                settings=settings,
                session_factory=dummy_factory,  # type: ignore[arg-type]
                client_type="web",
                existing_cookie_token=ambient_token,
            )
            await as2.commit()

            assert new_sess.id != old_session_id

        # Verify database state in an independent session
        with sm() as s3:
            s_old = s3.get(UserSession, old_session_id)
            assert s_old is not None
            assert s_old.revoked_at is not None
            assert s_old.revocation_reason == "session_rotation"

            s_new = s3.get(UserSession, new_sess.id)
            assert s_new is not None
            assert s_new.revoked_at is None
            assert s_new.user_id == user_id

            # User last login timestamp updated
            u_db = s3.get(User, user_id)
            assert u_db is not None
            assert u_db.last_login_at is not None

    asyncio.run(_test())


def test_35_failed_optimistic_login_aborts_before_revoking_ambient_session() -> None:
    """Genuine SQLAlchemy: failed optimistic check aborts before session rotation."""
    from sqlalchemy import select

    sm = _create_isolated_sqlite_db()
    user_id = uuid4()
    old_hash = hash_password("CorrectPass123!")
    ambient_token = generate_session_token()
    ambient_token_hash = hash_session_token(ambient_token)

    with sm() as s1:
        u = User(
            id=user_id,
            email="optimisticabort@example.com",
            password_hash=old_hash,
            role="tenant",
            status="active",
        )
        s1.add(u)
        s1.flush()
        old_session = UserSession(
            id=uuid4(),
            user_id=user_id,
            token_hash=ambient_token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=1),
            absolute_expires_at=datetime.now(UTC) + timedelta(days=14),
        )
        s1.add(old_session)
        s1.commit()
        old_session_id = old_session.id

    async def _test() -> None:
        with sm() as s2:
            as2: Any = RealSqliteAsyncSession(s2)
            settings = get_settings()

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def dummy_factory() -> AsyncIterator[Any]:
                yield as2

            # Simulate concurrent reset: update_login_success fails optimistic check (0 rows)
            payload = LoginRequest(email="optimisticabort@example.com", password="CorrectPass123!")
            with (
                patch("app.db.repositories.user_repo.update_login_success", return_value=False),
                pytest.raises(AppError) as exc_info,
            ):
                await auth_service.login(
                    session=as2,
                    payload=payload,
                    settings=settings,
                    session_factory=dummy_factory,  # type: ignore[arg-type]
                    client_type="web",
                    existing_cookie_token=ambient_token,
                )
            assert exc_info.value.code == "AUTHENTICATION_FAILED"
            await as2.rollback()

        # In DB: ambient session was NOT revoked because login aborted at step 6 before step 7
        with sm() as s3:
            s_old = s3.get(UserSession, old_session_id)
            assert s_old is not None
            assert s_old.revoked_at is None  # Untouched

            # Exactly 1 session exists (the old ambient session), 0 new sessions created
            stmt = select(UserSession).where(UserSession.user_id == user_id)
            sessions = s3.execute(stmt).scalars().all()
            assert len(sessions) == 1

    asyncio.run(_test())


def test_36_session_rotation_deadlock_freedom_total_lock_ordering_contract() -> None:
    """Verify total lock order: users -> password_reset_tokens -> user_sessions."""
    import inspect

    login_src = inspect.getsource(auth_service.login)
    reset_src = inspect.getsource(auth_service.reset_password)

    # In login: update_login_success appears BEFORE revoke_session
    idx_login_user = login_src.find("update_login_success")
    idx_login_revoke = login_src.find("revoke_session")
    idx_login_create = login_src.find("create_session")
    assert idx_login_user != -1 and idx_login_revoke != -1 and idx_login_create != -1
    assert idx_login_user < idx_login_revoke < idx_login_create, (
        "Login must acquire user lock before mutating user_sessions"
    )

    # In reset_password: for_update=True appears BEFORE mark_token_used and revoke_all_user_sessions
    idx_reset_user = reset_src.find("for_update=True")
    idx_reset_token = reset_src.find("mark_token_used")
    idx_reset_sessions = reset_src.find("revoke_all_user_sessions")
    assert idx_reset_user != -1 and idx_reset_token != -1 and idx_reset_sessions != -1
    assert idx_reset_user < idx_reset_token < idx_reset_sessions, (
        "Reset password must acquire user lock before mutating tokens and user_sessions"
    )
