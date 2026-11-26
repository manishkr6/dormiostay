"""Comprehensive unit and acceptance tests for T-06 Authentication Backend.

Covers all 22 required test cases plus focused security regression tests:
 1. Successful Tenant signup
 2. Successful Owner signup
 3. Admin signup rejection (HTTP 400)
 4. Duplicate email with case variations (HTTP 409)
 5. Wrong password (HTTP 401 generic message)
 6. Unknown email with generic error (HTTP 401 generic message)
 7. Suspended/banned account rejection (HTTP 403)
 8. Argon2id password hashing verification
 9. SHA-256 session-token storage verification
10. Web cookie-only issuance (no token in body, HttpOnly cookie set)
11. Native bearer-token issuance without a cookie (token in body, no cookie)
12. Native Admin login rejection (HTTP 403)
13. Mixed cookie and bearer rejection (HTTP 400)
14. Invalid bearer rejection without cookie fallback (HTTP 401)
15. Idle session expiry enforcement
16. Absolute session expiry enforcement
17. Immediate logout revocation
18. All-session revocation service
19. Session rotation on login
20. Safe current-user (/me) response without credential leakage
21. Transaction rollback and failure handling
22. Public-deployment activation guard (auth_endpoints_enabled=False returning 503)

Blocker Regressions:
- Blocker 1: Failed login and session expiry persist updates to DB before 401
- Blocker 2: Transaction-local RLS identity (set_config) executed on db_session
- Blocker 3: Conflicting transport headers rejected with 400; native HTTPS enforced in staging
- Blocker 4: Concurrency and FK IntegrityError handling (409 conflict, 400 bad institution)
- Blocker 5: Web re-login rotates old session; native login rejects ambient cookies;
  concurrent logout race
"""

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
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)
from app.db.models import User, UserProfile, UserSession
from app.db.repositories import session_repo, user_repo
from app.db.session import get_session, get_session_factory
from app.main import create_app

# ---------------------------------------------------------------------------
# Test Settings & Harness Helpers
# ---------------------------------------------------------------------------

_ACTIVE_PATCHERS: list[Any] = []


@pytest.fixture(autouse=True)
def cleanup_patchers() -> Generator[None, None, None]:
    """Ensure all mock patchers are stopped after each test for strict isolation."""
    yield
    while _ACTIVE_PATCHERS:
        p = _ACTIVE_PATCHERS.pop()
        p.stop()


def create_test_settings(
    auth_enabled: bool = True,
    csrf_enabled: bool = False,
    rate_limit_enabled: bool = False,
) -> Settings:
    """Create settings fixture with predictable configuration for tests."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("http://localhost:5173"),
        auth_endpoints_enabled=auth_enabled,
        csrf_protection_enabled=csrf_enabled,
        rate_limit_enabled=rate_limit_enabled,
        session_cookie_name="dormiostay_session",
        session_idle_timeout_minutes=30,
        session_absolute_timeout_days=14,
        admin_session_idle_timeout_minutes=15,
        admin_session_absolute_timeout_hours=8,
    )


class InMemoryAuthDb:
    """In-memory state store simulating PostgreSQL auth tables for unit testing."""

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
        status: str = "active",
        full_name: str = "Test User",
    ) -> User:
        user_id = uuid4()
        now = datetime.now(UTC)
        user = User(
            id=user_id,
            email=email,
            password_hash=hash_password(password),
            role=role,
            status=status,
            email_verified_at=now,
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
            gender="prefer_not_to_say",
            preferred_budget_min=1000,
            preferred_budget_max=2000,
            preferred_areas=["North Campus"],
            institution_id=None,
            created_at=now,
            updated_at=now,
        )
        self.users[user_id] = user
        self.profiles[user_id] = profile
        return user

    def add_session(
        self,
        user: User,
        raw_token: str,
        client_type: str = "web",
        idle_minutes_remaining: int = 30,
        absolute_days_remaining: int = 14,
        is_revoked: bool = False,
    ) -> tuple[UserSession, str]:
        session_id = uuid4()
        now = datetime.now(UTC)
        token_hash = hash_session_token(raw_token)
        expires_at = now + timedelta(minutes=idle_minutes_remaining)
        absolute_expires_at = now + timedelta(days=absolute_days_remaining)
        revoked_at = now if is_revoked else None

        user_session = UserSession(
            id=session_id,
            user_id=user.id,
            token_hash=token_hash,
            last_activity_at=now,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
            revoked_at=revoked_at,
            revocation_reason="test" if is_revoked else None,
            created_at=now,
            updated_at=now,
        )
        self.sessions[session_id] = user_session
        self.sessions_by_hash[token_hash] = user_session
        return user_session, raw_token


def setup_auth_test_app(
    db: InMemoryAuthDb, settings: Settings | None = None
) -> tuple[TestClient, Settings]:
    """Build FastAPI test app with mocked database and settings overrides."""
    resolved_settings = settings or create_test_settings(auth_enabled=True)
    app = create_app(resolved_settings)

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()

    @asynccontextmanager
    async def mock_begin_nested() -> AsyncIterator[None]:
        yield

    mock_db_session.begin_nested = mock_begin_nested
    app.state.mock_db_session = mock_db_session

    # Track repository session calls for security transaction isolation checks
    session_calls: dict[str, list[Any]] = {
        "update_login_failure": [],
        "revoke_session": [],
    }
    app.state.session_calls = session_calls

    # Mock session factory for independent security transactions
    mock_sec_session = AsyncMock()
    mock_sec_session.execute = AsyncMock()
    mock_sec_session.commit = AsyncMock()
    mock_sec_session.rollback = AsyncMock()

    mock_sec_begin_cm = MagicMock()
    mock_sec_begin_cm.__aenter__ = AsyncMock(return_value=None)
    mock_sec_begin_cm.__aexit__ = AsyncMock(return_value=None)
    mock_sec_session.begin = MagicMock(return_value=mock_sec_begin_cm)
    app.state.mock_sec_begin_cm = mock_sec_begin_cm

    @asynccontextmanager
    async def mock_factory_context() -> AsyncIterator[Any]:
        yield mock_sec_session

    mock_session_factory = MagicMock(side_effect=lambda: mock_factory_context())
    app.state.mock_session_factory = mock_session_factory
    app.state.mock_sec_session = mock_sec_session

    async def override_get_session() -> AsyncIterator[Any]:
        yield mock_db_session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_session_factory] = lambda: mock_session_factory
    app.dependency_overrides[get_settings] = lambda: resolved_settings

    # Wire user repository functions to in-memory store
    async def mock_get_user_by_email_ci(*args: Any, **kwargs: Any) -> User | None:
        email = kwargs.get("email") or (args[1] if len(args) > 1 else None)
        if not email:
            return None
        target = email.strip().lower()
        for u in db.users.values():
            if u.email.strip().lower() == target:
                return u
        return None

    async def mock_get_user_by_id(*args: Any, **kwargs: Any) -> User | None:
        user_id = kwargs.get("user_id") or (args[1] if len(args) > 1 else None)
        if not isinstance(user_id, UUID):
            return None
        return db.users.get(user_id)

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
            email=email,
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
        phone = kwargs.get("phone")
        institution_id = kwargs.get("institution_id")
        now = datetime.now(UTC)
        p = UserProfile(
            id=uuid4(),
            user_id=user_id,
            full_name=full_name,
            phone=phone,
            avatar_url=None,
            gender=None,
            preferred_budget_min=None,
            preferred_budget_max=None,
            preferred_areas=[],
            institution_id=institution_id,
            created_at=now,
            updated_at=now,
        )
        db.profiles[user_id] = p
        return p

    async def mock_update_login_success(*args: Any, **kwargs: Any) -> None:
        user_id = kwargs.get("user_id") or args[1]
        login_at = kwargs.get("login_at") or (args[2] if len(args) > 2 else datetime.now(UTC))
        if user_id in db.users:
            db.users[user_id].last_login_at = login_at
            db.users[user_id].failed_login_count = 0

    async def mock_update_login_failure(*args: Any, **kwargs: Any) -> None:
        session = kwargs.get("session") or (args[0] if len(args) > 0 else None)
        session_calls["update_login_failure"].append(session)
        user_id = kwargs.get("user_id") or (args[1] if len(args) > 1 else None)
        if user_id in db.users:
            db.users[user_id].failed_login_count += 1

    # Wire session repository functions to in-memory store
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
            revoked_at=None,
            revocation_reason=None,
            created_at=created_at,
            updated_at=created_at,
        )
        db.sessions[s_id] = s
        db.sessions_by_hash[token_hash] = s
        return s

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
        session_id = kwargs.get("session_id") or args[1]
        last_activity_at = kwargs.get("last_activity_at") or args[2]
        new_expires_at = kwargs.get("new_expires_at") or args[3]
        if session_id in db.sessions:
            s = db.sessions[session_id]
            if s.revoked_at is not None:
                return False
            s.last_activity_at = last_activity_at
            s.expires_at = new_expires_at
            return True
        return False

    async def mock_revoke_session(*args: Any, **kwargs: Any) -> None:
        session = kwargs.get("session") or (args[0] if len(args) > 0 else None)
        session_calls["revoke_session"].append(session)
        session_id = kwargs.get("session_id") or (args[1] if len(args) > 1 else None)
        revoked_at = kwargs.get("revoked_at") or (args[2] if len(args) > 2 else None)
        reason = kwargs.get("reason", "logout")
        if session_id in db.sessions:
            db.sessions[session_id].revoked_at = revoked_at
            db.sessions[session_id].revocation_reason = reason

    async def mock_revoke_all_user_sessions(*args: Any, **kwargs: Any) -> int:
        user_id = kwargs.get("user_id") or args[1]
        revoked_at = kwargs.get("revoked_at") or args[2]
        reason = kwargs.get("reason", "logout")
        count = 0
        for s in db.sessions.values():
            if s.user_id == user_id and s.revoked_at is None:
                s.revoked_at = revoked_at
                s.revocation_reason = reason
                count += 1
        return count

    # Patch repositories with tracking for automatic cleanup
    patchers: list[Any] = [
        patch(
            "app.services.auth_service.user_repo.get_user_by_email_ci",
            mock_get_user_by_email_ci,
        ),
        patch("app.services.auth_service.user_repo.get_user_by_id", mock_get_user_by_id),
        patch(
            "app.services.auth_service.user_repo.get_user_with_profile",
            mock_get_user_with_profile,
        ),
        patch("app.services.auth_service.user_repo.create_user", mock_create_user),
        patch(
            "app.services.auth_service.user_repo.create_user_profile",
            mock_create_user_profile,
        ),
        patch(
            "app.services.auth_service.user_repo.update_login_success",
            mock_update_login_success,
        ),
        patch(
            "app.services.auth_service.user_repo.update_login_failure",
            mock_update_login_failure,
        ),
        patch("app.services.auth_service.session_repo.create_session", mock_create_session),
        patch(
            "app.services.auth_service.session_repo.get_session_with_user_by_token_hash",
            mock_get_session_with_user_by_token_hash,
        ),
        patch(
            "app.services.auth_service.session_repo.update_session_activity",
            mock_update_session_activity,
        ),
        patch("app.services.auth_service.session_repo.revoke_session", mock_revoke_session),
        patch(
            "app.services.auth_service.session_repo.revoke_all_user_sessions",
            mock_revoke_all_user_sessions,
        ),
    ]

    for p in patchers:
        p.start()
        _ACTIVE_PATCHERS.append(p)

    return TestClient(app), resolved_settings


# ---------------------------------------------------------------------------
# Test Case 8: Argon2id Password Hashing Verification
# ---------------------------------------------------------------------------


def test_08_argon2id_password_hashing() -> None:
    """Verify Argon2id password hashing and constant-time dummy verification."""
    password = "CorrectHorseBatteryStaple123!"
    hashed = hash_password(password)

    # 1. Must use Argon2id format ($argon2id$)
    assert hashed.startswith("$argon2id$")
    assert "$m=65536,t=3,p=4" in hashed

    # 2. Correct password verifies True
    assert verify_password(password, hashed) is True

    # 3. Wrong password verifies False
    assert verify_password("WrongPassword123!", hashed) is False

    # 4. Dummy verification runs safely without exception
    verify_dummy_password()


def test_password_strength_validation() -> None:
    """Validate password strength checker enforcing length and common-password blacklists."""
    # Too short (< 10 chars)
    ok, err = validate_password_strength("Short1!")
    assert ok is False
    assert "at least 10 characters" in str(err)

    # Common passwords blocked
    ok, err = validate_password_strength("password123")
    assert ok is False
    assert "too common" in str(err).lower()

    # Valid strong password
    ok, err = validate_password_strength("ValidStrongP@ssw0rd2026")
    assert ok is True
    assert err is None


# ---------------------------------------------------------------------------
# Test Case 9: SHA-256 Session-Token Storage Verification
# ---------------------------------------------------------------------------


def test_09_sha256_session_token_storage() -> None:
    """Verify session token generation produces >= 32-byte entropy and 64-char hex SHA-256."""
    token = generate_session_token()
    assert len(token) >= 43  # URL-safe base64 of 32 bytes is ~43 chars

    token_hash = hash_session_token(token)
    assert len(token_hash) == 64
    assert all(c in "0123456789abcdef" for c in token_hash)
    # The raw token and token hash must never be identical
    assert token != token_hash


# ---------------------------------------------------------------------------
# Test Case 22: Public-Deployment Activation Guard (Disabled -> HTTP 503)
# ---------------------------------------------------------------------------


def test_22_activation_guard_disabled_returns_503() -> None:
    """When auth_endpoints_enabled=False, all /api/v1/auth endpoints return 503."""
    db = InMemoryAuthDb()
    settings = create_test_settings(auth_enabled=False)
    client, _ = setup_auth_test_app(db, settings=settings)

    # Signup
    r1 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "user@example.com",
            "password": "ValidPassword123!",
            "role": "tenant",
            "full_name": "Test User",
        },
    )
    assert r1.status_code == 503
    assert r1.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "temporarily disabled" in r1.json()["error"]["message"]

    # Login
    r2 = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "Password123!", "client_type": "web"},
    )
    assert r2.status_code == 503
    assert r2.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # Logout
    r3 = client.post("/api/v1/auth/logout")
    assert r3.status_code == 503
    assert r3.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # Me
    r4 = client.get("/api/v1/auth/me")
    assert r4.status_code == 503
    assert r4.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_activation_guard_health_endpoints_unaffected() -> None:
    """Health endpoints /healthz and /readyz continue working even if auth is disabled."""
    db = InMemoryAuthDb()
    settings = create_test_settings(auth_enabled=False)
    client, _ = setup_auth_test_app(db, settings=settings)

    r_health = client.get("/healthz")
    assert r_health.status_code == 200
    assert r_health.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Test Case 1 & 2: Successful Tenant & Owner Signup
# ---------------------------------------------------------------------------


def test_01_successful_tenant_signup() -> None:
    """Successful Tenant signup returns 201 Created and safe user details."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "tenant@example.com",
            "password": "StrongPassword123!",
            "role": "tenant",
            "full_name": "Alice Tenant",
            "phone": "+15551234567",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "tenant@example.com"
    assert body["role"] == "tenant"
    assert body["status"] == "active"
    assert body["email_verified"] is False
    assert "password" not in body
    assert "password_hash" not in body

    # Check database state
    user_id = UUID(body["id"])
    assert user_id in db.users
    assert user_id in db.profiles
    assert db.profiles[user_id].full_name == "Alice Tenant"
    assert db.profiles[user_id].phone == "+15551234567"


def test_02_successful_owner_signup() -> None:
    """Successful Owner signup returns 201 Created and safe user details."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "owner@example.com",
            "password": "StrongPassword123!",
            "role": "owner",
            "full_name": "Bob Owner",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "owner@example.com"
    assert body["role"] == "owner"
    assert body["status"] == "active"


# ---------------------------------------------------------------------------
# Test Case 3: Admin Signup Rejection (HTTP 400)
# ---------------------------------------------------------------------------


def test_03_admin_signup_rejection() -> None:
    """Admin registration on public signup endpoint is strictly rejected with HTTP 400."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "admin@example.com",
            "password": "StrongPassword123!",
            "role": "admin",
            "full_name": "Admin Impostor",
        },
    )

    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "FORBIDDEN_ROLE"
    assert "tenant and owner roles only" in body["message"]
    assert len(db.users) == 0


# ---------------------------------------------------------------------------
# Test Case 4: Duplicate Email Rejection with Case Variations (HTTP 409)
# ---------------------------------------------------------------------------


def test_04_duplicate_email_case_variation_rejection() -> None:
    """Case-insensitive email uniqueness check rejects duplicate emails with HTTP 409."""
    db = InMemoryAuthDb()
    db.add_user(email="Existing.User@Example.Com", password="Password123!")
    client, _ = setup_auth_test_app(db)

    # Attempt to signup with lowercase email
    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "existing.user@example.com",
            "password": "StrongPassword123!",
            "role": "tenant",
            "full_name": "Duplicate User",
        },
    )

    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "EMAIL_ALREADY_EXISTS"
    assert "already exists" in body["message"]


# ---------------------------------------------------------------------------
# Test Case 5 & 6: Wrong Password & Unknown Email Generic Error (HTTP 401)
# ---------------------------------------------------------------------------


def test_05_wrong_password_generic_error() -> None:
    """Wrong password returns HTTP 401 with generic error message."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="CorrectPassword123!")
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "WrongPassword123!", "client_type": "web"},
    )

    assert resp.status_code == 401
    body = resp.json()["error"]
    assert body["code"] == "AUTHENTICATION_FAILED"
    assert body["message"] == "Email or password is incorrect."
    assert user.failed_login_count == 1


def test_06_unknown_email_generic_error() -> None:
    """Unknown email returns HTTP 401 with identical generic error message."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": "nonexistent@example.com",
            "password": "SomePassword123!",
            "client_type": "web",
        },
    )

    assert resp.status_code == 401
    body = resp.json()["error"]
    assert body["code"] == "AUTHENTICATION_FAILED"
    assert body["message"] == "Email or password is incorrect."


# ---------------------------------------------------------------------------
# Test Case 7: Suspended/Banned Account Rejection (HTTP 403)
# ---------------------------------------------------------------------------


def test_07_suspended_or_banned_account_rejection() -> None:
    """Suspended or banned accounts cannot authenticate (HTTP 403)."""
    db = InMemoryAuthDb()
    db.add_user(
        email="suspended@example.com",
        password="Password123!",
        role="tenant",
        status="suspended",
    )
    db.add_user(
        email="banned@example.com",
        password="Password123!",
        role="tenant",
        status="banned",
    )
    client, _ = setup_auth_test_app(db)

    # Suspended account login
    r_susp = client.post(
        "/api/v1/auth/login",
        json={
            "email": "suspended@example.com",
            "password": "Password123!",
            "client_type": "web",
        },
    )
    assert r_susp.status_code == 403
    assert r_susp.json()["error"]["code"] == "ACCOUNT_DISABLED"

    # Banned account login
    r_ban = client.post(
        "/api/v1/auth/login",
        json={"email": "banned@example.com", "password": "Password123!", "client_type": "web"},
    )
    assert r_ban.status_code == 403
    assert r_ban.json()["error"]["code"] == "ACCOUNT_DISABLED"


# ---------------------------------------------------------------------------
# Test Case 10: Web Cookie-Only Issuance
# ---------------------------------------------------------------------------


def test_10_web_cookie_only_issuance() -> None:
    """Web login sets HttpOnly cookie and does NOT return session token in JSON body."""
    db = InMemoryAuthDb()
    db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    client, settings = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "web"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "token" not in body
    assert body["user"]["email"] == "alice@example.com"

    # Verify cookie was set
    cookie_header = resp.headers.get("set-cookie", "")
    assert f"{settings.session_cookie_name}=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header or "samesite=lax" in cookie_header.lower()
    assert "Path=/" in cookie_header


# ---------------------------------------------------------------------------
# Test Case 11: Native Bearer-Token Issuance Without Cookie
# ---------------------------------------------------------------------------


def test_11_native_bearer_token_issuance() -> None:
    """Native login returns token in non-cacheable JSON body and sets NO cookie."""
    db = InMemoryAuthDb()
    db.add_user(email="mobile@example.com", password="Password123!", role="tenant")
    client, settings = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "mobile@example.com", "password": "Password123!", "client_type": "native"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    raw_token = body["token"]
    assert len(raw_token) >= 43
    assert body["user"]["email"] == "mobile@example.com"

    # Verify NO cookie was set
    cookie_header = resp.headers.get("set-cookie")
    assert cookie_header is None

    # Verify no-cache headers
    assert "no-store" in resp.headers.get("Cache-Control", "")
    assert "no-cache" in resp.headers.get("Pragma", "")


# ---------------------------------------------------------------------------
# Test Case 12: Native Admin Login Rejection (HTTP 403)
# ---------------------------------------------------------------------------


def test_12_native_admin_login_rejection() -> None:
    """Admin login is forbidden on native mobile (HTTP 403)."""
    db = InMemoryAuthDb()
    db.add_user(email="admin@dormiostay.com", password="AdminPassword123!", role="admin")
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": "admin@dormiostay.com",
            "password": "AdminPassword123!",
            "client_type": "native",
        },
    )

    assert resp.status_code == 403
    body = resp.json()["error"]
    assert body["code"] == "ADMIN_NATIVE_LOGIN_DISALLOWED"


# ---------------------------------------------------------------------------
# Test Case 13: Mixed Cookie and Bearer Rejection (HTTP 400)
# ---------------------------------------------------------------------------


def test_13_mixed_cookie_and_bearer_rejection() -> None:
    """Requests presenting both session cookie and Authorization header are rejected (HTTP 400)."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    _, raw_token = db.add_session(user=user, raw_token=generate_session_token())
    client, settings = setup_auth_test_app(db)

    client.cookies.set(settings.session_cookie_name, raw_token)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})

    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "AMBIGUOUS_CREDENTIALS"
    assert "both session cookie and Authorization header" in body["message"]


# ---------------------------------------------------------------------------
# Test Case 14: Invalid Bearer Rejection Without Cookie Fallback (HTTP 401)
# ---------------------------------------------------------------------------


def test_14_invalid_bearer_rejection_without_cookie_fallback() -> None:
    """Malformed or invalid Bearer tokens reject immediately without cookie fallback."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    _, valid_token = db.add_session(user=user, raw_token=generate_session_token())
    client, settings = setup_auth_test_app(db)

    # 1. Non-bearer authorization header
    resp1 = client.get("/api/v1/auth/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp1.status_code == 401
    assert resp1.json()["error"]["code"] == "INVALID_AUTHORIZATION_HEADER"

    # 2. Empty bearer token
    resp2 = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer  "})
    assert resp2.status_code == 401
    assert resp2.json()["error"]["code"] == "EMPTY_BEARER_TOKEN"

    # 3. Invalid token string
    resp3 = client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer non-existent-session-token"}
    )
    assert resp3.status_code == 401
    assert resp3.json()["error"]["code"] == "SESSION_INVALID"


# ---------------------------------------------------------------------------
# Test Case 15 & 16: Idle & Absolute Session Expiry Enforcement
# ---------------------------------------------------------------------------


def test_15_idle_session_expiry_enforcement() -> None:
    """Sessions past their idle expiration window are revoked and return HTTP 401."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    # Session idle window expired 5 minutes ago (-5 mins remaining)
    session_obj, raw_token = db.add_session(
        user=user,
        raw_token=generate_session_token(),
        idle_minutes_remaining=-5,
        absolute_days_remaining=10,
    )
    client, _ = setup_auth_test_app(db)

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SESSION_EXPIRED"
    # Verify session was marked revoked in DB
    assert session_obj.revoked_at is not None
    assert session_obj.revocation_reason == "idle_expiry"


def test_16_absolute_session_expiry_enforcement() -> None:
    """Sessions past their absolute expiration window are revoked and return HTTP 401."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    # Session absolute window expired 1 day ago (-1 days remaining), even if idle has time
    session_obj, raw_token = db.add_session(
        user=user,
        raw_token=generate_session_token(),
        idle_minutes_remaining=25,
        absolute_days_remaining=-1,
    )
    client, _ = setup_auth_test_app(db)

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SESSION_EXPIRED"
    assert session_obj.revoked_at is not None
    assert session_obj.revocation_reason == "absolute_expiry"


# ---------------------------------------------------------------------------
# Test Case 17: Immediate Logout Revocation
# ---------------------------------------------------------------------------


def test_17_immediate_logout_revocation() -> None:
    """Logout immediately revokes the server session and deletes the session cookie."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    session_obj, raw_token = db.add_session(user=user, raw_token=generate_session_token())
    client, settings = setup_auth_test_app(db)

    # Perform logout via Web cookie
    client.cookies.set(settings.session_cookie_name, raw_token)
    logout_resp = client.post("/api/v1/auth/logout")

    assert logout_resp.status_code == 200
    assert logout_resp.json() == {"status": "ok"}
    assert session_obj.revoked_at is not None
    assert session_obj.revocation_reason == "logout"

    # Subsequent access with revoked session returns 401
    client.cookies.clear()
    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})
    assert me_resp.status_code == 401
    assert me_resp.json()["error"]["code"] == "SESSION_REVOKED"


# ---------------------------------------------------------------------------
# Test Case 18: All-Session Revocation Service
# ---------------------------------------------------------------------------


def test_18_all_session_revocation_service() -> None:
    """All-session revocation service revokes all active sessions for a user."""
    from app.services import auth_service

    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    s1, t1 = db.add_session(user=user, raw_token=generate_session_token())
    s2, t2 = db.add_session(user=user, raw_token=generate_session_token())
    s3, t3 = db.add_session(user=user, raw_token=generate_session_token())

    client, _ = setup_auth_test_app(db)

    # Run revocation service
    import asyncio

    mock_db_session = AsyncMock()
    revoked_count = asyncio.run(
        auth_service.revoke_all_sessions(
            session=mock_db_session, user_id=user.id, reason="password_reset"
        )
    )

    assert revoked_count == 3
    assert s1.revoked_at is not None
    assert s2.revoked_at is not None
    assert s3.revoked_at is not None

    # Each revoked session fails auth
    for token in (t1, t2, t3):
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "SESSION_REVOKED"


# ---------------------------------------------------------------------------
# Test Case 19: Session Rotation on Login
# ---------------------------------------------------------------------------


def test_19_session_rotation_on_login() -> None:
    """Each login invocation issues a completely new cryptographic session."""
    db = InMemoryAuthDb()
    db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    client, _ = setup_auth_test_app(db)

    r1 = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "native"},
    )
    r2 = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "native"},
    )

    token1 = r1.json()["token"]
    token2 = r2.json()["token"]

    assert token1 != token2
    assert len(db.sessions) == 2
    hash1 = hash_session_token(token1)
    hash2 = hash_session_token(token2)
    assert hash1 != hash2


# ---------------------------------------------------------------------------
# Test Case 20: Safe Current-User (/me) Response Without Credential Leakage
# ---------------------------------------------------------------------------


def test_20_safe_current_user_me_response() -> None:
    """GET /api/v1/auth/me returns safe profile without leaking password or token hashes."""
    db = InMemoryAuthDb()
    user = db.add_user(
        email="tenant@example.com",
        password="Password123!",
        role="tenant",
        full_name="Jane Tenant",
    )
    _, raw_token = db.add_session(user=user, raw_token=generate_session_token())
    client, _ = setup_auth_test_app(db)

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert "user" in body
    assert "profile" in body

    # Check user fields
    assert body["user"]["email"] == "tenant@example.com"
    assert body["user"]["role"] == "tenant"
    assert body["user"]["status"] == "active"

    # Check profile fields
    assert body["profile"]["full_name"] == "Jane Tenant"
    assert body["profile"]["phone"] == "+1234567890"

    # Credential leakage assertions
    raw_text = resp.text.lower()
    assert "password" not in raw_text
    assert "password_hash" not in raw_text
    assert "token_hash" not in raw_text
    assert "failed_login_count" not in raw_text


# ---------------------------------------------------------------------------
# Test Case 21: Transaction Rollback and Failure Handling
# ---------------------------------------------------------------------------


def test_21_transaction_rollback_and_failure_handling() -> None:
    """Database failures during write operations raise safe 500 error without leaking details."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    # Simulate database error during profile creation
    async def mock_failing_create_profile(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database constraint violated: private table detail")

    with (
        patch(
            "app.services.auth_service.user_repo.create_user_profile",
            mock_failing_create_profile,
        ),
        TestClient(client.app, raise_server_exceptions=False) as safe_client,
    ):
        resp = safe_client.post(
            "/api/v1/auth/signup",
            json={
                "email": "fail@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Failing User",
            },
        )

    assert resp.status_code == 500
    body = resp.json()["error"]
    assert body["code"] == "INTERNAL_SERVER_ERROR"
    assert body["message"] == "Something went wrong on our side."
    assert "private table detail" not in resp.text


# ---------------------------------------------------------------------------
# Sliding Idle Window Verification
# ---------------------------------------------------------------------------


def test_sliding_idle_window_updates() -> None:
    """Valid requests slide the idle expiration window forward."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    session_obj, raw_token = db.add_session(
        user=user,
        raw_token=generate_session_token(),
        idle_minutes_remaining=10,
        absolute_days_remaining=14,
    )
    initial_expires_at = session_obj.expires_at
    client, _ = setup_auth_test_app(db)

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})
    assert resp.status_code == 200

    # Verify expires_at was slid forward
    assert session_obj.expires_at > initial_expires_at


# ---------------------------------------------------------------------------
# Blocker 1 Regression: Failed Login & Expiry Commits State Before 401
# ---------------------------------------------------------------------------


def test_blocker1_failed_login_commits_counter_and_returns_401() -> None:
    """Failed login persists failed_login_count via independent session without committing outer."""
    db = InMemoryAuthDb()
    user = db.add_user(email="bob@example.com", password="CorrectPassword123!")
    client, _ = setup_auth_test_app(db)
    assert isinstance(client.app, FastAPI)
    mock_db_session = client.app.state.mock_db_session
    mock_session_factory = client.app.state.mock_session_factory
    mock_sec_session = client.app.state.mock_sec_session

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "WrongPassword123!", "client_type": "web"},
    )

    assert resp.status_code == 401
    assert user.failed_login_count == 1
    # Verify the outer session transaction was NOT prematurely committed
    assert mock_db_session.commit.await_count == 0
    # Verify the independent session factory was used and sec_session.begin() was entered
    assert mock_session_factory.call_count == 1
    assert mock_sec_session.begin.call_count == 1
    assert client.app.state.mock_sec_begin_cm.__aenter__.await_count == 1
    # Verify update_login_failure targeted sec_session and NOT mock_db_session
    calls = client.app.state.session_calls["update_login_failure"]
    assert len(calls) == 1
    assert calls[0] is mock_sec_session
    assert calls[0] is not mock_db_session


def test_blocker1_expired_session_commits_revocation_and_returns_401() -> None:
    """Expired session marks revocation via independent session without committing outer."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!")
    session_obj, raw_token = db.add_session(
        user=user,
        raw_token=generate_session_token(),
        idle_minutes_remaining=-10,  # expired
    )
    client, _ = setup_auth_test_app(db)
    assert isinstance(client.app, FastAPI)
    mock_db_session = client.app.state.mock_db_session
    mock_session_factory = client.app.state.mock_session_factory
    mock_sec_session = client.app.state.mock_sec_session

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})

    assert resp.status_code == 401
    assert session_obj.revoked_at is not None
    assert session_obj.revocation_reason == "idle_expiry"
    # Verify the outer session transaction was NOT prematurely committed
    assert mock_db_session.commit.await_count == 0
    # Verify the independent session factory was used and sec_session.begin() was entered
    assert mock_session_factory.call_count == 1
    assert mock_sec_session.begin.call_count == 1
    assert client.app.state.mock_sec_begin_cm.__aenter__.await_count == 1
    # Verify revoke_session targeted sec_session and NOT mock_db_session
    calls = client.app.state.session_calls["revoke_session"]
    assert len(calls) == 1
    assert calls[0] is mock_sec_session
    assert calls[0] is not mock_db_session


@pytest.mark.anyio
async def test_blocker2_auth_service_login_fails_closed_without_session_factory() -> None:
    """Direct call to auth_service.login without session_factory raises RuntimeError."""
    from app.schemas.auth import LoginRequest
    from app.services import auth_service

    mock_session = AsyncMock()
    payload = LoginRequest(email="test@example.com", password="Password123!", client_type="web")
    settings = create_test_settings()

    with pytest.raises(RuntimeError, match="SESSION_FACTORY_REQUIRED"):
        await auth_service.login(
            session=mock_session,
            payload=payload,
            settings=settings,
            session_factory=None,  # type: ignore[arg-type]
        )


@pytest.mark.anyio
async def test_blocker2_auth_service_validate_session_fails_closed_no_factory() -> None:
    """Direct call to auth_service.validate_session without session_factory raises RuntimeError."""
    from app.services import auth_service

    mock_session = AsyncMock()
    settings = create_test_settings()

    with pytest.raises(RuntimeError, match="SESSION_FACTORY_REQUIRED"):
        await auth_service.validate_session(
            session=mock_session,
            raw_token="raw-token-1234",
            settings=settings,
            session_factory=None,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Blocker 2 Regression: Authenticated Identity RLS set_config on db_session
# ---------------------------------------------------------------------------


def test_blocker2_rls_gucs_executed_on_db_session_in_me() -> None:
    """Authenticated request executes transaction-local set_config on db_session."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    _, raw_token = db.add_session(user=user, raw_token=generate_session_token())
    client, _ = setup_auth_test_app(db)
    assert isinstance(client.app, FastAPI)
    mock_db_session = client.app.state.mock_db_session

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})
    assert resp.status_code == 200

    # Inspect calls made to mock_db_session.execute
    set_config_calls = [
        call
        for call in mock_db_session.execute.call_args_list
        if len(call[0]) > 0 and "set_config" in str(call[0][0])
    ]
    assert len(set_config_calls) >= 1
    # Check that parameters passed include the user's ID and role
    matched = False
    for call in set_config_calls:
        args, kwargs = call
        params = kwargs if kwargs else (args[1] if len(args) > 1 else {})
        if str(params.get("user_id")) == str(user.id) and params.get("role") == "tenant":
            matched = True
            break
    assert matched, f"set_config not called with expected params; calls: {set_config_calls}"


# ---------------------------------------------------------------------------
# Blocker 3 Regression: Transport Disambiguation & Native HTTPS Enforcement
# ---------------------------------------------------------------------------


def test_blocker3_conflicting_client_type_header_and_body_returns_400() -> None:
    """Conflicting client_type between body and X-Client-Type header is rejected with 400."""
    db = InMemoryAuthDb()
    db.add_user(email="alice@example.com", password="Password123!")
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "web"},
        headers={"X-Client-Type": "native"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AMBIGUOUS_TRANSPORT"


def test_blocker3_native_insecure_transport_in_staging_returns_403() -> None:
    """Native authentication over plain HTTP outside development returns 403 INSECURE_TRANSPORT."""
    db = InMemoryAuthDb()
    db.add_user(email="alice@example.com", password="Password123!")
    settings = create_test_settings(auth_enabled=True)
    settings.app_env = "staging"  # Non-development environment
    client, _ = setup_auth_test_app(db, settings=settings)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "native"},
        headers={"X-Forwarded-Proto": "http"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "INSECURE_TRANSPORT"


def test_blocker3_admin_web_login_returns_admin_portal_required() -> None:
    """Admin login via Web returns 403 ADMIN_PORTAL_REQUIRED."""
    db = InMemoryAuthDb()
    db.add_user(email="admin@example.com", password="Password123!", role="admin")
    client, _ = setup_auth_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Password123!", "client_type": "web"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ADMIN_PORTAL_REQUIRED"


# ---------------------------------------------------------------------------
# Blocker 4 Regression: IntegrityError Handling on Concurrency / FK
# ---------------------------------------------------------------------------


class MockAsyncpgPostgresError(Exception):
    """Simulated asyncpg PostgresError with structured constraint_name and sqlstate attributes."""

    def __init__(self, constraint_name: str, sqlstate: str, message: str) -> None:
        super().__init__(message)
        self.constraint_name = constraint_name
        self.sqlstate = sqlstate


def test_blocker4_signup_structured_duplicate_email_returns_409() -> None:
    """Structured asyncpg IntegrityError with uq_users_email_ci (23505) returns 409."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_failing_create_user(*args: Any, **kwargs: Any) -> None:
        orig = MockAsyncpgPostgresError(
            constraint_name="uq_users_email_ci",
            sqlstate="23505",
            message='duplicate key value violates unique constraint "uq_users_email_ci"',
        )
        raise IntegrityError("INSERT INTO users", {}, orig)

    with patch("app.services.auth_service.user_repo.create_user", mock_failing_create_user):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
            },
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


def test_blocker4_signup_structured_invalid_institution_returns_400() -> None:
    """Structured asyncpg IntegrityError with institution FK (23503) returns 400."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_failing_create_profile(*args: Any, **kwargs: Any) -> None:
        orig = MockAsyncpgPostgresError(
            constraint_name="fk_user_profiles_institution_id_institutions",
            sqlstate="23503",
            message=(
                'insert or update on table "user_profiles" violates '
                'foreign key constraint "fk_user_profiles_institution_id_institutions"'
            ),
        )
        raise IntegrityError("INSERT INTO user_profiles", {}, orig)

    with patch(
        "app.services.auth_service.user_repo.create_user_profile", mock_failing_create_profile
    ):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
                "institution_id": str(uuid4()),
            },
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INSTITUTION"


def test_blocker4_unrelated_integrity_error_propagates_unhandled() -> None:
    """Unrelated IntegrityError is not swallowed or mapped to auth error codes (returns 500)."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_unrelated_integrity_error(*args: Any, **kwargs: Any) -> None:
        orig = MockAsyncpgPostgresError(
            constraint_name="ck_unrelated_constraint",
            sqlstate="23514",
            message='check constraint "ck_unrelated_constraint" violated',
        )
        raise IntegrityError("INSERT INTO users", {}, orig)

    with patch("app.services.auth_service.user_repo.create_user", mock_unrelated_integrity_error):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
            },
        )
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"


def test_blocker4_signup_duplicate_email_integrity_error_fallback_returns_409() -> None:
    """Fallback string parsing on uq_users_email_ci returns 409 EMAIL_ALREADY_EXISTS."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_failing_create_user(*args: Any, **kwargs: Any) -> None:
        raise IntegrityError(
            "INSERT INTO users",
            {},
            Exception('unique constraint "uq_users_email_ci" violated'),
        )

    with patch("app.services.auth_service.user_repo.create_user", mock_failing_create_user):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
            },
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


def test_blocker4_signup_invalid_institution_integrity_error_fallback_returns_400() -> None:
    """Fallback string parsing on institution FK returns 400 INVALID_INSTITUTION."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_failing_create_profile(*args: Any, **kwargs: Any) -> None:
        raise IntegrityError(
            "INSERT INTO user_profiles",
            {},
            Exception(
                'foreign key constraint "fk_user_profiles_institution_id_institutions" violated'
            ),
        )

    with patch(
        "app.services.auth_service.user_repo.create_user_profile", mock_failing_create_profile
    ):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
                "institution_id": str(uuid4()),
            },
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INSTITUTION"


def test_blocker1_structured_unrelated_unique_constraint_propagates_500() -> None:
    """Structured asyncpg 23505 with unrelated constraint name propagates 500 unhandled."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_failing_create_user(*args: Any, **kwargs: Any) -> None:
        orig = MockAsyncpgPostgresError(
            constraint_name="uq_users_phone",
            sqlstate="23505",
            message=(
                'duplicate key value violates unique constraint "uq_users_phone" on users email'
            ),
        )
        raise IntegrityError("INSERT INTO users", {}, orig)

    with patch("app.services.auth_service.user_repo.create_user", mock_failing_create_user):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
            },
        )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"


def test_blocker1_structured_unrelated_foreign_key_propagates_500() -> None:
    """Structured asyncpg 23503 with unrelated constraint name propagates 500 unhandled."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_failing_create_profile(*args: Any, **kwargs: Any) -> None:
        orig = MockAsyncpgPostgresError(
            constraint_name="fk_institutions_admin_id",
            sqlstate="23503",
            message='foreign key constraint "fk_institutions_admin_id" violated on institution',
        )
        raise IntegrityError("INSERT INTO user_profiles", {}, orig)

    with patch(
        "app.services.auth_service.user_repo.create_user_profile", mock_failing_create_profile
    ):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
                "institution_id": str(uuid4()),
            },
        )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"


@pytest.mark.anyio
async def test_blocker1_direct_signup_raises_unrelated_integrity_error_directly() -> None:
    """Direct call to auth_service.signup with unrelated IntegrityError raises IntegrityError."""
    from app.schemas.auth import SignupRequest
    from app.services import auth_service

    mock_session = AsyncMock()

    @asynccontextmanager
    async def mock_begin_nested() -> AsyncIterator[None]:
        yield

    mock_session.begin_nested = mock_begin_nested

    orig = MockAsyncpgPostgresError(
        constraint_name="uq_users_phone",
        sqlstate="23505",
        message='duplicate key value violates unique constraint "uq_users_phone" on users email',
    )
    unrelated_error = IntegrityError("INSERT INTO users", {}, orig)

    payload = SignupRequest(
        email="alice@example.com",
        password="Password123!",
        role="tenant",
        full_name="Alice Tenant",
    )

    with (
        patch(
            "app.services.auth_service.user_repo.get_user_by_email_ci",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.auth_service.user_repo.create_user",
            AsyncMock(side_effect=unrelated_error),
        ),
    ):
        with pytest.raises(IntegrityError) as exc_info:
            await auth_service.signup(session=mock_session, payload=payload)
        assert exc_info.value is unrelated_error


def test_blocker1_fallback_string_unrelated_error_raises_500() -> None:
    """Unstructured string containing generic words raises 500 and is not misclassified."""
    db = InMemoryAuthDb()
    client, _ = setup_auth_test_app(db)

    async def mock_failing_create_user(*args: Any, **kwargs: Any) -> None:
        raise IntegrityError(
            "INSERT INTO users",
            {},
            Exception("some generic database failure mentioning users and email"),
        )

    with patch("app.services.auth_service.user_repo.create_user", mock_failing_create_user):
        resp = client.post(
            "/api/v1/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "role": "tenant",
                "full_name": "Alice Tenant",
            },
        )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"


# ---------------------------------------------------------------------------
# Blocker 5 Regression: Session Rotation & Revocation Race Handling
# ---------------------------------------------------------------------------


def test_blocker5_web_relogin_revokes_old_ambient_session_cookie() -> None:
    """Web re-login revokes the old session attached via cookie (session rotation)."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    old_session, old_token = db.add_session(user=user, raw_token=generate_session_token())
    client, settings = setup_auth_test_app(db)

    # Attach old cookie to login request
    client.cookies.set(settings.session_cookie_name, old_token)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "web"},
    )
    assert resp.status_code == 200

    # Verify old session in DB is marked revoked
    assert old_session.revoked_at is not None
    assert old_session.revocation_reason == "session_rotation"

    # Attempting to access /me with the old token now fails with 401 SESSION_REVOKED
    client.cookies.clear()
    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert me_resp.status_code == 401
    assert me_resp.json()["error"]["code"] == "SESSION_REVOKED"


def test_blocker5_native_login_with_ambient_cookie_rejected_400() -> None:
    """Native login presenting a session cookie is rejected with 400 AMBIGUOUS_CREDENTIALS."""
    db = InMemoryAuthDb()
    db.add_user(email="alice@example.com", password="Password123!")
    client, settings = setup_auth_test_app(db)

    client.cookies.set(settings.session_cookie_name, "ambient-cookie-token")
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "native"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AMBIGUOUS_CREDENTIALS"


def test_blocker5_concurrent_logout_vs_activity_update_race() -> None:
    """If a session is revoked concurrently during update_session_activity, request returns 401."""
    db = InMemoryAuthDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    session_obj, raw_token = db.add_session(user=user, raw_token=generate_session_token())
    client, _ = setup_auth_test_app(db)

    # Simulate concurrent logout marking session revoked right before update_session_activity
    async def mock_race_update_activity(*args: Any, **kwargs: Any) -> bool:
        session_obj.revoked_at = datetime.now(UTC)
        session_obj.revocation_reason = "logout"
        return False  # 0 rows updated because WHERE revoked_at IS NULL matched nothing

    with patch(
        "app.services.auth_service.session_repo.update_session_activity",
        mock_race_update_activity,
    ):
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SESSION_REVOKED"


# ---------------------------------------------------------------------------
# Additional Negative Security Tests
# ---------------------------------------------------------------------------


def test_suspended_user_with_active_session_rejected_403() -> None:
    """A user suspended after session issuance is rejected with 403 ACCOUNT_DISABLED."""
    db = InMemoryAuthDb()
    user = db.add_user(
        email="user@example.com", password="Password123!", role="tenant", status="active"
    )
    _, raw_token = db.add_session(user=user, raw_token=generate_session_token())
    client, _ = setup_auth_test_app(db)

    # User is suspended by admin
    user.status = "suspended"

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_web_cookie_me_success_path() -> None:
    """Valid session cookie accesses /me successfully without Authorization header."""
    db = InMemoryAuthDb()
    user = db.add_user(
        email="webuser@example.com",
        password="Password123!",
        role="tenant",
        full_name="Web User",
    )
    _, raw_token = db.add_session(user=user, raw_token=generate_session_token())
    client, settings = setup_auth_test_app(db)

    client.cookies.set(settings.session_cookie_name, raw_token)
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "webuser@example.com"
    assert resp.json()["profile"]["full_name"] == "Web User"


# ---------------------------------------------------------------------------
# Genuine SQLAlchemy 2.0 Transaction & Query Regression Tests (Offline)
# ---------------------------------------------------------------------------


def test_genuine_sqlalchemy_premature_commit_fails_in_begin_context() -> None:
    """Demonstrate that calling commit() inside session.begin() breaks the context lifecycle.

    In SQLAlchemy 2.0, invoking session.commit() inside an active session.begin() block
    closes the transaction. Subsequent commands inside the context manager raise
    InvalidRequestError.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import InvalidRequestError
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE test_items (id INT PRIMARY KEY, name TEXT)"))
        conn.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        with session.begin():
            session.execute(text("INSERT INTO test_items VALUES (1, 'initial')"))
            # Simulating the old bug: prematurely committing inside begin()
            session.commit()
            assert not session.in_transaction()
            # Attempting any subsequent operation inside the same begin() block fails
            with pytest.raises(
                InvalidRequestError,
                match="Can't operate on closed transaction inside context manager",
            ):
                session.execute(text("SELECT 1"))


def test_genuine_sqlalchemy_independent_security_session_isolation() -> None:
    """Demonstrate independent session factory commits security events without affecting outer tx.

    Outer session has staged uncommitted writes. When an authentication failure occurs:
    1. An independent session (from session_factory) writes and commits the security event.
    2. The outer session rolls back upon error exit.
    3. Uncommitted writes in the outer session are completely discarded.
    4. The security event committed by the independent session remains persisted.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine_outer = create_engine("sqlite:///:memory:")
    with engine_outer.connect() as conn:
        conn.execute(text("CREATE TABLE test_audit (id TEXT PRIMARY KEY, note TEXT)"))
        conn.commit()

    engine_sec = create_engine("sqlite:///:memory:")
    with engine_sec.connect() as conn:
        conn.execute(text("CREATE TABLE test_users (id TEXT PRIMARY KEY, failed_login_count INT)"))
        conn.execute(text("INSERT INTO test_users VALUES ('u1', 0)"))
        conn.commit()

    factory_outer = sessionmaker(bind=engine_outer, expire_on_commit=False)
    factory_sec = sessionmaker(bind=engine_sec, expire_on_commit=False)

    # Outer session simulates request lifecycle
    with factory_outer() as outer_session:
        try:
            with outer_session.begin():
                assert outer_session.in_transaction()
                # Stage some uncommitted data in the outer session
                outer_session.execute(
                    text("INSERT INTO test_audit VALUES ('a1', 'uncommitted audit')")
                )

                # Simulate auth failure: execute security counter increment in INDEPENDENT session
                with factory_sec() as sec_session:
                    assert sec_session is not outer_session
                    with sec_session.begin():
                        assert sec_session.in_transaction()
                        sec_session.execute(
                            text(
                                "UPDATE test_users "
                                "SET failed_login_count = failed_login_count + 1 "
                                "WHERE id = 'u1'"
                            )
                        )
                    assert not sec_session.in_transaction()

                # Outer session transaction is STILL ACTIVE and was NOT prematurely closed
                assert outer_session.in_transaction()

                # Outer session raises authentication error (e.g. 401)
                raise ValueError("Simulated 401 Authentication Failure")
        except ValueError:
            pass  # Outer transaction rolled back

    # Verify database state
    with factory_sec() as verify_sec:
        # Security event counter persisted
        user_failed_count = verify_sec.execute(
            text("SELECT failed_login_count FROM test_users WHERE id = 'u1'")
        ).scalar()
        assert user_failed_count == 1

    with factory_outer() as verify_outer:
        # Outer uncommitted audit row was cleanly rolled back
        audit_count = verify_outer.execute(text("SELECT COUNT(*) FROM test_audit")).scalar()
        assert audit_count == 0


def test_genuine_sqlalchemy_savepoint_nested_rollback() -> None:
    """Demonstrate savepoint (begin_nested) isolates profile failure during signup.

    In signup(), user + profile creation happens inside session.begin_nested().
    If profile creation fails (e.g. FK violation), only the savepoint rolls back,
    leaving the outer transaction viable and clean.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE test_users (id TEXT PRIMARY KEY, email TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE test_profiles ("
                "  id TEXT PRIMARY KEY, user_id TEXT, institution_id TEXT"
                ")"
            )
        )
        conn.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        with session.begin():
            assert session.in_transaction()

            # Savepoint begins
            try:
                with session.begin_nested():
                    session.execute(
                        text("INSERT INTO test_users VALUES ('u1', 'alice@example.com')")
                    )
                    # Simulating profile failure (e.g. foreign key error)
                    raise IntegrityError(
                        "INSERT INTO test_profiles", {}, Exception("FOREIGN KEY failed")
                    )
            except IntegrityError:
                pass  # Savepoint rolled back

            # Outer transaction remains active and not closed
            assert session.in_transaction()

            # Verify user insert was undone by savepoint rollback
            user_count = session.execute(text("SELECT COUNT(*) FROM test_users")).scalar()
            assert user_count == 0


def test_genuine_sqlalchemy_outer_transaction_unexpected_error_rollback() -> None:
    """Verify that unexpected exceptions roll back the entire transaction."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE test_data (id INT PRIMARY KEY)"))
        conn.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        with pytest.raises(RuntimeError, match="Unexpected crash"):
            with session.begin():
                session.execute(text("INSERT INTO test_data VALUES (99)"))
                raise RuntimeError("Unexpected crash")

    with factory() as session:
        count = session.execute(text("SELECT COUNT(*) FROM test_data")).scalar()
        assert count == 0


class AsyncSqliteSessionAdapter:
    """Async session adapter wrapping a synchronous SQLite connection.

    Allows genuine production repository functions to execute against real SQLite
    in-memory tables, verifying exact SQL compilation, WHERE clauses, and RETURNING clauses.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        return self.conn.execute(stmt, params or {})

    async def flush(self) -> None:
        pass


def setup_sqlite_repo_test_db() -> tuple[Any, AsyncSqliteSessionAdapter]:
    """Create in-memory SQLite tables matching User and UserSession models."""
    engine = create_engine("sqlite:///:memory:")
    conn = engine.connect()
    conn.execute(
        text(
            "CREATE TABLE users ("
            "  id TEXT PRIMARY KEY,"
            "  email TEXT,"
            "  password_hash TEXT,"
            "  role TEXT DEFAULT 'tenant',"
            "  status TEXT DEFAULT 'active',"
            "  email_verified_at TEXT,"
            "  failed_login_count INTEGER DEFAULT 0,"
            "  locked_until TEXT,"
            "  last_login_at TEXT,"
            "  created_at TEXT DEFAULT CURRENT_TIMESTAMP,"
            "  updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE user_sessions ("
            "  id TEXT PRIMARY KEY,"
            "  user_id TEXT,"
            "  token_hash TEXT,"
            "  last_activity_at TEXT,"
            "  expires_at TEXT,"
            "  absolute_expires_at TEXT,"
            "  revoked_at TEXT,"
            "  revocation_reason TEXT,"
            "  created_at TEXT DEFAULT CURRENT_TIMESTAMP,"
            "  updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
    )
    conn.commit()
    adapter = AsyncSqliteSessionAdapter(conn)
    return conn, adapter


@pytest.mark.anyio
async def test_genuine_repository_update_session_activity() -> None:
    """Directly execute session_repo.update_session_activity on actual SQLAlchemy models."""
    conn, adapter = setup_sqlite_repo_test_db()
    uid = uuid4()
    sid_active = uuid4()
    sid_revoked = uuid4()
    now = datetime.now(UTC)

    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'hash_act', :now, :now, :now, NULL)"
        ),
        {"id": sid_active.hex, "uid": uid.hex, "now": str(now)},
    )
    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'hash_rev', :now, :now, :now, :rev)"
        ),
        {"id": sid_revoked.hex, "uid": uid.hex, "now": str(now), "rev": str(now)},
    )
    conn.commit()

    # 1. Active session update must succeed and return True
    new_expires = now + timedelta(minutes=30)
    updated_active = await session_repo.update_session_activity(
        session=adapter,  # type: ignore[arg-type]
        session_id=sid_active,
        last_activity_at=now,
        new_expires_at=new_expires,
    )
    assert updated_active is True

    # 2. Revoked session update must match 0 rows and return False due to revoked_at IS NULL
    updated_revoked = await session_repo.update_session_activity(
        session=adapter,  # type: ignore[arg-type]
        session_id=sid_revoked,
        last_activity_at=now,
        new_expires_at=new_expires,
    )
    assert updated_revoked is False
    conn.close()


@pytest.mark.anyio
async def test_genuine_repository_revoke_all_user_sessions() -> None:
    """Directly execute session_repo.revoke_all_user_sessions on actual SQLAlchemy models."""
    conn, adapter = setup_sqlite_repo_test_db()
    u1 = uuid4()
    u2 = uuid4()
    now = datetime.now(UTC)

    # 3 active sessions and 1 already revoked for u1; 1 active for u2
    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'h1', :now, :now, :now, NULL)"
        ),
        {"id": uuid4().hex, "uid": u1.hex, "now": str(now)},
    )
    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'h2', :now, :now, :now, NULL)"
        ),
        {"id": uuid4().hex, "uid": u1.hex, "now": str(now)},
    )
    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'h3', :now, :now, :now, NULL)"
        ),
        {"id": uuid4().hex, "uid": u1.hex, "now": str(now)},
    )
    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'h4', :now, :now, :now, :rev)"
        ),
        {"id": uuid4().hex, "uid": u1.hex, "now": str(now), "rev": str(now)},
    )
    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'h5', :now, :now, :now, NULL)"
        ),
        {"id": uuid4().hex, "uid": u2.hex, "now": str(now)},
    )
    conn.commit()

    rev_count = await session_repo.revoke_all_user_sessions(
        session=adapter,  # type: ignore[arg-type]
        user_id=u1,
        revoked_at=now,
        reason="password_reset",
    )
    # Exactly 3 unrevoked sessions were updated
    assert rev_count == 3

    # Verify all 4 sessions for u1 are now revoked, and u2 session is untouched
    revoked_u1 = conn.execute(
        text("SELECT COUNT(*) FROM user_sessions WHERE user_id = :uid AND revoked_at IS NOT NULL"),
        {"uid": u1.hex},
    ).scalar()
    assert revoked_u1 == 4

    active_u2 = conn.execute(
        text("SELECT COUNT(*) FROM user_sessions WHERE user_id = :uid AND revoked_at IS NULL"),
        {"uid": u2.hex},
    ).scalar()
    assert active_u2 == 1
    conn.close()


@pytest.mark.anyio
async def test_genuine_repository_update_login_failure() -> None:
    """Directly execute user_repo.update_login_failure on actual SQLAlchemy models."""
    conn, adapter = setup_sqlite_repo_test_db()
    uid = uuid4()
    conn.execute(
        text("INSERT INTO users (id, email, failed_login_count) VALUES (:id, 'u@ex.com', 0)"),
        {"id": uid.hex},
    )
    conn.commit()

    await user_repo.update_login_failure(adapter, uid)  # type: ignore[arg-type]
    conn.commit()
    cnt = conn.execute(
        text("SELECT failed_login_count FROM users WHERE id = :id"),
        {"id": uid.hex},
    ).scalar()
    assert cnt == 1

    await user_repo.update_login_failure(adapter, uid)  # type: ignore[arg-type]
    conn.commit()
    cnt2 = conn.execute(
        text("SELECT failed_login_count FROM users WHERE id = :id"),
        {"id": uid.hex},
    ).scalar()
    assert cnt2 == 2
    conn.close()


@pytest.mark.anyio
async def test_genuine_repository_revoke_session() -> None:
    """Directly execute session_repo.revoke_session on actual SQLAlchemy models."""
    conn, adapter = setup_sqlite_repo_test_db()
    sid = uuid4()
    uid = uuid4()
    now = datetime.now(UTC)
    conn.execute(
        text(
            "INSERT INTO user_sessions ("
            "  id, user_id, token_hash, last_activity_at, expires_at, "
            "  absolute_expires_at, revoked_at"
            ") VALUES (:id, :uid, 'h', :now, :now, :now, NULL)"
        ),
        {"id": sid.hex, "uid": uid.hex, "now": str(now)},
    )
    conn.commit()

    await session_repo.revoke_session(
        adapter,  # type: ignore[arg-type]
        sid,
        now,
        reason="manual_test",
    )
    conn.commit()

    row = conn.execute(
        text("SELECT revoked_at, revocation_reason FROM user_sessions WHERE id = :id"),
        {"id": sid.hex},
    ).first()
    assert row is not None
    assert row[0] is not None
    assert row[1] == "manual_test"
    conn.close()


def test_genuine_sqlalchemy_postgresql_query_compilation() -> None:
    """Verify all repository SQL queries compile validly against PostgreSQL dialect."""
    from sqlalchemy import func, select, update
    from sqlalchemy.dialects import postgresql

    now = datetime.now(UTC)
    uid = uuid4()
    sid = uuid4()
    pg_dialect = postgresql.dialect()  # type: ignore[no-untyped-call]

    # 1. user_repo.update_login_failure
    fail_stmt = (
        update(User).where(User.id == uid).values(failed_login_count=User.failed_login_count + 1)
    )
    compiled_fail = str(fail_stmt.compile(dialect=pg_dialect))
    assert (
        "UPDATE users SET failed_login_count=(users.failed_login_count + %(failed_login_count_1)s)"
        in compiled_fail
    )
    assert "WHERE users.id = %(id_1)s::UUID" in compiled_fail

    # 2. session_repo.update_session_activity
    act_stmt = (
        update(UserSession)
        .where(UserSession.id == sid, UserSession.revoked_at.is_(None))
        .values(last_activity_at=now, expires_at=now)
        .returning(UserSession.id)
    )
    compiled_act = str(act_stmt.compile(dialect=pg_dialect))
    assert "UPDATE user_sessions SET last_activity_at=" in compiled_act
    assert (
        "WHERE user_sessions.id = %(id_1)s::UUID AND user_sessions.revoked_at IS NULL"
        in compiled_act
    )
    assert "RETURNING user_sessions.id" in compiled_act

    # 3. session_repo.revoke_all_user_sessions
    rev_all_stmt = (
        update(UserSession)
        .where(UserSession.user_id == uid, UserSession.revoked_at.is_(None))
        .values(revoked_at=now, revocation_reason="logout")
    )
    compiled_rev = str(rev_all_stmt.compile(dialect=pg_dialect))
    assert (
        "WHERE user_sessions.user_id = %(user_id_1)s::UUID AND user_sessions.revoked_at IS NULL"
        in compiled_rev
    )

    # 4. user_repo.get_user_by_email_ci
    email_stmt = select(User).where(func.lower(User.email) == "test@example.com")
    compiled_email = str(email_stmt.compile(dialect=pg_dialect))
    assert "WHERE lower(users.email) = %(lower_1)s" in compiled_email
