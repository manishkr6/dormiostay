"""Unit tests for Feature Ticket T-07: CSRF and Origin Protection.

Tests all 20 required security scenarios:
1. Valid Web CSRF token and correct Origin.
2. Missing CSRF token.
3. Incorrect CSRF token.
4. Expired CSRF token.
5. Foreign or null Origin.
6. Anonymous Web CSRF bootstrap.
7. Token binding after login.
8. Session rotation and token invalidation.
9. Logout and revoked session.
10. Valid native bearer write without CSRF.
11. Invalid or expired native bearer.
12. Mixed cookie and bearer.
13. Spoofed native client marker.
14. Native pre-auth request with browser Origin.
15. Native pre-auth request with invalid content type.
16. GET requests remaining unaffected.
17. Browser fetch credential behavior and CSRF header integration.
18. No credential or CSRF-token leakage in logs or errors.
19. Regression coverage for existing T-06 authentication with CSRF enabled.
20. Activation guard remaining disabled by default.
"""

import time
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl

from app.core.config import Settings, get_settings
from app.core.csrf import (
    generate_session_csrf_token,
    get_csrf_signing_key,
    verify_anonymous_csrf_token,
    verify_session_csrf_token,
)
from app.core.security import generate_session_token, hash_password, hash_session_token
from app.db.models import User, UserProfile, UserSession
from app.db.session import get_session, get_session_factory
from app.main import create_app

TEST_FRONTEND_ORIGIN = "http://localhost:5173"

_ACTIVE_MONKEYPATCHES: list[pytest.MonkeyPatch] = []


@pytest.fixture(autouse=True)
def cleanup_csrf_monkeypatches() -> Generator[None, None, None]:
    """Ensure all monkeypatches in T-07 are undone after each test for strict isolation."""
    yield
    while _ACTIVE_MONKEYPATCHES:
        mp = _ACTIVE_MONKEYPATCHES.pop()
        mp.undo()


class InMemoryCsrfDb:
    """In-memory data store for CSRF and authentication tests."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.profiles: dict[UUID, UserProfile] = {}
        self.sessions: dict[UUID, UserSession] = {}
        self.sessions_by_hash: dict[str, UserSession] = {}

    def add_user(
        self,
        email: str = "alice@example.com",
        password: str = "Password123!",
        role: str = "tenant",
        status: str = "active",
        full_name: str = "Alice Test",
    ) -> User:
        user_id = uuid4()
        now = datetime.now(UTC)
        user = User(
            id=user_id,
            email=email.strip().lower(),
            password_hash=hash_password(password),
            role=role,
            status=status,
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )
        profile = UserProfile(
            id=uuid4(),
            user_id=user_id,
            full_name=full_name,
            preferred_areas=[],
            created_at=now,
            updated_at=now,
        )
        self.users[user_id] = user
        self.profiles[user_id] = profile
        return user

    def add_session(
        self,
        user: User,
        raw_token: str | None = None,
        idle_minutes: int = 30,
        abs_days: int = 14,
        revoked: bool = False,
    ) -> tuple[UserSession, str]:
        token = raw_token or generate_session_token()
        token_hash = hash_session_token(token)
        now = datetime.now(UTC)
        session_id = uuid4()
        user_session = UserSession(
            id=session_id,
            user_id=user.id,
            token_hash=token_hash,
            last_activity_at=now,
            expires_at=now + timedelta(minutes=idle_minutes),
            absolute_expires_at=now + timedelta(days=abs_days),
            revoked_at=now if revoked else None,
            revocation_reason="revoked" if revoked else None,
            created_at=now,
            updated_at=now,
        )
        self.sessions[session_id] = user_session
        self.sessions_by_hash[token_hash] = user_session
        return user_session, token


def create_csrf_test_settings(
    auth_enabled: bool = True,
    csrf_enabled: bool = True,
    frontend_origin: str = TEST_FRONTEND_ORIGIN,
) -> Settings:
    """Create settings fixture with CSRF enabled by default."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl(frontend_origin),
        auth_endpoints_enabled=auth_enabled,
        csrf_protection_enabled=csrf_enabled,
        csrf_secret_key="test-csrf-secret-key-for-unit-testing",
        csrf_token_expire_seconds=3600,
        session_cookie_name="dormiostay_session",
        session_idle_timeout_minutes=30,
        session_absolute_timeout_days=14,
        admin_session_idle_timeout_minutes=15,
        admin_session_absolute_timeout_hours=8,
    )


def setup_csrf_test_app(
    db: InMemoryCsrfDb, settings: Settings | None = None
) -> tuple[TestClient, Settings]:
    """Build FastAPI test app with mocked session repositories."""
    resolved_settings = settings or create_csrf_test_settings()
    app = create_app(resolved_settings)

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()

    @asynccontextmanager
    async def mock_begin_nested() -> AsyncIterator[None]:
        yield

    mock_db_session.begin_nested = mock_begin_nested

    # Independent security transaction mock
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
            institution_id=kwargs.get("institution_id"),
            preferred_areas=[],
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

    import app.db.repositories.session_repo as session_repo_mod
    import app.db.repositories.user_repo as user_repo_mod

    monkeypatch = pytest.MonkeyPatch()
    _ACTIVE_MONKEYPATCHES.append(monkeypatch)
    monkeypatch.setattr(user_repo_mod, "get_user_by_email_ci", mock_get_user_by_email_ci)
    monkeypatch.setattr(user_repo_mod, "get_user_with_profile", mock_get_user_with_profile)
    monkeypatch.setattr(user_repo_mod, "create_user", mock_create_user)
    monkeypatch.setattr(user_repo_mod, "create_user_profile", mock_create_user_profile)
    monkeypatch.setattr(user_repo_mod, "update_login_success", mock_update_login_success)
    monkeypatch.setattr(session_repo_mod, "create_session", mock_create_session)
    monkeypatch.setattr(
        session_repo_mod,
        "get_session_with_user_by_token_hash",
        mock_get_session_with_user_by_token_hash,
    )
    monkeypatch.setattr(session_repo_mod, "update_session_activity", mock_update_session_activity)
    monkeypatch.setattr(session_repo_mod, "revoke_session", mock_revoke_session)

    client = TestClient(app, raise_server_exceptions=False)
    return client, resolved_settings


# ---------------------------------------------------------------------------
# Test Scenarios 1 to 20
# ---------------------------------------------------------------------------


def test_01_valid_web_csrf_token_and_correct_origin() -> None:
    """1. Valid Web CSRF token and correct Origin allows state-changing requests."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    sess, raw_token = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    # Web write (logout) with valid session-bound CSRF token and valid Origin
    key = get_csrf_signing_key(settings)
    valid_csrf = generate_session_csrf_token(sess.token_hash, key)

    client.cookies.set(settings.session_cookie_name, raw_token)
    resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": valid_csrf},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert sess.revoked_at is not None


def test_02_missing_csrf_token() -> None:
    """2. Missing CSRF token on state-changing Web request returns 403 CSRF_TOKEN_MISSING."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    _, raw_token = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    client.cookies.set(settings.session_cookie_name, raw_token)
    resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN},  # No X-CSRF-Token
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CSRF_TOKEN_MISSING"


def test_03_incorrect_csrf_token() -> None:
    """3. Incorrect/tampered CSRF token returns 403 CSRF_TOKEN_INVALID."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    _, raw_token = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    client.cookies.set(settings.session_cookie_name, raw_token)
    current_ts = int(time.time())
    resp = client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": TEST_FRONTEND_ORIGIN,
            "X-CSRF-Token": f"sess.{current_ts}.6789.badbadbadbadbadbadbadbad",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CSRF_TOKEN_INVALID"


def test_04_expired_csrf_token() -> None:
    """4. Expired CSRF token returns 403 CSRF_TOKEN_EXPIRED."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    sess, raw_token = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    key = get_csrf_signing_key(settings)
    # Generate token with timestamp 2 hours in the past
    past_time = int(time.time()) - 7200
    import hashlib
    import hmac

    msg = f"sess:{sess.token_hash}:{past_time}:nonce123".encode()
    sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    expired_token = f"sess.{past_time}.nonce123.{sig}"

    client.cookies.set(settings.session_cookie_name, raw_token)
    resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": expired_token},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CSRF_TOKEN_EXPIRED"


def test_05_foreign_or_null_origin() -> None:
    """5. Foreign, null, or missing Origin returns 403 FORBIDDEN_ORIGIN."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    sess, raw_token = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)
    key = get_csrf_signing_key(settings)
    valid_csrf = generate_session_csrf_token(sess.token_hash, key)
    client.cookies.set(settings.session_cookie_name, raw_token)

    # 1. Foreign Origin
    resp_foreign = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://attacker.evil.com", "X-CSRF-Token": valid_csrf},
    )
    assert resp_foreign.status_code == 403
    assert resp_foreign.json()["error"]["code"] == "FORBIDDEN_ORIGIN"

    # 2. null Origin (sandboxed iframe / data URI)
    resp_null = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "null", "X-CSRF-Token": valid_csrf},
    )
    assert resp_null.status_code == 403
    assert resp_null.json()["error"]["code"] == "FORBIDDEN_ORIGIN"

    # 3. Missing Origin header
    resp_missing = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": valid_csrf},
    )
    assert resp_missing.status_code == 403
    assert resp_missing.json()["error"]["code"] == "FORBIDDEN_ORIGIN"


def test_06_anonymous_web_csrf_bootstrap() -> None:
    """6. Anonymous Web CSRF bootstrap endpoint provides valid short-lived anonymous token."""
    db = InMemoryCsrfDb()
    client, settings = setup_csrf_test_app(db)

    resp = client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert token.startswith("anon.")

    key = get_csrf_signing_key(settings)
    is_valid, err = verify_anonymous_csrf_token(token, key)
    assert is_valid is True
    assert err == ""

    # Anonymous token cannot be verified as session token
    sess_valid, _ = verify_session_csrf_token(token, "fake_hash", key)
    assert sess_valid is False


def test_07_token_binding_after_login() -> None:
    """7. Anonymous bootstrap token works for pre-auth login, then session-bound token is issued."""
    db = InMemoryCsrfDb()
    db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    client, settings = setup_csrf_test_app(db)

    # 1. Fetch anonymous bootstrap token
    bootstrap_resp = client.get("/api/v1/auth/csrf")
    assert bootstrap_resp.status_code == 200
    anon_token = bootstrap_resp.json()["token"]

    # 2. Web login using anonymous token and correct Origin
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "web"},
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": anon_token},
    )
    assert login_resp.status_code == 200
    session_cookie = login_resp.cookies.get(settings.session_cookie_name)
    assert session_cookie is not None

    # 3. Post-login: fetch fresh CSRF token carrying the session cookie
    client.cookies.set(settings.session_cookie_name, session_cookie)
    csrf_resp = client.get("/api/v1/auth/csrf")
    assert csrf_resp.status_code == 200
    sess_csrf_token = csrf_resp.json()["token"]
    assert sess_csrf_token.startswith("sess.")

    # 4. Attempting to use the pre-login anonymous token on post-login write fails 403
    logout_anon_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": anon_token},
    )
    assert logout_anon_resp.status_code == 403
    assert logout_anon_resp.json()["error"]["code"] == "CSRF_TOKEN_INVALID"

    # 5. Using the session-bound token succeeds
    logout_sess_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": sess_csrf_token},
    )
    assert logout_sess_resp.status_code == 200
    assert logout_sess_resp.json() == {"status": "ok"}


def test_08_session_rotation_and_token_invalidation() -> None:
    """8. Session rotation invalidates previous session's CSRF token."""
    db = InMemoryCsrfDb()
    user = db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    sess1, token1 = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    # Obtain CSRF token bound to session 1
    key = get_csrf_signing_key(settings)
    sess1_csrf = generate_session_csrf_token(sess1.token_hash, key)

    # User re-logs in with ambient cookie (session rotation)
    client.cookies.set(settings.session_cookie_name, token1)
    relogin_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "web"},
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": sess1_csrf},
    )
    assert relogin_resp.status_code == 200
    token2 = relogin_resp.cookies.get(settings.session_cookie_name)
    assert token2 != token1

    # Attempting to call /logout with token2 cookie but old sess1_csrf fails with 403
    client.cookies.set(settings.session_cookie_name, token2)
    old_csrf_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": sess1_csrf},
    )
    assert old_csrf_resp.status_code == 403
    assert old_csrf_resp.json()["error"]["code"] == "CSRF_TOKEN_INVALID"


def test_09_logout_and_revoked_session() -> None:
    """9. Revoked session cannot perform state-changing operations regardless of CSRF token."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    sess, token = db.add_session(user=user, revoked=True)
    client, settings = setup_csrf_test_app(db)

    key = get_csrf_signing_key(settings)
    csrf = generate_session_csrf_token(sess.token_hash, key)

    client.cookies.set(settings.session_cookie_name, token)
    resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SESSION_REVOKED"


def test_10_valid_native_bearer_write_without_csrf() -> None:
    """10. Valid native Bearer request succeeds without X-CSRF-Token or Origin."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    sess, token = db.add_session(user=user)
    client, _ = setup_csrf_test_app(db)

    resp = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert sess.revoked_at is not None


def test_11_invalid_or_expired_native_bearer() -> None:
    """11. Invalid or expired native Bearer request returns 401, never exempt."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    _, active_token = db.add_session(user=user)
    client, _ = setup_csrf_test_app(db)

    # 1. Nonexistent bearer
    resp_invalid = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": "Bearer non-existent-token"},
    )
    assert resp_invalid.status_code == 401
    assert resp_invalid.json()["error"]["code"] == "SESSION_INVALID"

    # 2. Expired bearer (absolute timeout)
    sess_exp, exp_token = db.add_session(user=user, abs_days=-1)
    resp_exp = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {exp_token}"},
    )
    assert resp_exp.status_code == 401
    assert resp_exp.json()["error"]["code"] == "SESSION_EXPIRED"


def test_12_mixed_cookie_and_bearer() -> None:
    """12. Mixed cookie and Authorization header returns 400 AMBIGUOUS_CREDENTIALS."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    _, token1 = db.add_session(user=user)
    _, token2 = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    client.cookies.set(settings.session_cookie_name, token1)
    resp = client.post(
        "/api/v1/auth/logout",
        headers={
            "Authorization": f"Bearer {token2}",
            "Origin": TEST_FRONTEND_ORIGIN,
            "X-CSRF-Token": "some-token",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AMBIGUOUS_CREDENTIALS"


def test_13_spoofed_native_client_marker() -> None:
    """13. Spoofed X-Client-Type marker alone does not bypass CSRF or Origin checks."""
    db = InMemoryCsrfDb()
    user = db.add_user()
    _, raw_token = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    # Attacker sends cookie + X-Client-Type: native without bearer
    client.cookies.set(settings.session_cookie_name, raw_token)
    resp = client.post(
        "/api/v1/auth/logout",
        headers={"X-Client-Type": "native"},  # No Origin, No CSRF, No Bearer
    )
    # Native client with ambient cookie is rejected with 400
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AMBIGUOUS_CREDENTIALS"


def test_14_native_preauth_request_with_browser_origin() -> None:
    """14. Native pre-auth request carrying browser Origin is rejected with 403."""
    db = InMemoryCsrfDb()
    db.add_user(email="alice@example.com", password="Password123!")
    client, _ = setup_csrf_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "native"},
        headers={
            "X-Client-Type": "native",
            "Origin": "http://localhost:5173",  # Browser Origin prohibited on native
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "NATIVE_ORIGIN_PROHIBITED"


def test_15_native_preauth_request_with_invalid_content_type() -> None:
    """15. Native pre-auth request with non-JSON Content-Type returns 415."""
    db = InMemoryCsrfDb()
    client, _ = setup_csrf_test_app(db)

    resp = client.post(
        "/api/v1/auth/login",
        content="email=alice%40example.com&password=Password123%21",
        headers={
            "X-Client-Type": "native",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "INVALID_CONTENT_TYPE"


def test_16_get_requests_remaining_unaffected() -> None:
    """16. GET requests remain safe and unaffected by CSRF requirements."""
    db = InMemoryCsrfDb()
    user = db.add_user(email="alice@example.com", full_name="Alice Test")
    _, raw_token = db.add_session(user=user)
    client, settings = setup_csrf_test_app(db)

    # 1. /auth/me with cookie and no Origin or CSRF header works
    client.cookies.set(settings.session_cookie_name, raw_token)
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "alice@example.com"

    # 2. /healthz works
    health_resp = client.get("/healthz")
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "ok"}


def test_17_browser_fetch_credential_and_csrf_integration() -> None:
    """17. End-to-end simulation of React apiClient fetch lifecycle with CSRF."""
    db = InMemoryCsrfDb()
    client, settings = setup_csrf_test_app(db)

    # Step 1: Pre-auth bootstrap
    csrf_init = client.get("/api/v1/auth/csrf")
    assert csrf_init.status_code == 200
    anon_csrf = csrf_init.json()["token"]

    # Step 2: Signup
    signup_resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "newuser@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "New User",
        },
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": anon_csrf},
    )
    assert signup_resp.status_code == 201

    # Step 3: Login
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "newuser@example.com", "password": "Password123!", "client_type": "web"},
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": anon_csrf},
    )
    assert login_resp.status_code == 200
    cookie_val = login_resp.cookies.get(settings.session_cookie_name)
    assert cookie_val is not None

    # Step 4: Post-login CSRF bootstrap for authenticated session
    client.cookies.set(settings.session_cookie_name, cookie_val)
    sess_csrf_resp = client.get("/api/v1/auth/csrf")
    sess_csrf = sess_csrf_resp.json()["token"]
    assert sess_csrf.startswith("sess.")

    # Step 5: Logout using session-bound CSRF token
    logout_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": TEST_FRONTEND_ORIGIN, "X-CSRF-Token": sess_csrf},
    )
    assert logout_resp.status_code == 200


def test_18_no_credential_or_token_leakage_in_errors() -> None:
    """18. CSRF error responses do not leak secrets, hashes, or internal tokens."""
    db = InMemoryCsrfDb()
    client, _ = setup_csrf_test_app(db)

    resp = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "leak@example.com",
            "password": "Password123!",
            "role": "tenant",
            "full_name": "Leak Test",
        },
        headers={"Origin": "https://attacker.evil.com", "X-CSRF-Token": "secret_token_123"},
    )
    assert resp.status_code == 403
    body = resp.json()
    err = body["error"]
    assert "secret_token_123" not in str(body)
    assert "secret_key" not in str(body)
    assert "token_hash" not in str(body)
    assert err["code"] == "FORBIDDEN_ORIGIN"
    assert "request_id" in err


def test_19_regression_coverage_existing_t06_with_csrf() -> None:
    """19. Standard T-06 authentication operations function with CSRF enabled."""
    db = InMemoryCsrfDb()
    db.add_user(email="alice@example.com", password="Password123!", role="tenant")
    client, settings = setup_csrf_test_app(db)

    # 1. Native mobile login with X-Client-Type and no Origin
    native_login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Password123!", "client_type": "native"},
        headers={"X-Client-Type": "native"},
    )
    assert native_login_resp.status_code == 200
    native_data = native_login_resp.json()
    assert "token" in native_data
    native_token = native_data["token"]

    # 2. Native mobile accesses /me
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {native_token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["user"]["email"] == "alice@example.com"

    # 3. Native mobile logs out without CSRF
    logout_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {native_token}"},
    )
    assert logout_resp.status_code == 200


def test_20_activation_guard_disabled_by_default() -> None:
    """20. Activation guard returns 503 on /csrf and auth endpoints when disabled."""
    db = InMemoryCsrfDb()
    # Create app with auth_endpoints_enabled=False (the production default)
    settings = create_csrf_test_settings(auth_enabled=False)
    client, _ = setup_csrf_test_app(db, settings=settings)

    # /auth/csrf returns 503
    resp_csrf = client.get("/api/v1/auth/csrf")
    assert resp_csrf.status_code == 503
    assert resp_csrf.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # /auth/login returns 503
    resp_login = client.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "Password123!", "client_type": "web"},
    )
    assert resp_login.status_code == 503

    # /healthz remains 200
    resp_health = client.get("/healthz")
    assert resp_health.status_code == 200


def test_21_csrf_secret_required_in_production_when_auth_enabled() -> None:
    """21. Setting auth_endpoints_enabled=True in staging/production without CSRF_SECRET fails."""
    with pytest.raises(ValueError, match="CSRF_SECRET"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
            frontend_origin=AnyHttpUrl("https://dormiostay.com"),
            auth_endpoints_enabled=True,
            csrf_secret_key="",
        )

    with pytest.raises(ValueError, match="CSRF_SECRET"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="staging",
            database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
            frontend_origin=AnyHttpUrl("https://staging.dormiostay.com"),
            auth_endpoints_enabled=True,
            csrf_secret_key="",
        )

    # When auth_endpoints_enabled=False (default), staging/production settings succeed
    valid_prod = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("https://dormiostay.com"),
        auth_endpoints_enabled=False,
        csrf_secret_key="",
    )
    assert valid_prod.auth_endpoints_enabled is False


def test_22_csrf_secret_alias_resolution() -> None:
    """22. Settings recognizes CSRF_SECRET environment variable alias."""
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("http://localhost:5173"),
        CSRF_SECRET="my-aliased-secret-key",
    )
    assert s.csrf_secret_key == "my-aliased-secret-key"


def test_23_csrf_signing_key_fails_closed_in_production() -> None:
    """23. get_csrf_signing_key fails closed if auth enabled in production without secret key."""
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("http://localhost:5173"),
        auth_endpoints_enabled=False,
        csrf_secret_key="",
    )
    # In development, falls back to salt:database_url
    dev_key = get_csrf_signing_key(s)
    assert len(dev_key) == 32

    # If mutated to production + auth_enabled without secret key, fails closed
    s.app_env = "production"
    s.auth_endpoints_enabled = True
    with pytest.raises(RuntimeError, match="CSRF_SECRET_KEY_REQUIRED_IN_PRODUCTION"):
        get_csrf_signing_key(s)
