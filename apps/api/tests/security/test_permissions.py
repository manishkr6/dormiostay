"""Unit and integration tests for FastAPI authorization dependencies (T-12).

Tests coverage:
- require_user: Authenticated active access, guest rejection (401),
  suspended/banned rejection (403).
- require_verified: Verified user access, unverified rejection (403 EMAIL_NOT_VERIFIED).
- require_role: Role enforcement, spoofed client headers/claims rejection,
  privilege escalation prevention.
- require_approved_owner: Role check, pending/draft/submitted/rejected host applications
  rejection (403), approved owner pass, suspended owner rejection.
- require_admin: Administrator check, non-admin rejection (403),
  future T-13 MFA boundary preservation (403 MFA_REQUIRED).
- Transport compatibility: Web HttpOnly cookie vs Native Mobile Bearer header,
  mixed credentials rejection (400).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI, Request, status
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl

from app.api.deps import (
    extract_raw_session_token,
    get_current_session_and_user,
)
from app.core.config import Settings, get_settings
from app.core.errors import AppError, register_exception_handlers
from app.core.permissions import (
    require_admin,
    require_approved_owner,
    require_role,
    require_user,
    require_verified,
)
from app.core.security import hash_session_token
from app.db.models import User, UserSession
from app.db.session import get_session

# ---------------------------------------------------------------------------
# Test Fixtures and Helpers
# ---------------------------------------------------------------------------


def create_test_settings() -> Settings:
    """Create test settings with auth enabled and test database."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://test:test@localhost:5432/neondb",
        frontend_origin=AnyHttpUrl("http://localhost:5173"),
        session_cookie_name="dormio_session",
        session_idle_timeout_minutes=30,
        session_absolute_timeout_days=14,
        admin_session_idle_timeout_minutes=15,
        admin_session_absolute_timeout_hours=8,
        auth_endpoints_enabled=True,
        csrf_protection_enabled=False,
        rate_limit_enabled=False,
    )


class MockDatabase:
    """In-memory mock store for users, sessions, and host applications."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.sessions: dict[str, tuple[UserSession, User]] = {}  # token_hash -> (session, user)
        self.host_applications: dict[UUID, str] = {}  # user_id -> status


def create_test_app(db: MockDatabase) -> FastAPI:
    """Create a FastAPI application with test endpoints protected by T-12 permissions."""
    app = FastAPI()
    register_exception_handlers(app)
    settings = create_test_settings()
    app.dependency_overrides[get_settings] = lambda: settings

    # Wire get_current_session_and_user to MockDatabase
    async def mock_get_current_session_and_user(
        request: Request,
        raw_token: Annotated[str, Depends(extract_raw_session_token)],
    ) -> tuple[UserSession, User]:
        token_hash = hash_session_token(raw_token)
        if token_hash not in db.sessions:
            raise AppError(
                code="SESSION_INVALID",
                message="Session is invalid or expired.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        user_session, user = db.sessions[token_hash]
        if user_session.revoked_at is not None:
            raise AppError(
                code="SESSION_REVOKED",
                message="Session has been revoked.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        # Propagate identity onto request state
        request.state.user_id = user.id
        request.state.role = user.role
        request.state.user = user
        request.state.user_session = user_session
        return user_session, user

    app.dependency_overrides[get_current_session_and_user] = mock_get_current_session_and_user

    # Wire mock db_session for is_owner_approved query
    class MockAsyncSession:
        async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
            stmt_str = str(stmt)
            if "host_applications" in stmt_str and params and "user_id" in params:
                uid = params["user_id"]
                app_status = db.host_applications.get(uid)
                mock_res = type("MockResult", (), {})()
                mock_res.scalar_one_or_none = lambda: 1 if app_status == "approved" else None
                return mock_res
            mock_res = type("MockResult", (), {})()
            mock_res.scalar_one_or_none = lambda: None
            return mock_res

    async def mock_get_session() -> AsyncIterator[MockAsyncSession]:
        yield MockAsyncSession()

    app.dependency_overrides[get_session] = mock_get_session

    # Test routes protected by T-12 dependencies
    @app.get("/api/v1/test/user-only")
    async def user_only_route(user: Annotated[User, Depends(require_user)]) -> dict[str, Any]:
        return {"user_id": str(user.id), "role": user.role, "status": user.status}

    @app.get("/api/v1/test/verified-only")
    async def verified_only_route(
        user: Annotated[User, Depends(require_verified)],
    ) -> dict[str, Any]:
        return {"user_id": str(user.id), "verified": user.email_verified_at is not None}

    @app.get("/api/v1/test/tenant-only")
    async def tenant_only_route(
        user: Annotated[User, Depends(require_role("tenant"))],
    ) -> dict[str, Any]:
        return {"user_id": str(user.id), "role": user.role}

    @app.get("/api/v1/test/owner-or-admin")
    async def owner_or_admin_route(
        user: Annotated[User, Depends(require_role("owner", "admin"))],
    ) -> dict[str, Any]:
        return {"user_id": str(user.id), "role": user.role}

    @app.get("/api/v1/test/approved-owner-only")
    async def approved_owner_route(
        user: Annotated[User, Depends(require_approved_owner)],
    ) -> dict[str, Any]:
        return {"user_id": str(user.id), "approved_owner": True}

    @app.get("/api/v1/test/admin-only")
    async def admin_only_route(user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        return {"user_id": str(user.id), "role": user.role}

    return app


def register_mock_user(
    db: MockDatabase,
    role: str = "tenant",
    status: str = "active",
    email_verified: bool = True,
    owner_app_status: str | None = None,
) -> tuple[User, str]:
    """Helper to create a user with a valid active session, returning (User, raw_token)."""
    user_id = uuid4()
    now = datetime.now(UTC)
    user = User(
        id=user_id,
        email=f"{role}_{user_id.hex[:6]}@example.com",
        password_hash="argon2id$mock",
        role=role,
        status=status,
        email_verified_at=now if email_verified else None,
        failed_login_count=0,
    )
    db.users[user_id] = user

    raw_token = f"tok_{uuid4().hex}{uuid4().hex}"
    token_hash = hash_session_token(raw_token)
    session = UserSession(
        id=uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        expires_at=now + timedelta(days=1),
        absolute_expires_at=now + timedelta(days=14),
        revoked_at=None,
    )
    db.sessions[token_hash] = (session, user)

    if owner_app_status is not None:
        db.host_applications[user_id] = owner_app_status

    return user, raw_token


# ---------------------------------------------------------------------------
# 1. require_user Tests
# ---------------------------------------------------------------------------


def test_require_user_unauthenticated_rejected() -> None:
    """Guest / missing credentials return HTTP 401 UNAUTHORIZED."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    res = client.get("/api/v1/test/user-only")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_require_user_active_tenant_passes() -> None:
    """Active authenticated tenant passes require_user via Web cookie."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="tenant", status="active")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/user-only")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["user_id"] == str(user.id)
    assert res.json()["role"] == "tenant"


def test_require_user_active_owner_bearer_passes() -> None:
    """Active authenticated owner passes require_user via Native Mobile Bearer header."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="owner", status="active")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/api/v1/test/user-only", headers=headers)
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["user_id"] == str(user.id)


def test_require_user_suspended_rejected() -> None:
    """Suspended user is rejected with HTTP 403 ACCOUNT_DISABLED."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="tenant", status="suspended")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/user-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "ACCOUNT_DISABLED"
    assert "suspended" in res.json()["error"]["message"]


def test_require_user_banned_rejected() -> None:
    """Banned user is rejected with HTTP 403 ACCOUNT_DISABLED."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="tenant", status="banned")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/user-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "ACCOUNT_DISABLED"
    assert "banned" in res.json()["error"]["message"]


def test_require_user_mixed_credentials_rejected() -> None:
    """Ambiguous transport (both cookie and Authorization header) returns HTTP 400."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="tenant", status="active")
    client.cookies.set("dormio_session", token)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/api/v1/test/user-only", headers=headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert res.json()["error"]["code"] == "AMBIGUOUS_CREDENTIALS"


# ---------------------------------------------------------------------------
# 2. require_verified Tests
# ---------------------------------------------------------------------------


def test_require_verified_verified_user_passes() -> None:
    """Verified user passes require_verified."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="tenant", email_verified=True)
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/verified-only")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["verified"] is True


def test_require_verified_unverified_user_rejected() -> None:
    """Unverified user is rejected with HTTP 403 EMAIL_NOT_VERIFIED."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="tenant", email_verified=False)
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/verified-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


# ---------------------------------------------------------------------------
# 3. require_role Tests
# ---------------------------------------------------------------------------


def test_require_role_matching_role_passes() -> None:
    """Tenant passes require_role('tenant')."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="tenant")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/tenant-only")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["role"] == "tenant"


def test_require_role_wrong_role_rejected() -> None:
    """Owner attempting tenant-only route receives HTTP 403 FORBIDDEN_ROLE."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    user, token = register_mock_user(db, role="owner")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/tenant-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_require_role_multiple_allowed_roles() -> None:
    """Route allowing 'owner' or 'admin' accepts both and rejects tenant."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    # 1. Owner passes
    _, owner_tok = register_mock_user(db, role="owner")
    client.cookies.set("dormio_session", owner_tok)
    res_owner = client.get("/api/v1/test/owner-or-admin")
    assert res_owner.status_code == status.HTTP_200_OK

    # 2. Admin passes
    _, admin_tok = register_mock_user(db, role="admin")
    client.cookies.set("dormio_session", admin_tok)
    res_admin = client.get("/api/v1/test/owner-or-admin")
    assert res_admin.status_code == status.HTTP_200_OK

    # 3. Tenant rejected
    _, tenant_tok = register_mock_user(db, role="tenant")
    client.cookies.set("dormio_session", tenant_tok)
    res_tenant = client.get("/api/v1/test/owner-or-admin")
    assert res_tenant.status_code == status.HTTP_403_FORBIDDEN
    assert res_tenant.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_require_role_ignores_client_supplied_headers_and_body() -> None:
    """Tenant cannot elevate to admin via X-Role, Role headers or request parameters."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="tenant")
    client.cookies.set("dormio_session", token)

    headers = {
        "X-Role": "admin",
        "Role": "admin",
        "X-User-Role": "admin",
        "X-Forwarded-Role": "admin",
    }
    res = client.get("/api/v1/test/admin-only", headers=headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "FORBIDDEN_ROLE"


# ---------------------------------------------------------------------------
# 4. require_approved_owner Tests
# ---------------------------------------------------------------------------


def test_require_approved_owner_tenant_rejected() -> None:
    """Tenant calling approved-owner route receives HTTP 403 FORBIDDEN_ROLE."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="tenant")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/approved-owner-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_require_approved_owner_no_application_rejected() -> None:
    """Owner with no host_application receives HTTP 403 OWNER_NOT_APPROVED."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="owner", owner_app_status=None)
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/approved-owner-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "OWNER_NOT_APPROVED"


@pytest.mark.parametrize("status_val", ["draft", "submitted", "needs_info", "rejected", "approved"])
def test_require_approved_owner_fails_closed_pending_t16(status_val: str) -> None:
    """Owner access fails closed pending T-16 host application onboarding schema.

    Invariants (T12-SEC-02):
    - host_applications table is scheduled for T-16 and does not exist in 0001-0003.
    - Owner actions fail closed with HTTP 403 OWNER_NOT_APPROVED.
    - No undefined-table SQL is executed against the database.
    - Owners cannot self-approve.
    """
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="owner", owner_app_status=status_val)
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/approved-owner-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "OWNER_NOT_APPROVED"


@pytest.mark.anyio
async def test_is_owner_approved_executes_no_sql_and_fails_closed() -> None:
    """Verify is_owner_approved returns False without executing SQL against non-existent table."""
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.permissions import is_owner_approved

    mock_session = AsyncMock(spec=AsyncSession)
    res = await is_owner_approved(mock_session, uuid4())
    assert res is False
    # Strict invariant: zero SQL statements executed on the database session
    assert mock_session.execute.call_count == 0


def test_require_approved_owner_cannot_self_approve_via_headers() -> None:
    """Owner cannot bypass approval via spoofed headers or request parameters."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="owner")
    client.cookies.set("dormio_session", token)
    headers = {
        "X-Host-Approved": "true",
        "X-Owner-Status": "approved",
        "X-Application-Status": "approved",
    }

    res = client.get("/api/v1/test/approved-owner-only", headers=headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "OWNER_NOT_APPROVED"


def test_require_approved_owner_suspended_owner_rejected() -> None:
    """Suspended owner is rejected with HTTP 403 ACCOUNT_DISABLED."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="owner", status="suspended")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/approved-owner-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "ACCOUNT_DISABLED"


# ---------------------------------------------------------------------------
# 5. require_admin Tests
# ---------------------------------------------------------------------------


def test_require_admin_tenant_rejected() -> None:
    """Tenant is rejected from admin route with HTTP 403 FORBIDDEN_ROLE."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="tenant")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/admin-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_require_admin_owner_rejected() -> None:
    """Owner is rejected from admin route with HTTP 403 FORBIDDEN_ROLE."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    _, token = register_mock_user(db, role="owner")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/admin-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_require_admin_missing_mfa_fails_closed() -> None:
    """Admin with missing/None MFA state is rejected with HTTP 403 MFA_REQUIRED (T12-SEC-03)."""
    db = MockDatabase()
    app = create_test_app(db)
    client = TestClient(app)

    # By default, mfa_completed is not set on request.state -> must fail closed
    user, token = register_mock_user(db, role="admin")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/admin-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "MFA_REQUIRED"


def test_require_admin_explicit_false_mfa_fails_closed() -> None:
    """Administrator with explicit False MFA state is rejected with HTTP 403 MFA_REQUIRED."""
    db = MockDatabase()
    app = create_test_app(db)

    @app.middleware("http")
    async def inject_false_mfa(request: Request, call_next: Any) -> Any:
        request.state.mfa_completed = False
        return await call_next(request)

    client = TestClient(app)
    _, token = register_mock_user(db, role="admin")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/admin-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "MFA_REQUIRED"


def test_require_admin_untrusted_string_mfa_fails_closed() -> None:
    """Administrator with string 'true' MFA state is rejected (must be boolean True)."""
    db = MockDatabase()
    app = create_test_app(db)

    @app.middleware("http")
    async def inject_string_mfa(request: Request, call_next: Any) -> Any:
        request.state.mfa_completed = "true"  # Untrusted string injection
        return await call_next(request)

    client = TestClient(app)
    _, token = register_mock_user(db, role="admin")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/admin-only")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["code"] == "MFA_REQUIRED"


def test_require_admin_verified_true_mfa_passes() -> None:
    """Administrator with verified boolean True MFA state passes require_admin."""
    db = MockDatabase()
    app = create_test_app(db)

    @app.middleware("http")
    async def inject_verified_mfa(request: Request, call_next: Any) -> Any:
        request.state.mfa_completed = True
        return await call_next(request)

    client = TestClient(app)
    _, token = register_mock_user(db, role="admin")
    client.cookies.set("dormio_session", token)

    res = client.get("/api/v1/test/admin-only")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["role"] == "admin"
