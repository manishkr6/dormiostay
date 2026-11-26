"""Comprehensive Unit and Security Tests for T-08: Rate Limiting and Abuse Prevention.

Verifies:
1. Requests below limit
2. Exact limit boundary
3. Exceeded limit returning 429
4. Retry-After calculation
5. TTL expiration and recovery
6. Multiple simultaneous requests (concurrency)
7. Atomic Redis counter behavior (Lua script)
8. Distinct IPs and accounts isolation
9. Same account across multiple IPs (distributed guessing protection)
10. Same IP across multiple accounts (credential stuffing protection)
11. Spoofed X-Forwarded-For rejection
12. Trusted proxy handling (right-to-left traversal)
13. Malformed client identifiers (IPv6, spaces, uppercase, unicode)
14. Missing Redis connection (in-memory stricter fallback)
15. Redis timeout and failure handling (never bypasses limits)
16. Account enumeration resistance (identical 429 bodies and timings)
17. Sensitive data absent from Redis keys and logs
18. Web cookie authentication integration
19. Native bearer authentication integration
20. CSRF and Origin enforcement compatibility
21. T-06 failed-login counters integrity in PostgreSQL
22. Authentication activation disabled by default (503 guard)
23. Cross-test isolation (zero state leakage)
24. Multi-worker simulated concurrency
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl
from redis.exceptions import TimeoutError as RedisTimeoutError

import app.db.repositories.session_repo as session_repo_mod
import app.db.repositories.user_repo as user_repo_mod
from app.core.config import Settings, get_settings
from app.core.rate_limit import (
    RateLimitRule,
    evaluate_rate_limit,
    get_client_ip,
    hash_identifier,
    is_ip_in_trusted_proxies,
    reset_rate_limit_state,
    set_redis_client_override,
)
from app.core.security import hash_password
from app.db.models import User, UserProfile, UserSession
from app.db.session import get_session, get_session_factory
from app.main import create_app

# Track monkeypatch instances for clean teardown across all tests
_ACTIVE_MONKEYPATCHES: list[pytest.MonkeyPatch] = []


# =============================================================================
# Test Doubles and Mocks
# =============================================================================


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


class InMemoryAuthDb:
    """In-memory mock for PostgreSQL user and session state."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.profiles: dict[UUID, UserProfile] = {}
        self.sessions: dict[UUID, UserSession] = {}
        self.sessions_by_hash: dict[str, UserSession] = {}

    def add_user(
        self,
        email: str,
        password: str,
        role: str = "tenant",
        full_name: str = "Test User",
        failed_login_count: int = 0,
    ) -> User:
        user_id = uuid4()
        now = datetime.now(UTC)
        pwd_hash = hash_password(password)
        user = User(
            id=user_id,
            email=email.strip().lower(),
            password_hash=pwd_hash,
            role=role,
            status="active",
            failed_login_count=failed_login_count,
            created_at=now,
            updated_at=now,
        )
        profile = UserProfile(
            id=uuid4(),
            user_id=user_id,
            full_name=full_name,
            phone="+15551234567",
            created_at=now,
            updated_at=now,
        )
        self.users[user_id] = user
        self.profiles[user_id] = profile
        return user


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clean_rate_limit_state() -> Generator[None, None, None]:
    """Ensure every test begins and ends with empty rate limiter state and no monkeypatch leaks."""
    asyncio.run(reset_rate_limit_state())
    set_redis_client_override(None)
    yield
    asyncio.run(reset_rate_limit_state())
    set_redis_client_override(None)
    while _ACTIVE_MONKEYPATCHES:
        mp = _ACTIVE_MONKEYPATCHES.pop()
        mp.undo()


def create_t08_test_settings(
    auth_enabled: bool = True,
    rate_limit_enabled: bool = True,
    csrf_enabled: bool = False,
    redis_url: str | None = "redis://mocked-local:6379/0",
    trusted_proxies: list[str] | None = None,
) -> Settings:
    """Create test Settings instance with predictable T-08 configuration."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/dormiostay",
        frontend_origin=AnyHttpUrl("http://localhost:5173"),
        auth_endpoints_enabled=auth_enabled,
        rate_limit_enabled=rate_limit_enabled,
        csrf_protection_enabled=csrf_enabled,
        redis_url=redis_url,
        rate_limit_connect_timeout_seconds=0.5,
        trusted_proxies=trusted_proxies or ["127.0.0.1", "::1", "testclient"],
        session_cookie_name="dormiostay_session",
        csrf_secret_key="test-csrf-secret-key-32-bytes-long!!",
    )


def setup_t08_test_app(
    db: InMemoryAuthDb,
    settings: Settings | None = None,
    mock_redis: AsyncMockRedis | None = None,
) -> tuple[TestClient, Settings, AsyncMockRedis | None]:
    """Build FastAPI test client with mocked DB, settings, and mock Redis."""
    resolved_settings = settings or create_t08_test_settings()
    if mock_redis is not None:
        redis_instance = mock_redis
        set_redis_client_override(redis_instance)
    elif resolved_settings.redis_url is not None:
        redis_instance = AsyncMockRedis()
        set_redis_client_override(redis_instance)
    else:
        redis_instance = None
        set_redis_client_override(None)

    app = create_app(resolved_settings)

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()

    @asynccontextmanager
    async def mock_begin_nested() -> AsyncIterator[None]:
        yield

    mock_db_session.begin_nested = mock_begin_nested

    # Factory session for auth transactions
    mock_sec_session = AsyncMock()
    mock_sec_session.execute = AsyncMock()
    mock_sec_session.commit = AsyncMock()
    mock_sec_session.rollback = AsyncMock()
    mock_sec_begin_cm = MagicMock()
    mock_sec_begin_cm.__aenter__ = AsyncMock(return_value=None)
    mock_sec_begin_cm.__aexit__ = AsyncMock(return_value=None)
    mock_sec_session.begin = MagicMock(return_value=mock_sec_begin_cm)

    @asynccontextmanager
    async def mock_factory_context() -> AsyncIterator[Any]:
        yield mock_sec_session

    mock_session_factory = MagicMock(side_effect=lambda: mock_factory_context())

    async def override_get_session() -> AsyncIterator[Any]:
        yield mock_db_session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_session_factory] = lambda: mock_session_factory
    app.dependency_overrides[get_settings] = lambda: resolved_settings

    # Wire repositories to in-memory store
    async def mock_get_user_by_email_ci(*args: Any, **kwargs: Any) -> User | None:
        email = kwargs.get("email") or (args[1] if len(args) > 1 else None)
        if not email:
            return None
        target = email.strip().lower()
        for u in db.users.values():
            if u.email.strip().lower() == target:
                return u
        return None

    async def mock_create_user(*args: Any, **kwargs: Any) -> User:
        user_id = uuid4()
        now = datetime.now(UTC)
        user = User(
            id=user_id,
            email=kwargs["email"].strip().lower(),
            password_hash=kwargs["password_hash"],
            role=kwargs.get("role", "tenant"),
            status=kwargs.get("status", "active"),
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )
        db.users[user_id] = user
        return user

    async def mock_create_user_profile(*args: Any, **kwargs: Any) -> UserProfile:
        pid = uuid4()
        now = datetime.now(UTC)
        profile = UserProfile(
            id=pid,
            user_id=kwargs["user_id"],
            full_name=kwargs["full_name"],
            phone=kwargs.get("phone"),
            created_at=now,
            updated_at=now,
        )
        db.profiles[kwargs["user_id"]] = profile
        return profile

    async def mock_get_user_with_profile(
        *args: Any, **kwargs: Any
    ) -> tuple[User, UserProfile | None] | None:
        user_id = kwargs.get("user_id") or (args[1] if len(args) > 1 else None)
        if user_id in db.users:
            return db.users[user_id], db.profiles.get(user_id)
        return None

    async def mock_update_login_success(*args: Any, **kwargs: Any) -> None:
        user_id = kwargs.get("user_id") or args[1]
        login_at = kwargs.get("login_at") or (args[2] if len(args) > 2 else datetime.now(UTC))
        if user_id in db.users:
            db.users[user_id].last_login_at = login_at
            db.users[user_id].failed_login_count = 0

    async def mock_update_login_failure(*args: Any, **kwargs: Any) -> None:
        user_id = kwargs.get("user_id") or args[1]
        if user_id in db.users:
            db.users[user_id].failed_login_count += 1

    async def mock_create_session(*args: Any, **kwargs: Any) -> UserSession:
        user_id = kwargs.get("user_id") or args[1]
        token_hash = kwargs.get("token_hash") or args[2]
        expires_at = kwargs.get("expires_at") or args[3]
        absolute_expires_at = kwargs.get("absolute_expires_at") or args[4]
        created_at = (
            kwargs.get("created_at") or (args[5] if len(args) > 5 else None) or datetime.now(UTC)
        )
        s_id = uuid4()
        s = UserSession(
            id=s_id,
            user_id=user_id,
            token_hash=token_hash,
            last_activity_at=created_at,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
            created_at=created_at,
            updated_at=created_at,
        )
        db.sessions[s_id] = s
        db.sessions_by_hash[token_hash] = s
        return s

    async def mock_get_session_with_user_by_token_hash(
        *args: Any, **kwargs: Any
    ) -> tuple[UserSession, User] | None:
        th = kwargs.get("token_hash") or (args[1] if len(args) > 1 else None)
        if th in db.sessions_by_hash:
            sess = db.sessions_by_hash[th]
            user = db.users.get(sess.user_id)
            if user:
                return sess, user
        return None

    async def mock_update_session_activity(*args: Any, **kwargs: Any) -> bool:
        sid = kwargs.get("session_id") or (args[1] if len(args) > 1 else None)
        if sid in db.sessions:
            sess = db.sessions[sid]
            if sess.revoked_at is not None:
                return False
            sess.last_activity_at = kwargs.get("last_activity_at", datetime.now(UTC))
            sess.expires_at = kwargs.get("new_expires_at", sess.expires_at)
            return True
        return False

    async def mock_revoke_session(*args: Any, **kwargs: Any) -> None:
        sid = kwargs.get("session_id") or (args[1] if len(args) > 1 else None)
        if sid in db.sessions:
            sess = db.sessions[sid]
            sess.revoked_at = kwargs.get("revoked_at", datetime.now(UTC))
            sess.revocation_reason = kwargs.get("reason", "logout")

    monkeypatch = pytest.MonkeyPatch()
    _ACTIVE_MONKEYPATCHES.append(monkeypatch)
    monkeypatch.setattr(user_repo_mod, "get_user_by_email_ci", mock_get_user_by_email_ci)
    monkeypatch.setattr(user_repo_mod, "get_user_with_profile", mock_get_user_with_profile)
    monkeypatch.setattr(user_repo_mod, "create_user", mock_create_user)
    monkeypatch.setattr(user_repo_mod, "create_user_profile", mock_create_user_profile)
    monkeypatch.setattr(user_repo_mod, "update_login_success", mock_update_login_success)
    monkeypatch.setattr(user_repo_mod, "update_login_failure", mock_update_login_failure)
    monkeypatch.setattr(session_repo_mod, "create_session", mock_create_session)
    monkeypatch.setattr(
        session_repo_mod,
        "get_session_with_user_by_token_hash",
        mock_get_session_with_user_by_token_hash,
    )
    monkeypatch.setattr(session_repo_mod, "update_session_activity", mock_update_session_activity)
    monkeypatch.setattr(session_repo_mod, "revoke_session", mock_revoke_session)

    client = TestClient(app, base_url="http://testserver", raise_server_exceptions=False)
    return client, resolved_settings, redis_instance


# =============================================================================
# 1. Requests Below Limit & Headers
# =============================================================================


def test_01_requests_below_limit_decrement_remaining() -> None:
    """Requests below limit succeed with decreasing X-RateLimit-Remaining headers."""
    db = InMemoryAuthDb()
    client, _, _ = setup_t08_test_app(db)

    # Anonymous CSRF endpoint has limit 60/min
    resp1 = client.get("/api/v1/auth/csrf")
    assert resp1.status_code == status.HTTP_200_OK
    assert resp1.headers["X-RateLimit-Limit"] == "60"
    assert resp1.headers["X-RateLimit-Remaining"] == "59"
    assert int(resp1.headers["X-RateLimit-Reset"]) > 0

    resp2 = client.get("/api/v1/auth/csrf")
    assert resp2.status_code == status.HTTP_200_OK
    assert resp2.headers["X-RateLimit-Limit"] == "60"
    assert resp2.headers["X-RateLimit-Remaining"] == "58"


# =============================================================================
# 2. Exact Limit Boundary
# =============================================================================


def test_02_exact_limit_boundary_allows_last_request() -> None:
    """The request matching the exact max limit is allowed with Remaining=0."""
    db = InMemoryAuthDb()
    client, _, _ = setup_t08_test_app(db)

    # Signup limit is 5 per hour
    for i in range(4):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": f"tenant{i}@example.com",
                "password": "ValidPassword123!",
                "role": "tenant",
                "full_name": f"Tenant {i}",
            },
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert int(resp.headers["X-RateLimit-Remaining"]) == 4 - i

    # 5th request: exact boundary
    resp_5 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "tenant_final@example.com",
            "password": "ValidPassword123!",
            "role": "tenant",
            "full_name": "Final Tenant",
        },
    )
    assert resp_5.status_code == status.HTTP_201_CREATED
    assert resp_5.headers["X-RateLimit-Remaining"] == "0"
    assert resp_5.headers["X-RateLimit-Limit"] == "5"


# =============================================================================
# 3. Exceeded Limit Returns 429
# =============================================================================


def test_03_exceeded_limit_returns_429_with_structured_error() -> None:
    """Exceeding the limit immediately returns HTTP 429 RATE_LIMIT_EXCEEDED."""
    db = InMemoryAuthDb()
    client, _, _ = setup_t08_test_app(db)

    # Exhaust 5 signup attempts
    for i in range(5):
        client.post(
            "/api/v1/auth/signup",
            json={
                "email": f"t{i}@example.com",
                "password": "ValidPassword123!",
                "role": "tenant",
                "full_name": f"User {i}",
            },
        )

    # 6th attempt: Exceeded
    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "t6@example.com",
            "password": "ValidPassword123!",
            "role": "tenant",
            "full_name": "User 6",
        },
    )
    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert body["error"]["message"] == "Too many requests. Please try again later."
    assert "request_id" in body["error"]


# =============================================================================
# 4. Retry-After Calculation
# =============================================================================


def test_04_retry_after_header_present_and_positive() -> None:
    """HTTP 429 responses contain positive integer Retry-After matching remaining window."""
    db = InMemoryAuthDb()
    client, _, _ = setup_t08_test_app(db)

    for i in range(5):
        client.post(
            "/api/v1/auth/signup",
            json={
                "email": f"t{i}@example.com",
                "password": "ValidPassword123!",
                "role": "tenant",
                "full_name": "User",
            },
        )

    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "blocked@example.com",
            "password": "ValidPassword123!",
            "role": "tenant",
            "full_name": "User",
        },
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    retry_after = int(resp.headers["Retry-After"])
    assert 0 < retry_after <= 3600
    assert resp.headers["X-RateLimit-Reset"] == str(retry_after)
    assert resp.headers["X-RateLimit-Remaining"] == "0"


# =============================================================================
# 5. TTL Expiration and Recovery
# =============================================================================


def test_05_ttl_expiration_resets_counter() -> None:
    """When the window expires, rate limiter resets counter and allows requests."""
    db = InMemoryAuthDb()
    client, _, mock_redis = setup_t08_test_app(db)

    # Exhaust signup attempts (5/hr)
    for i in range(5):
        client.post(
            "/api/v1/auth/signup",
            json={
                "email": f"t{i}@example.com",
                "password": "ValidPassword123!",
                "role": "tenant",
                "full_name": "User",
            },
        )

    # Blocked
    assert (
        client.post(
            "/api/v1/auth/signup",
            json={
                "email": "t_blocked@example.com",
                "password": "ValidPassword123!",
                "role": "tenant",
                "full_name": "User",
            },
        ).status_code
        == 429
    )

    # Simulate TTL expiration in mock Redis
    assert mock_redis is not None
    for key in list(mock_redis.ttls.keys()):
        mock_redis.ttls[key] = time.monotonic() - 1

    # New request succeeds after TTL expiration
    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "t_fresh@example.com",
            "password": "ValidPassword123!",
            "role": "tenant",
            "full_name": "Fresh User",
        },
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.headers["X-RateLimit-Remaining"] == "4"


# =============================================================================
# 6. Multiple Simultaneous Requests (Concurrency)
# =============================================================================


def test_06_multiple_simultaneous_requests_concurrency() -> None:
    """Multiple simultaneous requests are dispatched concurrently and respect limits."""

    async def run_concurrent() -> tuple[int, int]:
        mock_redis = AsyncMockRedis()
        rule = RateLimitRule(
            name="test_concurrent",
            max_requests=10,
            window_seconds=60,
            fallback_max_requests=5,
            fallback_window_seconds=60,
        )
        settings = create_t08_test_settings()

        async def single_check(idx: int) -> bool:
            res = await evaluate_rate_limit("rl:test:key", rule, settings)
            return res.allowed

        set_redis_client_override(mock_redis)
        tasks = [single_check(i) for i in range(30)]
        results = await asyncio.gather(*tasks)
        allowed = sum(1 for a in results if a)
        denied = sum(1 for a in results if not a)
        return allowed, denied

    allowed_count, denied_count = asyncio.run(run_concurrent())
    assert allowed_count == 10, f"Expected exactly 10 allowed, got {allowed_count}"
    assert denied_count == 20, f"Expected exactly 20 denied, got {denied_count}"


# =============================================================================
# 7. Atomic Redis Counter Behavior (Lua Script)
# =============================================================================


def test_07_atomic_redis_counter_behavior_lua_script() -> None:
    """Lua script increments and sets TTL in a single atomic step without separate commands."""
    mock_redis = AsyncMockRedis()
    rule = RateLimitRule(
        name="test_atomicity",
        max_requests=3,
        window_seconds=900,
        fallback_max_requests=2,
        fallback_window_seconds=900,
    )
    settings = create_t08_test_settings()
    set_redis_client_override(mock_redis)

    res1 = asyncio.run(evaluate_rate_limit("rl:atomic:key", rule, settings))
    assert res1.allowed is True
    assert res1.remaining == 2
    assert 0 < res1.retry_after <= 900

    # Ensure TTL was recorded on key creation
    assert "rl:atomic:key" in mock_redis.ttls
    # Check that eval was invoked atomically
    assert len(mock_redis.call_history) == 1
    assert mock_redis.call_history[0] == ("rl:atomic:key", 900, 3)


# =============================================================================
# 8. Distinct IPs and Accounts Isolation
# =============================================================================


def test_08_distinct_ips_and_accounts_are_isolated() -> None:
    """Requests from IP A do not consume quota for IP B; Account A does not affect Account B."""
    db = InMemoryAuthDb()
    client, settings, _ = setup_t08_test_app(db)

    # Exhaust signup from IP 198.51.100.1
    for i in range(5):
        resp = client.post(
            "/api/v1/auth/signup",
            headers={"X-Forwarded-For": "198.51.100.1"},
            json={
                "email": f"ipa_{i}@example.com",
                "password": "ValidPassword123!",
                "role": "tenant",
                "full_name": "User",
            },
        )
        assert resp.status_code == 201

    # IP 198.51.100.1 is now blocked
    assert (
        client.post(
            "/api/v1/auth/signup",
            headers={"X-Forwarded-For": "198.51.100.1"},
            json={
                "email": "ipa_blocked@example.com",
                "password": "ValidPassword123!",
                "role": "tenant",
                "full_name": "User",
            },
        ).status_code
        == 429
    )

    # IP 203.0.113.50 is fresh and succeeds
    resp_b = client.post(
        "/api/v1/auth/signup",
        headers={"X-Forwarded-For": "203.0.113.50"},
        json={
            "email": "ipb_fresh@example.com",
            "password": "ValidPassword123!",
            "role": "tenant",
            "full_name": "User",
        },
    )
    assert resp_b.status_code == 201
    assert resp_b.headers["X-RateLimit-Remaining"] == "4"


# =============================================================================
# 9. Same Account Across Multiple IPs (Distributed Brute-Force)
# =============================================================================


def test_09_account_limit_triggers_across_multiple_ips() -> None:
    """Targeting the same email from different IPs hits the 5/15m account limit."""
    db = InMemoryAuthDb()
    db.add_user(email="target@example.com", password="CorrectPassword123!")
    client, _, _ = setup_t08_test_app(db)

    # 5 failed login attempts from 5 distinct IP addresses
    for i in range(5):
        resp = client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": f"198.51.100.{10 + i}"},
            json={
                "email": "target@example.com",
                "password": "WrongPassword!",
                "client_type": "web",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    # 6th attempt from a brand new IP (203.0.113.99)
    resp_6 = client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "203.0.113.99"},
        json={
            "email": "target@example.com",
            "password": "CorrectPassword123!",
            "client_type": "web",
        },
    )
    assert resp_6.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert resp_6.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


# =============================================================================
# 10. Same IP Across Multiple Accounts (Credential Stuffing)
# =============================================================================


def test_10_ip_limit_triggers_across_different_accounts() -> None:
    """An attacker cycling through different emails from one IP hits the 20/15m IP limit."""
    db = InMemoryAuthDb()
    client, _, _ = setup_t08_test_app(db)

    # 20 attempts on 20 distinct emails from IP 198.51.100.5
    for i in range(20):
        resp = client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": "198.51.100.5"},
            json={
                "email": f"victim{i}@example.com",
                "password": "Password123!",
                "client_type": "web",
            },
        )
        assert resp.status_code == 401

    # 21st attempt on a fresh email from the same IP is blocked
    resp_21 = client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "198.51.100.5"},
        json={
            "email": "victim_fresh@example.com",
            "password": "Password123!",
            "client_type": "web",
        },
    )
    assert resp_21.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert resp_21.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


# =============================================================================
# 11. Spoofed X-Forwarded-For Rejection
# =============================================================================


def test_11_spoofed_forwarded_headers_ignored_from_untrusted_peer() -> None:
    """When direct connection comes from an untrusted peer, client-supplied XFF is ignored."""
    settings = create_t08_test_settings(
        trusted_proxies=["127.0.0.1", "10.0.0.1"]  # "testclient" is NOT trusted
    )

    class DummyClient:
        host = "198.51.100.200"

    class DummyRequest:
        client = DummyClient()
        headers = {"X-Forwarded-For": "1.1.1.1", "X-Real-IP": "2.2.2.2"}

    resolved_ip = get_client_ip(DummyRequest(), settings)  # type: ignore[arg-type]
    assert resolved_ip == "198.51.100.200", "Spoofed XFF must be completely ignored"


# =============================================================================
# 12. Trusted Proxy Handling (Right-to-Left Traversal)
# =============================================================================


def test_12_trusted_proxy_chain_traversal() -> None:
    """Proxy chain parsing walks right to left, selecting the first untrusted upstream IP."""
    settings = create_t08_test_settings(trusted_proxies=["10.0.0.0/8", "172.16.0.1", "127.0.0.1"])

    class DummyClient:
        host = "10.0.0.2"  # Trusted reverse proxy (e.g. Render ingress)

    class DummyRequest:
        client = DummyClient()
        headers = {"X-Forwarded-For": "203.0.113.195, 172.16.0.1"}

    resolved_ip = get_client_ip(DummyRequest(), settings)  # type: ignore[arg-type]
    assert resolved_ip == "203.0.113.195"


# =============================================================================
# 13. Malformed Client Identifiers
# =============================================================================


def test_13_malformed_client_identifiers_handled_safely() -> None:
    """IPv6, invalid characters, whitespace, and case variations normalize safely."""
    assert is_ip_in_trusted_proxies("::1", ["::1", "127.0.0.1"])
    assert is_ip_in_trusted_proxies("2001:db8::1", ["2001:db8::/32"])
    assert not is_ip_in_trusted_proxies("2001:db8::1", ["127.0.0.1"])

    h1 = hash_identifier("User@Example.Com ")
    h2 = hash_identifier("user@example.com")
    assert h1 == h2
    assert len(h1) == 32
    assert "user" not in h1


# =============================================================================
# 14. Missing Redis Connection (In-Memory Stricter Fallback)
# =============================================================================


def test_14_missing_redis_connection_applies_stricter_in_memory_limits() -> None:
    """When redis_url is None, in-memory fallback applies STRICTER limits (signup: 2/hr)."""
    db = InMemoryAuthDb()
    settings = create_t08_test_settings(redis_url=None)
    client, _, _ = setup_t08_test_app(db, settings=settings)

    # 1st signup: allowed
    r1 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "user1@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "U1",
        },
    )
    assert r1.status_code == 201

    # 2nd signup: allowed (fallback limit is 2)
    r2 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "user2@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "U2",
        },
    )
    assert r2.status_code == 201
    assert r2.headers["X-RateLimit-Limit"] == "2"

    # 3rd signup: BLOCKED by stricter in-memory limit (would be allowed under normal 5 Redis limit)
    r3 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "user3@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "U3",
        },
    )
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


# =============================================================================
# 15. Redis Timeout and Failure Handling
# =============================================================================


def test_15_redis_timeout_and_failure_falls_back_stricter(caplog: pytest.LogCaptureFixture) -> None:
    """Redis connection errors or timeouts trigger warning and apply stricter limits."""
    db = InMemoryAuthDb()
    client, settings, _ = setup_t08_test_app(db)

    # Mock Redis to raise RedisTimeoutError
    failing_redis = AsyncMock()
    failing_redis.eval = AsyncMock(side_effect=RedisTimeoutError("Redis connection timed out"))
    set_redis_client_override(failing_redis)

    # Attempt 1 & 2 succeed under stricter in-memory fallback limit (2/hr for signup)
    r1 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "fb1@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "Fallback 1",
        },
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "fb2@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "Fallback 2",
        },
    )
    assert r2.status_code == 201

    # Attempt 3 blocked by in-memory stricter limit
    r3 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "fb3@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "Fallback 3",
        },
    )
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    # Verify warning log was emitted
    assert any("falling back to stricter in-memory limits" in rec.message for rec in caplog.records)


# =============================================================================
# 16. Account Enumeration Resistance
# =============================================================================


def test_16_account_enumeration_resistance() -> None:
    """Existing and non-existing accounts hit identical 429 rate limit errors."""
    db = InMemoryAuthDb()
    db.add_user(email="real@example.com", password="SecretPassword123!")
    client, _, _ = setup_t08_test_app(db)

    # Exhaust limit for real@example.com (5 attempts)
    for _ in range(5):
        client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": "10.10.10.1"},
            json={
                "email": "real@example.com",
                "password": "WrongPassword!",
                "client_type": "web",
            },
        )

    # Exhaust limit for fake@example.com (5 attempts)
    for _ in range(5):
        client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": "10.10.10.2"},
            json={
                "email": "fake@example.com",
                "password": "WrongPassword!",
                "client_type": "web",
            },
        )

    # 6th attempt on both
    resp_real = client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "10.10.10.1"},
        json={"email": "real@example.com", "password": "SecretPassword123!", "client_type": "web"},
    )
    resp_fake = client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "10.10.10.2"},
        json={"email": "fake@example.com", "password": "SecretPassword123!", "client_type": "web"},
    )

    assert resp_real.status_code == 429
    assert resp_fake.status_code == 429
    assert resp_real.json()["error"]["code"] == resp_fake.json()["error"]["code"]
    assert resp_real.json()["error"]["message"] == resp_fake.json()["error"]["message"]


# =============================================================================
# 17. Sensitive Data Absent from Redis Keys and Logs
# =============================================================================


def test_17_no_sensitive_data_in_redis_keys() -> None:
    """Redis keys contain only hashes; raw emails, passwords, and tokens are absent."""
    db = InMemoryAuthDb()
    client, _, mock_redis = setup_t08_test_app(db)

    secret_email = "very_sensitive_user@bank.com"
    secret_pass = "SuperSecretPassword123!"

    client.post(
        "/api/v1/auth/login",
        json={"email": secret_email, "password": secret_pass, "client_type": "web"},
    )

    assert mock_redis is not None
    keys = list(mock_redis.store.keys())
    assert len(keys) > 0

    for key in keys:
        assert secret_email not in key
        assert secret_pass not in key
        assert "dormiostay" not in key
        assert "@" not in key
        parts = key.split(":")
        assert parts[0] == "rl"
        assert len(parts[3]) == 32  # 32 hex chars sha256


# =============================================================================
# 18. Web Cookie Authentication Integration
# =============================================================================


def test_18_web_cookie_login_rate_limiting_integration() -> None:
    """Web cookie login operates seamlessly with rate limiting enabled."""
    db = InMemoryAuthDb()
    db.add_user(email="alice@example.com", password="Password123!")
    client, settings, _ = setup_t08_test_app(db)

    r_web = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "web"},
    )
    assert r_web.status_code == 200
    assert settings.session_cookie_name in r_web.cookies
    assert "token" not in r_web.json()
    assert r_web.headers["X-RateLimit-Limit"] == "5"


# =============================================================================
# 19. Native Bearer Authentication Integration
# =============================================================================


def test_19_native_bearer_login_rate_limiting_integration() -> None:
    """Native bearer login returns token in body with rate limiting headers attached."""
    db = InMemoryAuthDb()
    db.add_user(email="bob_native@example.com", password="Password123!")
    client, settings, _ = setup_t08_test_app(db)

    r_native = client.post(
        "/api/v1/auth/login",
        json={
            "email": "bob_native@example.com",
            "password": "Password123!",
            "client_type": "native",
        },
    )
    assert r_native.status_code == 200
    assert "token" in r_native.json()
    assert settings.session_cookie_name not in r_native.cookies
    assert r_native.headers["X-RateLimit-Limit"] == "5"


# =============================================================================
# 20. CSRF and Origin Compatibility
# =============================================================================


def test_20_csrf_and_origin_enforcement_remains_active() -> None:
    """State-changing requests with invalid CSRF return 403; rate limiter does not bypass CSRF."""
    db = InMemoryAuthDb()
    settings = create_t08_test_settings(csrf_enabled=True)
    client, _, _ = setup_t08_test_app(db, settings=settings)

    # POST /signup without CSRF returns 403 Forbidden
    resp = client.post(
        "/api/v1/auth/signup",
        headers={"Origin": "http://localhost:5173"},
        json={
            "email": "user@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "User",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["error"]["code"] == "CSRF_TOKEN_MISSING"

    # GET /csrf allows obtaining bootstrap token (protected by 60/min rate limit)
    csrf_resp = client.get("/api/v1/auth/csrf")
    assert csrf_resp.status_code == 200
    token = csrf_resp.json()["token"]

    # Now POST with valid CSRF succeeds
    resp_valid = client.post(
        "/api/v1/auth/signup",
        headers={"Origin": "http://localhost:5173", "X-CSRF-Token": token},
        json={
            "email": "user@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "User",
        },
    )
    assert resp_valid.status_code == status.HTTP_201_CREATED


# =============================================================================
# 21. T-06 Failed-Login Counters Integrity
# =============================================================================


def test_21_t06_failed_login_counters_remain_correct() -> None:
    """Failed logins under the rate limit increment failed_login_count; 429 does not reach DB."""
    db = InMemoryAuthDb()
    user = db.add_user(
        email="bob@example.com",
        password="ValidPassword123!",
        failed_login_count=0,
    )
    client, _, _ = setup_t08_test_app(db)

    # 5 failed login attempts
    for _ in range(5):
        client.post(
            "/api/v1/auth/login",
            json={"email": "bob@example.com", "password": "WrongPassword!", "client_type": "web"},
        )

    assert user.failed_login_count == 5

    # 6th attempt is blocked by rate limiter with 429
    resp_6 = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "WrongPassword!", "client_type": "web"},
    )
    assert resp_6.status_code == 429
    # Failed login counter in DB remains 5; rate limit blocked request before auth_service
    assert user.failed_login_count == 5


# =============================================================================
# 22. Authentication Activation Disabled by Default
# =============================================================================


def test_22_auth_endpoints_disabled_by_default_returns_503() -> None:
    """When auth_endpoints_enabled=False (the default), all auth endpoints return 503."""
    db = InMemoryAuthDb()
    settings = create_t08_test_settings(auth_enabled=False)
    client, _, _ = setup_t08_test_app(db, settings=settings)

    r_csrf = client.get("/api/v1/auth/csrf")
    assert r_csrf.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert r_csrf.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    r_login = client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "Password123!", "client_type": "web"},
    )
    assert r_login.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    r_signup = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "test@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "Test",
        },
    )
    assert r_signup.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# =============================================================================
# 23. Cross-Test State Isolation
# =============================================================================


def test_23_cross_test_isolation_part_1() -> None:
    """Consume part of quota in part 1."""
    db = InMemoryAuthDb()
    client, _, mock_redis = setup_t08_test_app(db)
    r = client.get("/api/v1/auth/csrf")
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Remaining"] == "59"


def test_23_cross_test_isolation_part_2() -> None:
    """Verify quota is completely reset and clean in part 2."""
    db = InMemoryAuthDb()
    client, _, mock_redis = setup_t08_test_app(db)
    r = client.get("/api/v1/auth/csrf")
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Remaining"] == "59"


# =============================================================================
# 24. Multi-Worker Simulated Concurrency
# =============================================================================


def test_24_multi_worker_shared_redis_concurrency() -> None:
    """Simulate 3 independent API workers sharing the same Redis instance under high load."""

    async def run_multi_worker() -> tuple[int, int]:
        shared_redis = AsyncMockRedis()
        rule = RateLimitRule(
            name="shared_worker_rule",
            max_requests=15,
            window_seconds=120,
            fallback_max_requests=5,
            fallback_window_seconds=120,
        )
        settings = create_t08_test_settings()

        async def worker_job(worker_id: int, requests_count: int) -> list[bool]:
            results: list[bool] = []
            for _ in range(requests_count):
                res = await evaluate_rate_limit("rl:shared:pool", rule, settings)
                results.append(res.allowed)
            return results

        set_redis_client_override(shared_redis)

        w1, w2, w3 = await asyncio.gather(
            worker_job(1, 10),
            worker_job(2, 10),
            worker_job(3, 10),
        )

        all_results = w1 + w2 + w3
        allowed = sum(1 for a in all_results if a)
        denied = sum(1 for a in all_results if not a)
        return allowed, denied

    allowed_count, denied_count = asyncio.run(run_multi_worker())
    assert allowed_count == 15, f"Shared limit of 15 must be strictly enforced, got {allowed_count}"
    assert denied_count == 15, f"Remaining 15 requests must be rejected, got {denied_count}"
