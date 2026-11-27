"""Regression tests for T12-FIX-01: Session Identity Fail-Closed Remediation.

Verifies that PostgreSQL set_config() failures can NEVER be silently suppressed,
while legitimate SQLite and offline unit test environments remain supported.
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import (
    set_session_identity,
    system_transaction,
    transaction,
)


@pytest.mark.anyio
async def test_set_session_identity_postgres_success() -> None:
    """Verify successful set_config execution on PostgreSQL dialect."""
    mock_session = AsyncMock(spec=AsyncSession)
    executed: list[tuple[str, dict[str, Any]]] = []

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> None:
        executed.append((str(stmt), params or {}))

    mock_session.execute = mock_execute
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    uid = uuid4()
    await set_session_identity(mock_session, role="tenant", user_id=uid)

    assert len(executed) == 1
    stmt, params = executed[0]
    assert "set_config('app.user_id', :user_id, true)" in stmt
    assert "set_config('app.role', :role, true)" in stmt
    assert params["role"] == "tenant"
    assert params["user_id"] == str(uid)


@pytest.mark.anyio
async def test_set_session_identity_postgres_generic_failure_propagates() -> None:
    """Verify that any database exception during set_config propagates immediately."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    mock_session.execute.side_effect = RuntimeError("connection terminated unexpectedly")

    with pytest.raises(RuntimeError, match="connection terminated unexpectedly"):
        await set_session_identity(mock_session, role="system")


@pytest.mark.anyio
async def test_set_session_identity_failure_mentioning_set_config_cannot_be_swallowed() -> None:
    """Verify that errors containing 'set_config' are NOT swallowed (T12-FIX-01 core defect)."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    mock_session.execute.side_effect = RuntimeError(
        "ERROR: permission denied for function set_config"
    )

    with pytest.raises(RuntimeError, match="permission denied for function set_config"):
        await set_session_identity(mock_session, role="guest")


@pytest.mark.anyio
async def test_set_session_identity_no_such_function_not_swallowed() -> None:
    """Verify that errors containing 'no such function' are NOT swallowed on PostgreSQL."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    mock_session.execute.side_effect = RuntimeError(
        "no such function: set_config (catalog corruption or missing extension)"
    )

    with pytest.raises(RuntimeError, match="no such function: set_config"):
        await set_session_identity(mock_session, role="system")


@pytest.mark.anyio
async def test_set_session_identity_sqlite_dialect_noop() -> None:
    """Verify that legitimate SQLite dialect cleanly no-ops without calling execute."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "sqlite"
    mock_session.bind = mock_bind

    await set_session_identity(mock_session, role="system")

    # execute must NOT be invoked on SQLite
    assert mock_session.execute.call_count == 0


@pytest.mark.anyio
async def test_set_session_identity_sqlite_adapter_conn_noop() -> None:
    """Verify that session adapters wrapping a connection (e.g. AsyncSqliteSessionAdapter) no-op."""
    mock_session = AsyncMock()
    del mock_session.bind
    del mock_session.get_bind
    del mock_session.sync_session

    mock_conn = MagicMock()
    mock_conn.dialect.name = "sqlite"
    mock_session.conn = mock_conn

    await set_session_identity(mock_session, role="system")

    assert mock_session.execute.call_count == 0


@pytest.mark.anyio
async def test_failed_identity_setup_prevents_protected_operations_in_transaction() -> None:
    """Verify that a set_config failure aborts the transaction before any query executes."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    mock_session.execute.side_effect = RuntimeError("FATAL: set_config failed")

    @asynccontextmanager
    async def mock_begin() -> Any:
        yield

    mock_session.begin = mock_begin

    @asynccontextmanager
    async def mock_factory() -> Any:
        yield mock_session

    protected_operation_executed = False

    with pytest.raises(RuntimeError, match="FATAL: set_config failed"):
        async with transaction(mock_factory, role="tenant", user_id=uuid4()):  # type: ignore[arg-type]
            protected_operation_executed = True

    assert not protected_operation_executed, (
        "CRITICAL: Protected operation executed despite failed identity setup!"
    )


@pytest.mark.anyio
async def test_failed_identity_setup_prevents_protected_operations_in_system_transaction() -> None:
    """Verify that system_transaction aborts before body execution if set_config fails."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    mock_session.execute.side_effect = RuntimeError("DB error during set_config")

    @asynccontextmanager
    async def mock_begin() -> Any:
        yield

    mock_session.begin = mock_begin

    @asynccontextmanager
    async def mock_factory() -> Any:
        yield mock_session

    system_operation_executed = False

    with pytest.raises(RuntimeError, match="DB error during set_config"):
        async with system_transaction(mock_factory):  # type: ignore[arg-type]
            system_operation_executed = True

    assert not system_operation_executed, (
        "CRITICAL: System operation executed despite failed identity setup!"
    )


@pytest.mark.anyio
async def test_role_and_user_validation_preserved() -> None:
    """Verify existing role and user_id validation remains fully active."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.bind = mock_bind

    # Invalid role must raise ValueError
    with pytest.raises(ValueError, match="Invalid role"):
        await set_session_identity(mock_session, role="invalid_role")  # type: ignore[arg-type]

    # Authenticated role without user_id must raise ValueError
    with pytest.raises(ValueError, match="Authenticated roles require a user ID"):
        await set_session_identity(mock_session, role="tenant", user_id=None)

    with pytest.raises(ValueError, match="Authenticated roles require a user ID"):
        await set_session_identity(mock_session, role="admin", user_id=None)

    with pytest.raises(ValueError, match="Authenticated roles require a user ID"):
        await set_session_identity(mock_session, role="owner", user_id=None)

    # Guest, system, and worker without user_id are allowed
    mock_session.execute.reset_mock()
    await set_session_identity(mock_session, role="guest")
    assert mock_session.execute.call_count == 1

    mock_session.execute.reset_mock()
    await set_session_identity(mock_session, role="system")
    assert mock_session.execute.call_count == 1

    mock_session.execute.reset_mock()
    await set_session_identity(mock_session, role="worker")
    assert mock_session.execute.call_count == 1
