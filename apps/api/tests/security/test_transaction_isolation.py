"""Unit tests for transaction-local RLS identity context and isolation (T-12).

Verifies:
1. set_config('app.user_id', ..., true) and set_config('app.role', ..., true) are transaction-local.
2. Authenticated roles ('tenant', 'owner', 'admin') require a non-null user_id.
3. Unauthenticated 'guest' and trusted 'system' roles permit None user_id.
4. Unsupported role strings raise ValueError.
5. Parameterized SQL execution avoids SQL injection in set_config calls.
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session, set_session_identity, system_transaction, transaction


@pytest.mark.anyio
async def test_transaction_sets_local_identity() -> None:
    """Verify transaction executes set_config with user_id and role as local transaction GUCs."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed_statements: list[tuple[Any, dict[str, Any]]] = []

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> None:
        executed_statements.append((str(stmt), params or {}))

    mock_session.execute = mock_execute

    @asynccontextmanager
    async def mock_begin() -> Any:
        yield

    mock_session.begin = mock_begin

    @asynccontextmanager
    async def mock_factory() -> Any:
        yield mock_session

    user_id = uuid4()
    async with transaction(mock_factory, user_id=user_id, role="tenant") as session:  # type: ignore[arg-type]
        assert session is mock_session

    assert len(executed_statements) == 1
    stmt_str, params = executed_statements[0]
    assert "set_config('app.user_id', :user_id, true)" in stmt_str
    assert "set_config('app.role', :role, true)" in stmt_str
    assert params["user_id"] == str(user_id)
    assert params["role"] == "tenant"


@pytest.mark.anyio
async def test_transaction_guest_role_empty_user_id() -> None:
    """Verify guest role sets empty string for user_id and 'guest' for role."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed_statements: list[tuple[Any, dict[str, Any]]] = []

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> None:
        executed_statements.append((str(stmt), params or {}))

    mock_session.execute = mock_execute

    @asynccontextmanager
    async def mock_begin() -> Any:
        yield

    mock_session.begin = mock_begin

    @asynccontextmanager
    async def mock_factory() -> Any:
        yield mock_session

    async with transaction(mock_factory, user_id=None, role="guest"):  # type: ignore[arg-type]
        pass

    assert len(executed_statements) == 1
    _, params = executed_statements[0]
    assert params["user_id"] == ""
    assert params["role"] == "guest"


@pytest.mark.anyio
async def test_transaction_system_role_empty_user_id() -> None:
    """Verify system role sets empty string for user_id and 'system' for role."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed_statements: list[tuple[Any, dict[str, Any]]] = []

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> None:
        executed_statements.append((str(stmt), params or {}))

    mock_session.execute = mock_execute

    @asynccontextmanager
    async def mock_begin() -> Any:
        yield

    mock_session.begin = mock_begin

    @asynccontextmanager
    async def mock_factory() -> Any:
        yield mock_session

    async with transaction(mock_factory, user_id=None, role="system"):  # type: ignore[arg-type]
        pass

    assert len(executed_statements) == 1
    _, params = executed_statements[0]
    assert params["user_id"] == ""
    assert params["role"] == "system"


@pytest.mark.anyio
@pytest.mark.parametrize("auth_role", ["tenant", "owner", "admin"])
async def test_transaction_authenticated_role_requires_user_id(auth_role: Any) -> None:
    """Verify authenticated roles raise ValueError when user_id is None."""
    mock_factory = MagicMock()

    with pytest.raises(ValueError, match="Authenticated roles require a user ID"):
        async with transaction(mock_factory, user_id=None, role=auth_role):
            pass


@pytest.mark.anyio
async def test_transaction_invalid_role_raises_value_error() -> None:
    """Verify invalid or unrecognised roles raise ValueError."""
    mock_factory = MagicMock()

    with pytest.raises(ValueError, match="Authenticated roles require a user ID"):
        async with transaction(mock_factory, user_id=uuid4(), role="superuser"):  # type: ignore[arg-type]
            pass


@pytest.mark.anyio
async def test_set_session_identity_worker_role() -> None:
    """Verify worker role sets empty string for user_id and 'worker' for role (T12-SEC-05)."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed_statements: list[tuple[Any, dict[str, Any]]] = []

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> None:
        executed_statements.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    # Simulate postgresql dialect
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    await set_session_identity(mock_session, role="worker")

    assert len(executed_statements) == 1
    _, params = executed_statements[0]
    assert params["user_id"] == ""
    assert params["role"] == "worker"


@pytest.mark.anyio
async def test_system_transaction_sets_system_role() -> None:
    """Verify system_transaction scopes identity to role='system' and user_id=''."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed_statements: list[tuple[Any, dict[str, Any]]] = []

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> None:
        executed_statements.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    @asynccontextmanager
    async def mock_begin() -> Any:
        yield

    mock_session.begin = mock_begin

    @asynccontextmanager
    async def mock_factory() -> Any:
        yield mock_session

    async with system_transaction(mock_factory) as session:  # type: ignore[arg-type]
        assert session is mock_session

    assert len(executed_statements) == 1
    _, params = executed_statements[0]
    assert params["user_id"] == ""
    assert params["role"] == "system"


@pytest.mark.anyio
async def test_set_session_identity_non_postgres_dialect_noop() -> None:
    """Verify set_session_identity gracefully no-ops on SQLite/non-PostgreSQL dialect."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "sqlite"
    mock_session.bind = mock_bind

    await set_session_identity(mock_session, role="system")
    assert mock_session.execute.call_count == 0


@pytest.mark.anyio
async def test_get_session_rejects_privileged_roles_from_request_state() -> None:
    """Verify get_session defaults to 'guest' if request.state has 'system' or 'worker'."""
    from fastapi import Request

    mock_request = MagicMock(spec=Request)
    mock_request.state.role = "system"  # Privileged claim injection attempt
    mock_request.state.user_id = uuid4()
    mock_request.app.state.database_engine = MagicMock()

    mock_session = AsyncMock(spec=AsyncSession)
    executed_statements: list[tuple[Any, dict[str, Any]]] = []

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> None:
        executed_statements.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    @asynccontextmanager
    async def mock_begin() -> Any:
        yield

    mock_session.begin = mock_begin

    # Mock verify_runtime_role to avoid real query
    from unittest.mock import patch

    with patch("app.db.session.verify_runtime_role", new=AsyncMock()):
        with patch("app.db.session.async_sessionmaker") as mock_maker:

            @asynccontextmanager
            async def fake_session_context() -> Any:
                yield mock_session

            mock_maker.return_value = fake_session_context

            session_gen = get_session(mock_request)
            session = await anext(session_gen)
            assert session is mock_session

    # Invariant: Must have defaulted to guest with empty user_id, ignoring 'system'
    assert len(executed_statements) == 1
    _, params = executed_statements[0]
    assert params["role"] == "guest"
    assert params["user_id"] == ""
