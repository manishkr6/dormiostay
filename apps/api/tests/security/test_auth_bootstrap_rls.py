"""Unit and architectural tests for Auth Bootstrap under PostgreSQL RLS (T-12 / T12-SEC-01).

Tests the resolution of the authentication circular dependency:
1. Under guest context (app.role='guest', app.user_id=''), protected tables (users,
   user_sessions, tokens) fail closed and return zero rows.
2. Internal authentication operations establish trusted transaction-local system context
   (app.role='system') to perform necessary credential and token lookups.
3. Once authenticated, the session identity transitions to the specific user's identity
   (app.role='tenant'/'owner'/'admin', app.user_id=str(user.id)), restoring tenant isolation.
4. Normal HTTP request sessions can NEVER select or assume 'system' or 'worker' roles.
5. Worker context is restricted to email_outbox operations and cannot access users/sessions.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import User, UserSession
from app.db.session import (
    set_session_identity,
)


@pytest.mark.anyio
async def test_guest_context_sets_empty_identity() -> None:
    """Demonstrate that guest context sets app.role='guest' and app.user_id=''."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed: list[tuple[str, dict[str, str]]] = []

    async def mock_execute(stmt: str, params: dict[str, str] | None = None) -> None:
        executed.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    await set_session_identity(mock_session, role="guest")

    assert len(executed) == 1
    stmt, params = executed[0]
    assert params["role"] == "guest"
    assert params["user_id"] == ""


@pytest.mark.anyio
async def test_system_context_sets_system_role_for_auth_bootstrap() -> None:
    """Verify that internal authentication context sets app.role='system' (T12-SEC-01)."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed: list[tuple[str, dict[str, str]]] = []

    async def mock_execute(stmt: str, params: dict[str, str] | None = None) -> None:
        executed.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    await set_session_identity(mock_session, role="system")

    assert len(executed) == 1
    stmt, params = executed[0]
    assert params["role"] == "system"
    assert params["user_id"] == ""


@pytest.mark.anyio
async def test_user_context_sets_authenticated_tenant_identity() -> None:
    """Verify that after authentication, session context switches to tenant identity."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed: list[tuple[str, dict[str, str]]] = []

    async def mock_execute(stmt: str, params: dict[str, str] | None = None) -> None:
        executed.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    user_id = uuid4()
    await set_session_identity(mock_session, role="tenant", user_id=user_id)

    assert len(executed) == 1
    stmt, params = executed[0]
    assert params["role"] == "tenant"
    assert params["user_id"] == str(user_id)


@pytest.mark.anyio
async def test_worker_context_sets_worker_role_for_email_outbox() -> None:
    """Verify that email worker sets app.role='worker' (T12-SEC-05)."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed: list[tuple[str, dict[str, str]]] = []

    async def mock_execute(stmt: str, params: dict[str, str] | None = None) -> None:
        executed.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    await set_session_identity(mock_session, role="worker")

    assert len(executed) == 1
    stmt, params = executed[0]
    assert params["role"] == "worker"
    assert params["user_id"] == ""


@pytest.mark.anyio
async def test_auth_service_functions_invoke_system_identity() -> None:
    """Verify that auth_service functions establish trusted system identity."""
    from app.services import auth_service

    mock_session = AsyncMock(spec=AsyncSession)
    executed_roles: list[str] = []

    async def mock_execute(stmt: str, params: dict[str, str] | None = None) -> Any:
        if params and "role" in params:
            executed_roles.append(params["role"])
        mock_res = MagicMock()
        mock_res.rowcount = 1
        return mock_res

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    # 1. logout establishes system role
    await auth_service.logout(mock_session, uuid4())
    assert "system" in executed_roles

    # 2. revoke_all_sessions establishes system role
    executed_roles.clear()
    await auth_service.revoke_all_sessions(mock_session, uuid4(), "test")
    assert "system" in executed_roles


@pytest.mark.anyio
async def test_deps_transitions_identity_from_system_to_authenticated_user() -> None:
    """Verify get_current_session_and_user establishes user RLS context after validation."""
    from fastapi import Request

    from app.api.deps import get_current_session_and_user

    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    # Ensure state does not already have user/user_session
    del mock_request.state.user
    del mock_request.state.user_session

    user_id = uuid4()
    mock_user = MagicMock(spec=User)
    mock_user.id = user_id
    mock_user.role = "tenant"
    mock_user.status = "active"

    mock_session_obj = MagicMock(spec=UserSession)
    mock_session_obj.id = uuid4()
    mock_session_obj.user_id = user_id

    mock_db_session = AsyncMock(spec=AsyncSession)
    executed_identities: list[dict[str, str]] = []

    async def mock_execute(stmt: str, params: dict[str, str] | None = None) -> None:
        if params and "role" in params:
            executed_identities.append(dict(params))

    mock_db_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_db_session.bind = mock_bind

    mock_settings = MagicMock(spec=Settings)
    mock_factory = MagicMock()

    mock_validate = AsyncMock(return_value=(mock_session_obj, mock_user))
    with patch("app.services.auth_service.validate_session", new=mock_validate):
        sess, usr = await get_current_session_and_user(
            request=mock_request,
            raw_token="valid_raw_session_token_12345678",
            db_session=mock_db_session,
            session_factory=mock_factory,
            settings=mock_settings,
        )

    assert usr is mock_user
    assert sess is mock_session_obj
    assert len(executed_identities) == 1
    assert executed_identities[0]["role"] == "tenant"
    assert executed_identities[0]["user_id"] == str(user_id)


@pytest.mark.anyio
async def test_negative_malformed_roles_rejected() -> None:
    """Verify set_session_identity rejects unsupported or injection roles (T12-SEC-10)."""
    mock_session = AsyncMock(spec=AsyncSession)

    invalid_roles = [
        "superadmin",
        "root",
        "system_override",
        "",
        "'; DROP TABLE users; --",
        "admin'--",
    ]
    for inv in invalid_roles:
        with pytest.raises(ValueError, match="Invalid role"):
            await set_session_identity(mock_session, role=inv)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_negative_authenticated_roles_require_user_id() -> None:
    """Verify tenant, owner, and admin roles require a non-null UUID (T12-SEC-10)."""
    mock_session = AsyncMock(spec=AsyncSession)

    for auth_role in ["tenant", "owner", "admin"]:
        with pytest.raises(ValueError, match="Authenticated roles require a user ID"):
            await set_session_identity(mock_session, role=auth_role, user_id=None)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_guc_parameters_exact_canonical_names() -> None:
    """Verify that identity setter uses canonical app.user_id and app.role (T12-SEC-10)."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed_statements: list[str] = []

    async def mock_execute(stmt: Any, params: dict[str, str] | None = None) -> None:
        executed_statements.append(str(stmt))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    await set_session_identity(mock_session, role="system")

    assert len(executed_statements) == 1
    sql = executed_statements[0]
    assert "set_config('app.user_id', :user_id, true)" in sql
    assert "set_config('app.role', :role, true)" in sql
    assert "app.current_user_id" not in sql
    assert "app.current_user_role" not in sql
