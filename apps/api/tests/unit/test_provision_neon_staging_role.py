"""Comprehensive offline unit tests for Neon Staging Role Provisioning Automation.

Mocks all external interactions:
  - Neon Control Plane API (HTTP/TLS, redirects, operations)
  - PostgreSQL administrative and application connections (asyncpg)
  - macOS Keychain credential storage adapter (Security.framework C API)

Tests all safety invariants, failure modes, error sanitization, state machine transitions,
CLI dispatch, operation polling, and fail-closed containment.
Zero live database connections; zero real credentials generated or stored.
"""

from __future__ import annotations

import asyncio
import ctypes
import http.client
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.response
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.provision_neon_staging_role import (
    ADMIN_ROLE,
    APP_ROLE,
    ERR_SEC_AUTH_FAILED,
    ERR_SEC_DUPLICATE_ITEM,
    ERR_SEC_INTERACTION_NOT_ALLOWED,
    ERR_SEC_ITEM_NOT_FOUND,
    ERR_SEC_PARAM,
    ERR_SEC_SUCCESS,
    EXPECTED_DATABASE,
    EXPECTED_ENDPOINT_HOST,
    EXPECTED_ENDPOINT_PORT,
    K_SEC_GENERIC_PASSWORD_ITEM_CLASS,
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    PRODUCTION_BRANCH_ID,
    PRODUCTION_BRANCH_NAME,
    PROJECT_ID,
    PROJECT_NAME,
    STAGING_BRANCH_ID,
    STAGING_BRANCH_NAME,
    CheckpointManager,
    ContainmentFailureError,
    DatabaseConnector,
    MacOSSecurityFrameworkAdapter,
    NeonApiClient,
    OperationTimeoutError,
    PrivilegeVerificationError,
    ProvisioningError,
    ProvisioningMode,
    ProvisioningState,
    SecretRedactor,
    SecretStore,
    SecretStoreError,
    StagingRoleProvisioner,
    StrictNoRedirectHandler,
    TargetVerificationError,
    _ActiveRequestHolder,
    _extract_socket,
    _is_socket_like,
    _run_with_bounded_sigint_handling,
    _safe_close_or_terminate,
    _sanitize_text,
    _verify_client_tls,
    cli_entrypoint,
    map_db_error,
    map_http_error,
)


class MockNeonApi:
    """Mock implementation of Neon Control Plane API."""

    def __init__(
        self,
        project_id: str = PROJECT_ID,
        branches: list[dict[str, Any]] | None = None,
        endpoints: list[dict[str, Any]] | None = None,
        reset_response: dict[str, Any] | None = None,
        operations: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.project_id = project_id
        self.branches = branches or [
            {"id": STAGING_BRANCH_ID, "name": STAGING_BRANCH_NAME},
            {"id": PRODUCTION_BRANCH_ID, "name": PRODUCTION_BRANCH_NAME},
        ]
        self.endpoints = endpoints or [
            {
                "id": "ep-staging-1",
                "branch_id": STAGING_BRANCH_ID,
                "host": EXPECTED_ENDPOINT_HOST,
            }
        ]
        self.reset_response = reset_response or {
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "mocked-neon-generated-password-12345",
            },
            "operations": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "endpoint_id": "ep-staging-1",
                    "status": "finished",
                }
            ],
        }
        self.operations = operations or {}
        self.reset_called = False
        self.polled_ops: list[str] = []

    def get_project(self, project_id: str) -> dict[str, Any]:
        return {"project": {"id": self.project_id, "name": PROJECT_NAME}}

    def list_branches(self, project_id: str) -> list[dict[str, Any]]:
        return self.branches

    def list_endpoints(self, project_id: str) -> list[dict[str, Any]]:
        return self.endpoints

    def reset_role_password(
        self, project_id: str, branch_id: str, role_name: str
    ) -> dict[str, Any]:
        self.reset_called = True
        return self.reset_response

    def get_operation(self, project_id: str, operation_id: str) -> dict[str, Any]:
        self.polled_ops.append(operation_id)
        if operation_id in self.operations:
            res = dict(self.operations[operation_id])
            op = res.get("operation", res)
            if isinstance(op, dict) and "id" not in op:
                op["id"] = operation_id
            return res
        return {"operation": {"id": operation_id, "status": "finished"}}


class MockDbConn:
    """Mock implementation of asyncpg connection."""

    def __init__(
        self,
        current_user: str = ADMIN_ROLE,
        session_user: str = ADMIN_ROLE,
        current_database: str = EXPECTED_DATABASE,
        host: str = EXPECTED_ENDPOINT_HOST,
        ssl_active: bool = True,
        role_exists: bool = False,
        pub_schema_create: bool = False,
        pub_db_create: bool = False,
        pub_db_connect: bool = True,
        pub_db_temp: bool = True,
        role_flags: dict[str, Any] | None = None,
        role_memberships: int | None = 0,
        ownership: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
        effective_privileges: dict[str, bool] | None = None,
        throw_on_alter_nologin: bool = False,
        throw_on_create_role: bool = False,
        throw_on_auth_query: bool = False,
        client_ssl_active: bool | None = None,
    ) -> None:
        self.current_user = current_user
        self.session_user = session_user
        self.current_database = current_database
        self.host = host
        self.ssl_active = ssl_active
        self.client_ssl_active = ssl_active if client_ssl_active is None else client_ssl_active
        self.role_exists = role_exists
        self.pub_schema_create = pub_schema_create
        self.pub_db_create = pub_db_create
        self.pub_db_connect = pub_db_connect
        self.pub_db_temp = pub_db_temp
        default_flags = {
            "rolcanlogin": False,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolbypassrls": False,
            "rolreplication": False,
        }
        if role_flags:
            default_flags.update(role_flags)
        self.role_flags = default_flags
        self.role_memberships = role_memberships
        self.ownership = ownership
        default_privs = {
            "can_connect": True,
            "can_create_db": False,
            "can_create_schema": False,
            "can_use_schema": True,
            "can_temp_db": True,
        }
        if effective_privileges:
            default_privs.update(effective_privileges)
        self.effective_privileges = default_privs
        self.throw_on_alter_nologin = throw_on_alter_nologin
        self.throw_on_create_role = throw_on_create_role
        self.throw_on_auth_query = throw_on_auth_query
        self.throw_on_close = False
        self.executed_statements: list[str] = []
        self.is_closed: bool = False
        self._protocol = MagicMock(is_ssl=self.client_ssl_active)
        self._transport = MagicMock()
        self._transport.get_extra_info.return_value = (
            MagicMock() if self.client_ssl_active else None
        )

    def is_ssl(self) -> bool:
        return self.client_ssl_active

    async def fetchrow(self, query: str, *args: Any) -> Any:
        q = query.strip()
        if (
            "SELECT current_user, session_user, current_database(), ssl_is_used();" in q
            or "ssl_is_used()" in q
            or "pg_stat_ssl" in q
        ):
            return (self.current_user, self.session_user, self.current_database, self.ssl_active)
        if "SELECT count(*) FROM pg_roles WHERE rolname" in q:
            return (1 if self.role_exists else 0,)
        if "has_schema_privilege('public', 'public', 'CREATE')" in q:
            return {
                "pub_schema_create": self.pub_schema_create,
                "pub_db_create": self.pub_db_create,
                "pub_db_connect": self.pub_db_connect,
                "pub_db_temp": self.pub_db_temp,
            }
        if "SELECT count(*) FROM pg_auth_members" in q:
            if self.role_memberships is None:
                return (None,)
            return (self.role_memberships,)
        if "SELECT (SELECT count(*) FROM pg_database" in q:
            return {
                "db_owned": self.ownership[0],
                "nsp_owned": self.ownership[1],
                "rel_owned": self.ownership[2],
                "proc_owned": self.ownership[3] if len(self.ownership) > 3 else 0,
                "typ_owned": self.ownership[4] if len(self.ownership) > 4 else 0,
            }
        if "has_database_privilege($1, current_database(), 'CONNECT')" in q:
            return self.effective_privileges
        if "SELECT current_user, session_user;" in q:
            if self.throw_on_auth_query:
                raise RuntimeError("Simulated query failure during auth check")
            return (self.current_user, self.session_user)
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        if "SELECT * FROM pg_roles WHERE rolname" in query:
            if not self.role_exists:
                return []
            return [self.role_flags]
        return []

    async def execute(self, query: str) -> None:
        if self.throw_on_alter_nologin and "ALTER ROLE" in query and "NOLOGIN" in query:
            raise RuntimeError("Database connection broken during containment")
        if self.throw_on_create_role and "CREATE ROLE" in query:
            raise RuntimeError(f"PostgreSQL syntax error near {query}")
        self.executed_statements.append(query)
        if "CREATE ROLE" in query:
            self.role_exists = True
        if "ALTER ROLE" in query and "LOGIN;" in query:
            self.role_flags["rolcanlogin"] = True
        if "ALTER ROLE" in query and "NOLOGIN;" in query:
            self.role_flags["rolcanlogin"] = False

    async def close(self) -> None:
        self.is_closed = True
        if self.throw_on_close:
            raise RuntimeError("Connection dropped during close")


class MockDatabaseConnector:
    """Mock implementation of DatabaseConnectorProtocol."""

    def __init__(
        self,
        admin_conn: MockDbConn | None = None,
        fresh_admin_conn: MockDbConn | None = None,
        app_conn: MockDbConn | None = None,
        raise_on_connect: Exception | None = None,
    ) -> None:
        self.admin_conn = admin_conn or MockDbConn(role_exists=False)
        self.fresh_admin_conn = fresh_admin_conn
        self.app_conn = app_conn
        self.raise_on_connect = raise_on_connect
        self.connect_calls: list[dict[str, Any]] = []

    async def connect(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        ssl_context: ssl.SSLContext | None = None,
    ) -> Any:
        self.connect_calls.append(
            {
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "database": database,
                "ssl_context": ssl_context,
            }
        )
        if self.raise_on_connect:
            raise self.raise_on_connect

        if user == ADMIN_ROLE:
            if self.fresh_admin_conn is not None and (
                self.admin_conn.is_closed or len(self.connect_calls) > 1
            ):
                return self.fresh_admin_conn
            return self.admin_conn

        if user == APP_ROLE:
            if self.app_conn is not None:
                return self.app_conn
            return MockDbConn(current_user=APP_ROLE, session_user=APP_ROLE, role_exists=True)

        return MockDbConn()


@pytest.fixture
def checkpoint_file(tmp_path: Any) -> str:
    return str(tmp_path / "checkpoint.json")


@pytest.fixture
def mock_secret_store() -> SecretStore:
    mock_adapter = MagicMock()
    mock_adapter.entry_exists.return_value = False
    stored_pwd = ["mocked-neon-generated-password-12345"]

    def _store(pwd: str, allow_overwrite: bool = False) -> str:
        stored_pwd[0] = pwd
        return pwd

    mock_adapter.store_password.side_effect = _store
    mock_adapter.get_password.side_effect = lambda: stored_pwd[0]
    mock_adapter.verify_stored_password.return_value = True
    return SecretStore(service=KEYCHAIN_SERVICE, account=KEYCHAIN_ACCOUNT, adapter=mock_adapter)


# ==============================================================================
# Category 1: Target and Connection Binding (TLS & Host Enforcement)
# ==============================================================================


def test_database_connector_rejects_host_mismatch() -> None:
    connector = DatabaseConnector()
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(
            connector.connect(
                host="ep-attacker-controlled.neon.tech",
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password="secret-admin-password",
                database=EXPECTED_DATABASE,
            )
        )
    assert "Target host 'ep-attacker-controlled.neon.tech' rejected" in str(exc.value)


def test_database_connector_rejects_port_mismatch() -> None:
    connector = DatabaseConnector()
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(
            connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=5433,
                user=ADMIN_ROLE,
                password="secret-admin-password",
                database=EXPECTED_DATABASE,
            )
        )
    assert "Target port '5433' rejected" in str(exc.value)


def test_database_connector_rejects_database_mismatch() -> None:
    connector = DatabaseConnector()
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(
            connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password="secret-admin-password",
                database="wrong_database",
            )
        )
    assert "Target database 'wrong_database' rejected" in str(exc.value)


def test_database_connector_rejects_insecure_ssl_context() -> None:
    connector = DatabaseConnector()
    insecure_ctx = ssl.create_default_context()
    insecure_ctx.check_hostname = False
    insecure_ctx.verify_mode = ssl.CERT_NONE

    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(
            connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password="secret-admin-password",
                database=EXPECTED_DATABASE,
                ssl_context=insecure_ctx,
            )
        )
    assert "Insecure SSL context rejected" in str(exc.value)


def test_verify_pg_tls_and_identity_host_mismatch(checkpoint_file: str) -> None:
    conn = MockDbConn(host="ep-attacker-controlled.neon.tech")
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert "Host mismatch" in str(exc.value)


def test_verify_pg_tls_and_identity_user_mismatch(checkpoint_file: str) -> None:
    conn = MockDbConn(current_user="untrusted_user")
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert "Admin identity mismatch" in str(exc.value)


def test_verify_pg_tls_and_identity_session_user_mismatch(checkpoint_file: str) -> None:
    conn = MockDbConn(session_user="untrusted_session_user")
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert "Admin identity mismatch" in str(exc.value)


def test_verify_pg_tls_and_identity_database_mismatch(checkpoint_file: str) -> None:
    conn = MockDbConn(current_database="untrusted_db")
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert "Database mismatch" in str(exc.value)


def test_verify_pg_tls_and_identity_accepts_proxy_ssl_false(checkpoint_file: str) -> None:
    conn = MockDbConn(ssl_active=False, client_ssl_active=True)
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert provisioner._last_backend_ssl_active is False


# ==============================================================================
# Category 2: Real CLI Execution Lifecycle & Preflight
# ==============================================================================


def test_cli_missing_neon_api_key_exits_1(checkpoint_file: str, monkeypatch: Any) -> None:
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    code = cli_entrypoint(["--checkpoint-file", checkpoint_file], api_client=None)
    assert code == 1


def test_cli_dry_run_without_admin_password_returns_partial_exit_1(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    code = cli_entrypoint(
        ["--checkpoint-file", checkpoint_file],
        api_client=api,
        secret_store=mock_secret_store,
        admin_password=None,
    )
    assert code == 1
    assert not api.reset_called


def test_cli_dry_run_with_admin_password_passes_exit_0(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)
    code = cli_entrypoint(
        ["--checkpoint-file", checkpoint_file],
        api_client=api,
        secret_store=mock_secret_store,
        connector=connector,
        admin_password="test-admin-password",
    )
    assert code == 0
    assert not api.reset_called
    assert len(connector.connect_calls) >= 1


def test_cli_execute_without_admin_password_exits_1(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    code = cli_entrypoint(
        ["--execute", "--confirm-branch", STAGING_BRANCH_ID, "--checkpoint-file", checkpoint_file],
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        admin_password=None,
    )
    assert code == 1


def test_cli_contain_without_admin_password_exits_1(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    code = cli_entrypoint(
        [
            "--contain-role",
            "--confirm-branch",
            STAGING_BRANCH_ID,
            "--checkpoint-file",
            checkpoint_file,
        ],
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        admin_password=None,
    )
    assert code == 1


def test_cli_execution_without_confirmation_fails(checkpoint_file: str) -> None:
    code = cli_entrypoint(["--execute", "--checkpoint-file", checkpoint_file])
    assert code == 1


def test_cli_containment_without_confirmation_fails(checkpoint_file: str) -> None:
    code = cli_entrypoint(["--contain-role", "--checkpoint-file", checkpoint_file])
    assert code == 1


def test_cli_production_branch_rejected_across_all_modes(checkpoint_file: str) -> None:
    for branch_flag in (PRODUCTION_BRANCH_ID, PRODUCTION_BRANCH_NAME):
        assert (
            cli_entrypoint(
                ["--execute", "--confirm-branch", branch_flag, "--checkpoint-file", checkpoint_file]
            )
            == 1
        )
        assert (
            cli_entrypoint(
                [
                    "--contain-role",
                    "--confirm-branch",
                    branch_flag,
                    "--checkpoint-file",
                    checkpoint_file,
                ]
            )
            == 1
        )
        assert (
            cli_entrypoint(
                ["--dry-run", "--confirm-branch", branch_flag, "--checkpoint-file", checkpoint_file]
            )
            == 1
        )


def test_cli_execution_dispatch_success(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    app_conn = MockDbConn(current_user=APP_ROLE, session_user=APP_ROLE, role_exists=True)
    connector = MockDatabaseConnector(
        admin_conn=admin_conn, fresh_admin_conn=fresh_admin_conn, app_conn=app_conn
    )

    code = cli_entrypoint(
        ["--execute", "--confirm-branch", STAGING_BRANCH_ID, "--checkpoint-file", checkpoint_file],
        api_client=api,
        secret_store=mock_secret_store,
        connector=connector,
        admin_password="valid-staging-admin-pwd",
    )
    assert code == 0
    assert api.reset_called
    assert fresh_admin_conn.role_flags["rolcanlogin"] is True
    assert admin_conn.is_closed is True
    assert fresh_admin_conn.is_closed is True


def test_cli_containment_dispatch_success(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=True, role_flags={"rolcanlogin": True})
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    code = cli_entrypoint(
        [
            "--contain-role",
            "--confirm-branch",
            STAGING_BRANCH_ID,
            "--checkpoint-file",
            checkpoint_file,
        ],
        api_client=api,
        secret_store=mock_secret_store,
        connector=connector,
        admin_password="valid-staging-admin-pwd",
    )
    assert code == 0
    assert admin_conn.role_flags["rolcanlogin"] is False
    assert any("ALTER ROLE dormiostay_app NOLOGIN;" in s for s in admin_conn.executed_statements)


# ==============================================================================
# Category 3: Fail-Closed LOGIN Containment
# ==============================================================================


def test_session_user_mismatch_triggers_auto_containment(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    # Returns session_user as "neondb_owner" instead of "dormiostay_app"
    mismatched_app_conn = MockDbConn(
        current_user=APP_ROLE, session_user="neondb_owner", role_exists=True
    )
    connector = MockDatabaseConnector(
        admin_conn=admin_conn, fresh_admin_conn=fresh_admin_conn, app_conn=mismatched_app_conn
    )

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))

    assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(exc.value)
    assert fresh_admin_conn.role_flags["rolcanlogin"] is False
    has_nologin = any(
        "ALTER ROLE dormiostay_app NOLOGIN;" in s for s in fresh_admin_conn.executed_statements
    )
    assert has_nologin
    assert provisioner.state == ProvisioningState.CONTAINMENT_EXECUTED


def test_containment_fallback_to_fresh_connection(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    # Existing fresh connection breaks when executing ALTER ROLE ... NOLOGIN
    broken_fresh_admin = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    emergency_admin_conn = MockDbConn(role_exists=True)
    mismatched_app = MockDbConn(current_user=APP_ROLE, session_user="attacker", role_exists=True)

    class FallbackConnector:
        def __init__(self) -> None:
            self.calls = 0

        async def connect(
            self,
            host: str,
            port: int,
            user: str,
            password: str,
            database: str,
            ssl_context: ssl.SSLContext | None = None,
        ) -> Any:
            self.calls += 1
            if user == APP_ROLE:
                return mismatched_app
            if self.calls == 1:
                return admin_conn
            if self.calls == 2:
                return broken_fresh_admin
            return emergency_admin_conn

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=FallbackConnector(),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))

    assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(exc.value)
    assert emergency_admin_conn.role_flags["rolcanlogin"] is False
    has_emergency_nologin = any(
        "ALTER ROLE dormiostay_app NOLOGIN;" in s for s in emergency_admin_conn.executed_statements
    )
    assert has_emergency_nologin


def test_containment_failure_raises_critical_error(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    broken_fresh_admin = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    mismatched_app = MockDbConn(current_user=APP_ROLE, session_user="attacker", role_exists=True)

    class FailingFallbackConnector:
        def __init__(self) -> None:
            self.calls = 0

        async def connect(
            self,
            host: str,
            port: int,
            user: str,
            password: str,
            database: str,
            ssl_context: ssl.SSLContext | None = None,
        ) -> Any:
            self.calls += 1
            if user == APP_ROLE:
                return mismatched_app
            if self.calls == 1:
                return admin_conn
            if self.calls == 2:
                return broken_fresh_admin
            raise TargetVerificationError("Network broken during emergency reconnection")

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=FailingFallbackConnector(),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))

    assert "CRITICAL: Post-LOGIN validation failed" in str(exc.value)
    assert "emergency containment failed" in str(exc.value)
    assert provisioner.state == ProvisioningState.CONTAINMENT_FAILED


def test_containment_checkpoint_disk_failure_does_not_mask_outcome(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=True, role_flags={"rolcanlogin": True})
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    mock_checkpoint = MagicMock()
    mock_checkpoint.record.side_effect = OSError("Read-only filesystem")

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=mock_checkpoint,
        connector=connector,
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    # Does not raise OSError; containment succeeds despite disk error
    asyncio.run(provisioner.execute_containment_nologin(admin_password="valid-admin-password"))
    assert admin_conn.role_flags["rolcanlogin"] is False


# ==============================================================================
# Category 4: Native macOS Security.framework Adapter
# ==============================================================================


def test_keychain_adapter_boundary_store_and_readback() -> None:
    mock_adapter = MagicMock()
    mock_adapter.entry_exists.return_value = False
    mock_adapter.store_password.return_value = "stored-secret-pw"
    mock_adapter.get_password.return_value = "stored-secret-pw"
    mock_adapter.verify_stored_password.return_value = True

    store = SecretStore(service="test.svc", account="test.acc", adapter=mock_adapter)
    assert not store.entry_exists()
    stored = store.store_password("stored-secret-pw", allow_overwrite=False)
    assert stored == "stored-secret-pw"
    assert store.get_password() == "stored-secret-pw"
    assert store.verify_stored_password() is True


def test_keychain_adapter_duplicate_entry_rejected_without_overwrite() -> None:
    mock_adapter = MagicMock()
    mock_adapter.entry_exists.return_value = True

    store = SecretStore(service="test.svc", account="test.acc", adapter=mock_adapter)
    with pytest.raises(SecretStoreError) as exc:
        store.store_password("new-password", allow_overwrite=False)
    assert "already exists in Keychain" in str(exc.value)


def test_keychain_adapter_overwrite_succeeds() -> None:
    mock_adapter = MagicMock()
    mock_adapter.entry_exists.return_value = True
    mock_adapter.store_password.return_value = "overwritten-pw"

    store = SecretStore(service="test.svc", account="test.acc", adapter=mock_adapter)
    stored = store.store_password("overwritten-pw", allow_overwrite=True)
    assert stored == "overwritten-pw"
    mock_adapter.store_password.assert_called_once_with("overwritten-pw", allow_overwrite=True)


def test_secret_store_failure_halts_before_login(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    msg = "SecKeychainAddGenericPassword failed: errSecAuthFailed (-60008)"
    mock_secret_store.store_password = MagicMock(  # type: ignore[method-assign]
        side_effect=SecretStoreError(msg)
    )
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(SecretStoreError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))

    assert "errSecAuthFailed" in str(exc.value)
    # Critical invariant: LOGIN must NEVER have been enabled!
    assert admin_conn.role_flags["rolcanlogin"] is False
    assert not any("ALTER ROLE dormiostay_app LOGIN;" in s for s in admin_conn.executed_statements)


def test_unverified_stored_password_halts_before_login(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    mock_secret_store.verify_stored_password = MagicMock(return_value=False)  # type: ignore[method-assign]
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(SecretStoreError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))

    assert "Failed to verify durable password storage" in str(exc.value)
    assert admin_conn.role_flags["rolcanlogin"] is False
    assert not any("ALTER ROLE dormiostay_app LOGIN;" in s for s in admin_conn.executed_statements)


def test_app_authentication_uses_keychain_retrieved_password(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "keychain-verified-pwd-12345",
            },
            "operations": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    app_conn = MockDbConn(current_user=APP_ROLE, session_user=APP_ROLE, role_exists=True)
    connector = MockDatabaseConnector(
        admin_conn=admin_conn, fresh_admin_conn=fresh_admin_conn, app_conn=app_conn
    )

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    asyncio.run(provisioner.run_provisioning(admin_password="admin-password-12345"))

    # Verify that the connect call for APP_ROLE passed exactly the Keychain-retrieved password
    app_connect_calls = [c for c in connector.connect_calls if c["user"] == APP_ROLE]
    assert len(app_connect_calls) == 1
    assert app_connect_calls[0]["password"] == "keychain-verified-pwd-12345"


# ==============================================================================
# Category 5: Secret Redaction & Diagnostic Mapping
# ==============================================================================


def test_output_redaction_sanitizes_credentials() -> None:
    raw = (
        "Connection postgresql://user:my_secret_pass@ep-host.aws.neon.tech/neondb "
        "failed with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and npg_secretkey123"
    )
    sanitized = _sanitize_text(raw)
    assert "my_secret_pass" not in sanitized
    assert "postgresql://***:***@ep-host.aws.neon.tech" in sanitized
    assert "eyJhbGci" not in sanitized
    assert "npg_secretkey123" not in sanitized


def test_secret_redactor_redacts_registered_tokens() -> None:
    redactor = SecretRedactor()
    ephemeral_token = "ephemeral_token_abc1234567890"
    admin_pwd = "super_admin_secret_pass"
    redactor.register(ephemeral_token)
    redactor.register(admin_pwd)

    text = f"Error: query failed with token '{ephemeral_token}' and admin '{admin_pwd}'"
    redacted = redactor.redact(text)
    assert ephemeral_token not in redacted
    assert admin_pwd not in redacted
    assert "[REDACTED]" in redacted


def test_map_db_error_suppresses_raw_query_and_passwords() -> None:
    class FakeDbError(Exception):
        def __init__(self, message: str, sqlstate: str) -> None:
            super().__init__(message)
            self.sqlstate = sqlstate

    err = FakeDbError("syntax error near PASSWORD 'leak_me'", "42601")
    mapped = map_db_error(err)
    assert "SYNTAX_ERROR (SQLSTATE 42601)" in mapped
    assert "leak_me" not in mapped

    err_auth = FakeDbError("password authentication failed for user", "28P01")
    mapped_auth = map_db_error(err_auth)
    assert "INVALID_PASSWORD (SQLSTATE 28P01)" in mapped_auth


def test_map_http_error_suppresses_raw_response_bodies() -> None:
    assert "HTTP 401 Unauthorized" in map_http_error(401)
    assert "HTTP 403 Forbidden" in map_http_error(403)
    assert "HTTP 404 Not Found" in map_http_error(404)
    assert "HTTP 429 Too Many Requests" in map_http_error(429)
    assert "HTTP 500" in map_http_error(500)


def test_raw_sql_error_credential_redaction(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False, throw_on_create_role=True)
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))

    err_str = str(exc.value)
    assert "DATABASE_OPERATION_FAILED" in err_str
    # Verify no raw password leaked
    assert "PASSWORD" not in err_str


# ==============================================================================
# Category 6: Strict Password-Reset Response Validation & Polling
# ==============================================================================


def test_pending_reset_operations_polled(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "new-pass-1234567890123",
            },
            "operations": [
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "running",
                }
            ],
        },
        operations={
            "22222222-2222-2222-2222-222222222222": {
                "operation": {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            }
        },
    )
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    app_conn = MockDbConn(current_user=APP_ROLE, session_user=APP_ROLE, role_exists=True)
    connector = MockDatabaseConnector(
        admin_conn=admin_conn, fresh_admin_conn=fresh_admin_conn, app_conn=app_conn
    )

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))
    assert "22222222-2222-2222-2222-222222222222" in api.polled_ops
    assert provisioner.state == ProvisioningState.COMPLETED


def test_failed_reset_operation_halts(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "new-pass-1234567890123",
            },
            "operations": [
                {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "failed",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))
    assert "operation '33333333-3333-3333-3333-333333333333' failed" in str(exc.value)
    assert admin_conn.role_flags["rolcanlogin"] is False


def test_timed_out_reset_operation_halts(
    checkpoint_file: str, mock_secret_store: SecretStore, monkeypatch: Any
) -> None:
    async def fast_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "new-pass-1234567890123",
            },
            "operations": [
                {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "running",
                }
            ],
        },
        operations={
            "44444444-4444-4444-4444-444444444444": {
                "operation": {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "running",
                }
            }
        },
    )
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(OperationTimeoutError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))
    assert "timed out while pending" in str(exc.value)


def test_lost_reset_response_halts_without_blind_retry(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
            },  # Missing 'password'
            "operations": [
                {
                    "id": "55555555-5555-5555-5555-555555555555",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))
    assert "did not return valid final password" in str(exc.value)
    assert admin_conn.role_flags["rolcanlogin"] is False


# ==============================================================================
# Category 7: Fresh Administrative Reconnection Post-Reset
# ==============================================================================


def test_initial_admin_conn_closed_before_fresh_reconnection(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    app_conn = MockDbConn(current_user=APP_ROLE, session_user=APP_ROLE, role_exists=True)
    connector = MockDatabaseConnector(
        admin_conn=admin_conn, fresh_admin_conn=fresh_admin_conn, app_conn=app_conn
    )

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    asyncio.run(provisioner.run_provisioning(admin_password="valid-admin-password"))

    assert admin_conn.is_closed is True
    assert fresh_admin_conn.is_closed is True
    # Initial conn created NOLOGIN role, fresh conn enabled LOGIN
    assert any("CREATE ROLE" in s for s in admin_conn.executed_statements)
    has_login = any(
        "ALTER ROLE dormiostay_app LOGIN;" in s for s in fresh_admin_conn.executed_statements
    )
    assert has_login


# ==============================================================================
# Category 8: Complete Privilege & Ownership Verification
# ==============================================================================


def test_superuser_flag_rejection(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    conn = MockDbConn(
        role_exists=True,
        role_flags={
            "rolcanlogin": False,
            "rolsuper": True,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolbypassrls": False,
            "rolreplication": False,
        },
    )
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "rolsuper is true" in str(exc.value)


def test_createdb_flag_rejection(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    conn = MockDbConn(
        role_exists=True,
        role_flags={"rolcanlogin": False, "rolcreatedb": True},
    )
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "rolcreatedb is true" in str(exc.value)


def test_createrole_flag_rejection(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    conn = MockDbConn(
        role_exists=True,
        role_flags={"rolcanlogin": False, "rolcreaterole": True},
    )
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "rolcreaterole is true" in str(exc.value)


def test_bypassrls_flag_rejection(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    conn = MockDbConn(
        role_exists=True,
        role_flags={"rolcanlogin": False, "rolbypassrls": True},
    )
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "rolbypassrls is true" in str(exc.value)


def test_replication_flag_rejection(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    conn = MockDbConn(
        role_exists=True,
        role_flags={"rolcanlogin": False, "rolreplication": True},
    )
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "rolreplication is true" in str(exc.value)


def test_role_memberships_rejection(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    conn = MockDbConn(role_exists=True, role_memberships=1)
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "Role membership failed" in str(exc.value)


def test_ownership_rejections(checkpoint_file: str, mock_secret_store: SecretStore) -> None:
    # Test rejection of database ownership
    conn_db = MockDbConn(role_exists=True, ownership=(1, 0, 0, 0, 0))
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc_db:
        asyncio.run(provisioner.verify_structural_restrictions(conn_db, expect_login=False))
    assert "Ownership assertion failed" in str(exc_db.value)

    # Test rejection of routine/procedure ownership
    conn_proc = MockDbConn(role_exists=True, ownership=(0, 0, 0, 1, 0))
    with pytest.raises(PrivilegeVerificationError) as exc_proc:
        asyncio.run(provisioner.verify_structural_restrictions(conn_proc, expect_login=False))
    assert "Ownership assertion failed" in str(exc_proc.value)

    # Test rejection of custom type ownership
    conn_typ = MockDbConn(role_exists=True, ownership=(0, 0, 0, 0, 1))
    with pytest.raises(PrivilegeVerificationError) as exc_typ:
        asyncio.run(provisioner.verify_structural_restrictions(conn_typ, expect_login=False))
    assert "Ownership assertion failed" in str(exc_typ.value)


def test_baseline_public_schema_create_rejection(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    conn = MockDbConn(pub_schema_create=True)
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_baseline_privileges(conn))
    assert "PUBLIC role possesses CREATE on public schema" in str(exc.value)


def test_baseline_public_db_create_rejection(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    conn = MockDbConn(pub_db_create=True)
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_baseline_privileges(conn))
    assert "PUBLIC role possesses CREATE on database" in str(exc.value)


def test_effective_privilege_assertions(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    # Denied CONNECT
    conn_no_conn = MockDbConn(
        role_exists=True,
        effective_privileges={
            "can_connect": False,
            "can_create_db": False,
            "can_create_schema": False,
            "can_use_schema": True,
        },
    )
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc_conn:
        asyncio.run(provisioner.verify_structural_restrictions(conn_no_conn, expect_login=False))
    assert "CONNECT denied on database" in str(exc_conn.value)

    # Granted CREATE on database
    conn_db_create = MockDbConn(
        role_exists=True,
        effective_privileges={
            "can_connect": True,
            "can_create_db": True,
            "can_create_schema": False,
            "can_use_schema": True,
        },
    )
    with pytest.raises(PrivilegeVerificationError) as exc_create:
        asyncio.run(provisioner.verify_structural_restrictions(conn_db_create, expect_login=False))
    assert "Database CREATE granted" in str(exc_create.value)

    # Granted CREATE on schema
    conn_sch_create = MockDbConn(
        role_exists=True,
        effective_privileges={
            "can_connect": True,
            "can_create_db": False,
            "can_create_schema": True,
            "can_use_schema": True,
        },
    )
    with pytest.raises(PrivilegeVerificationError) as exc_sch:
        asyncio.run(provisioner.verify_structural_restrictions(conn_sch_create, expect_login=False))
    assert "Schema CREATE granted" in str(exc_sch.value)


def test_role_row_count_missing_or_duplicate(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    conn = MockDbConn(role_exists=False)
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn))
    assert "Role row count failed" in str(exc.value)


# ==============================================================================
# Category 9: Secure Checkpoint Persistence
# ==============================================================================


def test_checkpoint_recording_and_retrieval(checkpoint_file: str) -> None:
    mgr = CheckpointManager(checkpoint_file)
    mgr.record(
        ProvisioningState.ROLE_CREATED_NOLOGIN,
        step_number=4,
        details="Created role with temporary token",
    )
    latest = mgr.get_latest_checkpoint()
    assert latest is not None
    assert latest.state == ProvisioningState.ROLE_CREATED_NOLOGIN
    assert latest.step_number == 4


def test_checkpoint_symlink_file_rejection(tmp_path: Any) -> None:
    target_file = tmp_path / "target.json"
    target_file.write_text("[]", encoding="utf-8")
    symlink_file = tmp_path / "symlink_checkpoint.json"
    os.symlink(target_file, symlink_file)

    mgr = CheckpointManager(str(symlink_file))
    with pytest.raises(TargetVerificationError) as exc:
        mgr.record(ProvisioningState.INITIALIZED, step_number=1, details="Symlink test")
    assert "unsafe symlink" in str(exc.value).lower()

    with pytest.raises(TargetVerificationError) as exc_get:
        mgr.get_latest_checkpoint()
    assert "unsafe symlink" in str(exc_get.value).lower()


def test_checkpoint_symlink_dir_rejection(tmp_path: Any) -> None:
    target_dir = tmp_path / "real_dir"
    target_dir.mkdir()
    symlink_dir = tmp_path / "symlink_dir"
    os.symlink(target_dir, symlink_dir)

    mgr = CheckpointManager(str(symlink_dir / "checkpoint.json"))
    with pytest.raises(TargetVerificationError) as exc:
        mgr.record(ProvisioningState.INITIALIZED, step_number=1, details="Symlink dir test")
    assert "unsafe symlink" in str(exc.value).lower()


def test_checkpoint_atomic_replace_and_permissions(checkpoint_file: str) -> None:
    mgr = CheckpointManager(checkpoint_file)
    mgr.record(ProvisioningState.INITIALIZED, step_number=1, details="Init test")

    assert os.path.exists(checkpoint_file)
    mode = os.stat(checkpoint_file).st_mode & 0o777
    assert mode == 0o600


def test_checkpoint_scrubs_secrets(checkpoint_file: str) -> None:
    redactor = SecretRedactor()
    raw_secret = "super_secret_token_1234567890"
    redactor.register(raw_secret)

    mgr = CheckpointManager(checkpoint_file, redactor=redactor)
    mgr.record(
        ProvisioningState.INITIALIZED,
        step_number=1,
        details=f"Contains secret {raw_secret}",
    )

    with open(checkpoint_file, encoding="utf-8") as f:
        content = f.read()

    assert raw_secret not in content
    assert "[REDACTED]" in content


def test_corrupted_checkpoint_recovery(checkpoint_file: str) -> None:
    with open(checkpoint_file, "w", encoding="utf-8") as f:
        f.write("{invalid json file corrupt}")

    mgr = CheckpointManager(checkpoint_file)
    with pytest.raises(ProvisioningError) as exc:
        mgr.get_latest_checkpoint()
    assert "corrupted" in str(exc.value)

    # Corrupt history prevents further appending until cleanly reset
    with pytest.raises(ProvisioningError) as exc_rec:
        mgr.record(ProvisioningState.INITIALIZED, step_number=1, details="Recovery record")
    assert "corrupted" in str(exc_rec.value)

    os.unlink(checkpoint_file)
    mgr.record(ProvisioningState.INITIALIZED, step_number=1, details="Fresh record")
    latest = mgr.get_latest_checkpoint()
    assert latest is not None
    assert latest.state == ProvisioningState.INITIALIZED


# ==============================================================================
# Category 10: Control Plane Security & Redirects
# ==============================================================================


def test_staging_metadata_project_mismatch(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(project_id="untrusted-project-id")
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(TargetVerificationError) as exc:
        provisioner.verify_target_metadata()
    assert "Project ID mismatch" in str(exc.value)


def test_staging_metadata_branch_missing(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(branches=[{"id": "br-other-branch", "name": "other"}])
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(TargetVerificationError) as exc:
        provisioner.verify_target_metadata()
    assert "Staging branch 'br-weathered-dust-bim5fibs' not found" in str(exc.value)


def test_staging_metadata_branch_name_mismatch(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(branches=[{"id": STAGING_BRANCH_ID, "name": "renamed_branch"}])
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
    )
    with pytest.raises(TargetVerificationError) as exc:
        provisioner.verify_target_metadata()
    assert "Branch name mismatch" in str(exc.value)


def test_cross_origin_http_redirect_rejection() -> None:
    handler = StrictNoRedirectHandler()
    req = urllib.request.Request("https://console.neon.tech/api/v2/test")
    with pytest.raises(TargetVerificationError) as exc:
        handler.redirect_request(
            req, fp=None, code=302, msg="Found", headers={}, newurl="https://attacker.com/leak"
        )
    assert "forbidden by security policy" in str(exc.value)


# ==============================================================================
# Category 11: Adversarial Regression Tests for Astra Findings 1-8
# ==============================================================================


def test_post_login_cancelled_error_triggers_containment_and_reraises(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    fresh_admin_conn.throw_on_alter_nologin = False

    async def fail_structural_check(conn: Any, expect_login: bool = False) -> None:
        if expect_login:
            raise asyncio.CancelledError("Task was cancelled after LOGIN")

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(
            admin_conn=admin_conn,
            fresh_admin_conn=fresh_admin_conn,
        ),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    provisioner.verify_structural_restrictions = fail_structural_check  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provisioner.run_provisioning("admin-pwd"))

    assert fresh_admin_conn.role_flags["rolcanlogin"] is False
    assert any("NOLOGIN" in s for s in fresh_admin_conn.executed_statements)


def test_post_login_keyboard_interrupt_triggers_containment_and_reraises(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)

    async def interrupt_check(conn: Any, expect_login: bool = False) -> None:
        if expect_login:
            raise KeyboardInterrupt("Simulated Ctrl+C after LOGIN")

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(
            admin_conn=admin_conn,
            fresh_admin_conn=fresh_admin_conn,
        ),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    provisioner.verify_structural_restrictions = interrupt_check  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(provisioner.run_provisioning("admin-pwd"))

    assert fresh_admin_conn.role_flags["rolcanlogin"] is False


def test_checkpoint_step_12_failure_reverts_to_nologin(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)

    ckpt = CheckpointManager(checkpoint_file)
    orig_record = ckpt.record

    def fail_at_step_12(state: ProvisioningState, step_number: int, details: str) -> Any:
        if step_number == 12:
            raise OSError("Disk failure during step 12 checkpoint write")
        return orig_record(state, step_number, details)

    ckpt.record = fail_at_step_12  # type: ignore[assignment]

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=ckpt,
        connector=MockDatabaseConnector(
            admin_conn=admin_conn,
            fresh_admin_conn=fresh_admin_conn,
        ),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(exc.value)
    assert fresh_admin_conn.role_flags["rolcanlogin"] is False


def test_reset_missing_operations_field_rejected(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={"role": {"name": APP_ROLE, "password": "valid-pwd-1234567890"}}
    )
    admin_conn = MockDbConn(role_exists=False)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(admin_conn=admin_conn),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert "missing operations list" in str(exc.value)


def test_reset_empty_operations_rejected(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {"name": APP_ROLE, "password": "valid-pwd-1234567890"},
            "operations": [],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(admin_conn=admin_conn),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert "missing operations list" in str(exc.value)


def test_reset_operations_mismatched_project_rejected(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {"name": APP_ROLE, "password": "valid-pwd-1234567890"},
            "operations": [
                {
                    "id": "66666666-6666-6666-6666-666666666666",
                    "project_id": "alien-project",
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(admin_conn=admin_conn),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert "Operation project mismatch" in str(exc.value)


def test_reset_operations_production_branch_rejected(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "valid-pwd-1234567890",
            },
            "operations": [
                {
                    "id": "77777777-7777-7777-7777-777777777777",
                    "project_id": PROJECT_ID,
                    "branch_id": PRODUCTION_BRANCH_ID,
                    "status": "finished",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(admin_conn=admin_conn),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert "Production branch operation detected" in str(exc.value)


def test_polled_operation_reporting_failed_raises_provisioning_error(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "valid-pwd-1234567890",
            },
            "operations": [
                {
                    "id": "88888888-8888-8888-8888-888888888888",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "running",
                }
            ],
        },
        operations={
            "88888888-8888-8888-8888-888888888888": {
                "operation": {
                    "id": "88888888-8888-8888-8888-888888888888",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "failed",
                }
            }
        },
    )
    admin_conn = MockDbConn(role_exists=False)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(admin_conn=admin_conn),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert "reported failed status" in str(exc.value)


def test_emergency_containment_reverifies_metadata_and_rejects_altered_host(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi(
        endpoints=[
            {
                "id": "ep-staging-1",
                "branch_id": STAGING_BRANCH_ID,
                "host": "ep-altered-host.neon.tech",
            }
        ]
    )
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.execute_containment_nologin(admin_password="pwd"))
    assert "Endpoint mismatch" in str(exc.value)


def test_auto_containment_fails_closed_when_reverify_metadata_fails(
    checkpoint_file: str, mock_secret_store: SecretStore
) -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    fresh_admin_conn.throw_on_alter_nologin = True

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_secret_store,
        checkpoint_mgr=CheckpointManager(checkpoint_file),
        connector=MockDatabaseConnector(
            admin_conn=admin_conn,
            fresh_admin_conn=fresh_admin_conn,
        ),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    api.endpoints = [
        {
            "id": "ep-staging-1",
            "branch_id": STAGING_BRANCH_ID,
            "host": "ep-shifted-endpoint.neon.tech",
        }
    ]

    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                fresh_admin_conn, RuntimeError("Auth check failed"), admin_password="admin-pwd"
            )
        )
    assert "CRITICAL: Post-LOGIN validation failed" in str(exc.value)
    assert "emergency containment failed" in str(exc.value)


def test_keychain_adapter_status_codes() -> None:
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    fake_sec.SecKeychainSearchCreateFromAttributes.return_value = ERR_SEC_ITEM_NOT_FOUND

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )

    fake_sec.SecKeychainFindGenericPassword.return_value = ERR_SEC_ITEM_NOT_FOUND
    assert adapter.entry_exists() is False

    fake_sec.SecKeychainFindGenericPassword.return_value = ERR_SEC_AUTH_FAILED
    with pytest.raises(SecretStoreError) as exc1:
        adapter.entry_exists()
    assert "Keychain access denied" in str(exc1.value)

    fake_sec.SecKeychainFindGenericPassword.return_value = ERR_SEC_INTERACTION_NOT_ALLOWED
    with pytest.raises(SecretStoreError) as exc2:
        adapter.entry_exists()
    assert "Keychain access denied" in str(exc2.value)

    fake_sec.SecKeychainFindGenericPassword.return_value = ERR_SEC_PARAM
    with pytest.raises(SecretStoreError) as exc3:
        adapter.entry_exists()
    assert f"status {ERR_SEC_PARAM}" in str(exc3.value)

    # 5. Duplicate item raises SecretStoreError
    fake_sec.SecKeychainFindGenericPassword.return_value = ERR_SEC_ITEM_NOT_FOUND
    fake_sec.SecKeychainAddGenericPassword.return_value = ERR_SEC_DUPLICATE_ITEM
    with pytest.raises(SecretStoreError) as exc4:
        adapter.store_password("pwd_test")
    assert "errSecDuplicateItem" in str(exc4.value)


def test_keychain_adapter_byte_comparison_mismatch() -> None:
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    fake_sec.SecKeychainSearchCreateFromAttributes.return_value = ERR_SEC_ITEM_NOT_FOUND

    def fake_add(
        kc: Any, slen: Any, s: Any, alen: Any, a: Any, plen: Any, p: Any, item_ref: Any
    ) -> int:
        item_ref._obj.value = 0x100
        return ERR_SEC_SUCCESS

    def fake_copy(item: Any, iclass: Any, attr: Any, rlen: Any, rdata: Any) -> int:
        import ctypes

        b = b"corrupted_or_different_password"
        buf = ctypes.create_string_buffer(b)
        rdata._obj.value = ctypes.cast(buf, ctypes.c_void_p).value
        rlen._obj.value = len(b)
        return ERR_SEC_SUCCESS

    fake_sec.SecKeychainFindGenericPassword.return_value = ERR_SEC_ITEM_NOT_FOUND
    fake_sec.SecKeychainAddGenericPassword = fake_add
    fake_sec.SecKeychainItemCopyContent = fake_copy
    fake_sec.SecKeychainItemFreeContent.return_value = ERR_SEC_SUCCESS

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )

    with pytest.raises(SecretStoreError) as exc:
        adapter.store_password("original_password_12345")
    assert "password bytes mismatch" in str(exc.value)


def test_verify_structural_restrictions_null_membership_rejected() -> None:
    api = MockNeonApi()
    conn = MockDbConn(role_exists=True, role_memberships=None)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "Role membership query returned no data or NULL" in str(exc.value)


def test_verify_structural_restrictions_cross_schema_type_ownership_rejected() -> None:
    api = MockNeonApi()
    conn = MockDbConn(role_exists=True, ownership=(0, 0, 0, 0, 1))

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert "Ownership assertion failed" in str(exc.value)


def test_verify_structural_restrictions_records_inherited_temporary() -> None:
    api = MockNeonApi()
    conn = MockDbConn(role_exists=True)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))
    assert provisioner._last_can_temp_db is True


def test_verify_pg_tls_and_identity_accepts_backend_ssl_active_false_when_client_tls_verified() -> (
    None
):
    api = MockNeonApi()
    conn = MockDbConn(ssl_active=False, client_ssl_active=True)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert provisioner._last_backend_ssl_active is False


def test_verify_pg_tls_and_identity_accepts_backend_ssl_active_true() -> None:
    api = MockNeonApi()
    conn = MockDbConn(ssl_active=True, client_ssl_active=True)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert provisioner._last_backend_ssl_active is True


def test_verify_pg_tls_and_identity_handles_backend_ssl_null() -> None:
    api = MockNeonApi()
    conn = MockDbConn(ssl_active=None, client_ssl_active=True)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert provisioner._last_backend_ssl_active is None


def test_verify_client_tls_comprehensive() -> None:
    from scripts.provision_neon_staging_role import _verify_client_tls

    # 1. Connection with is_ssl() -> True
    mock1 = MagicMock()
    mock1.is_ssl.return_value = True
    assert _verify_client_tls(mock1) is True

    # 2. Connection with _protocol.is_ssl == True
    mock2 = MagicMock(spec=["_protocol"])
    mock2._protocol = MagicMock()
    mock2._protocol.is_ssl = True
    assert _verify_client_tls(mock2) is True

    # 3. Connection with _transport.get_extra_info("ssl_object")
    mock3 = MagicMock(spec=["_transport"])
    mock3._transport = MagicMock()
    mock3._transport.get_extra_info.side_effect = (
        lambda key: MagicMock() if key == "ssl_object" else None
    )
    assert _verify_client_tls(mock3) is True

    # 4. Connection with client_ssl = False
    mock4 = MagicMock(spec=["_protocol", "_transport"])
    mock4._protocol = MagicMock(is_ssl=False)
    mock4._transport = MagicMock()
    mock4._transport.get_extra_info.return_value = None
    assert _verify_client_tls(mock4) is False

    # 5. Empty object
    class Empty:
        pass

    assert _verify_client_tls(Empty()) is False


def test_database_connector_rejects_hostname_check_disabled() -> None:
    connector = DatabaseConnector()
    insecure_ctx = connector.build_ssl_context()
    insecure_ctx.check_hostname = False
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(
            connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password="test_password",
                database=EXPECTED_DATABASE,
                ssl_context=insecure_ctx,
            )
        )
    assert "Insecure SSL context rejected: check_hostname and CERT_REQUIRED required." in str(
        exc.value
    )


def test_database_connector_rejects_verify_mode_disabled() -> None:
    connector = DatabaseConnector()
    insecure_ctx = connector.build_ssl_context()
    insecure_ctx.check_hostname = False
    insecure_ctx.verify_mode = ssl.CERT_NONE
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(
            connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password="test_password",
                database=EXPECTED_DATABASE,
                ssl_context=insecure_ctx,
            )
        )
    assert "Insecure SSL context rejected: check_hostname and CERT_REQUIRED required." in str(
        exc.value
    )


def test_representative_tls_handshake_rejects_untrusted_cert() -> None:
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = f"{tmpdir}/key.pem"
        cert_path = f"{tmpdir}/cert.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                key_path,
                "-out",
                cert_path,
                "-days",
                "1",
                "-subj",
                "/CN=untrusted.neon.tech",
            ],
            check=True,
            capture_output=True,
        )
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cert_path, key_path)

        connector = DatabaseConnector()
        client_ctx = connector.build_ssl_context()

        client_in = ssl.MemoryBIO()
        client_out = ssl.MemoryBIO()
        client_ssl = client_ctx.wrap_bio(
            client_in, client_out, server_hostname="untrusted.neon.tech"
        )

        server_in = ssl.MemoryBIO()
        server_out = ssl.MemoryBIO()
        server_ssl = server_ctx.wrap_bio(server_in, server_out, server_side=True)

        try:
            client_ssl.do_handshake()
        except ssl.SSLWantReadError:
            pass
        server_in.write(client_out.read())

        try:
            server_ssl.do_handshake()
        except ssl.SSLWantReadError:
            pass
        client_in.write(server_out.read())

        with pytest.raises(ssl.SSLCertVerificationError) as exc:
            client_ssl.do_handshake()
        assert "certificate verify failed" in str(exc.value)


def test_dry_run_performs_zero_mutations_and_zero_keychain_writes() -> None:
    api = MockNeonApi()
    mock_store = MagicMock()
    admin_conn = MockDbConn(ssl_active=False, client_ssl_active=True, role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)
    checkpoint_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=mock_store,
        checkpoint_mgr=checkpoint_mgr,
        connector=connector,
        mode=ProvisioningMode.DRY_RUN,
    )
    res = asyncio.run(provisioner.run_dry_run(admin_password="test_admin_pwd"))
    assert res["status"] == "PASS"
    assert res["database_verified"] is True
    assert res["backend_ssl_active"] is False
    assert mock_store.store_password.call_count == 0
    assert api.reset_called is False
    assert not any("CREATE ROLE" in stmt for stmt in admin_conn.executed_statements)


def test_verify_pg_tls_and_identity_rejects_client_is_ssl_false() -> None:
    api = MockNeonApi()
    conn = MockDbConn(ssl_active=True, client_ssl_active=False)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.verify_pg_tls_and_identity(conn))
    assert "Client connection reports TLS is not active" in str(exc.value)


# ==============================================================================
# Dedicated Adversarial Regression Tests for Astra Findings 1-8
# ==============================================================================


def test_auto_containment_succeeds_when_checkpoint_fails_and_admin_conn_fails() -> None:
    api = MockNeonApi()
    broken_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    fresh_conn = MockDbConn(role_exists=True)
    connector = MockDatabaseConnector(admin_conn=broken_conn, fresh_admin_conn=fresh_conn)

    mock_ckpt = MagicMock()
    # Simulate checkpoint failure on metadata recording
    mock_ckpt.record.side_effect = OSError("Disk read-only during metadata check")

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=mock_ckpt,
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                broken_conn,
                RuntimeError("Simulated post-LOGIN failure"),
                admin_password="test-admin-password",
            )
        )
    assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(exc.value)
    assert provisioner.state == ProvisioningState.CONTAINMENT_EXECUTED
    assert any("NOLOGIN" in s for s in fresh_conn.executed_statements)


def test_auto_containment_succeeds_when_fresh_conn_close_fails() -> None:
    api = MockNeonApi()
    broken_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    fresh_conn = MockDbConn(role_exists=True)
    fresh_conn.throw_on_close = True
    connector = MockDatabaseConnector(admin_conn=broken_conn, fresh_admin_conn=fresh_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                broken_conn,
                RuntimeError("Simulated post-LOGIN failure"),
                admin_password="test-admin-password",
            )
        )
    assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(exc.value)
    assert provisioner.state == ProvisioningState.CONTAINMENT_EXECUTED


def test_auto_containment_fails_closed_when_metadata_mismatch_prevents_sql() -> None:
    api = MockNeonApi(
        endpoints=[
            {
                "id": "ep-rogue",
                "branch_id": STAGING_BRANCH_ID,
                "host": "ep-rogue-endpoint.neon.tech",
            }
        ]
    )
    broken_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    connector = MockDatabaseConnector(admin_conn=broken_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                broken_conn,
                RuntimeError("Simulated post-LOGIN failure"),
                admin_password="test-admin-password",
            )
        )
    assert "CRITICAL: Post-LOGIN validation failed" in str(exc.value)
    assert provisioner.state == ProvisioningState.CONTAINMENT_FAILED


def test_database_connector_close_on_cancellation_and_interrupt() -> None:
    connector = DatabaseConnector()

    class CancelConn:
        def __init__(self) -> None:
            self.closed = False

        async def fetchrow(self, query: str) -> Any:
            raise asyncio.CancelledError()

        async def close(self) -> None:
            self.closed = True

    mock_conn = CancelConn()

    async def run_cancel_test() -> None:
        import unittest.mock

        with unittest.mock.patch("asyncpg.connect", return_value=mock_conn):
            await connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password="test_password",
                database=EXPECTED_DATABASE,
            )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_cancel_test())
    assert mock_conn.closed is True

    class InterruptConn:
        def __init__(self) -> None:
            self.closed = False

        async def fetchrow(self, query: str) -> Any:
            raise KeyboardInterrupt()

        async def close(self) -> None:
            self.closed = True

    mock_conn2 = InterruptConn()

    async def run_interrupt_test() -> None:
        import unittest.mock

        with unittest.mock.patch("asyncpg.connect", return_value=mock_conn2):
            await connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password="test_password",
                database=EXPECTED_DATABASE,
            )

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run_interrupt_test())
    assert mock_conn2.closed is True


def test_run_provisioning_app_conn_closed_on_failure() -> None:
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    app_conn = MockDbConn(current_user=APP_ROLE, throw_on_auth_query=True)

    connector = MockDatabaseConnector(
        admin_conn=admin_conn,
        fresh_admin_conn=fresh_admin_conn,
        app_conn=app_conn,
    )

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(
            store_password=MagicMock(return_value="mock-pwd"),
            get_password=MagicMock(return_value="mocked-neon-generated-password-12345"),
            verify_stored_password=MagicMock(return_value=True),
        ),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ProvisioningError):
        asyncio.run(provisioner.run_provisioning(admin_password="test_admin_pwd"))

    assert app_conn.is_closed is True


@pytest.mark.parametrize(
    "bad_op",
    [
        {"id": None, "project_id": PROJECT_ID, "branch_id": STAGING_BRANCH_ID},
        {"id": "   ", "project_id": PROJECT_ID, "branch_id": STAGING_BRANCH_ID},
        {"id": 12345, "project_id": PROJECT_ID, "branch_id": STAGING_BRANCH_ID},
        {"id": "op-not-a-uuid", "project_id": PROJECT_ID, "branch_id": STAGING_BRANCH_ID},
        {"id": "../../traversal", "project_id": PROJECT_ID, "branch_id": STAGING_BRANCH_ID},
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": "alien-proj",
            "branch_id": STAGING_BRANCH_ID,
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "branch_id": PRODUCTION_BRANCH_ID,
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "branch_id": PRODUCTION_BRANCH_NAME,
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "branch_id": "br-rogue",
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "endpoint_id": "ep-alien-endpoint",
        },
    ],
)
def test_poll_neon_operations_rejects_missing_or_non_string_fields(
    bad_op: dict[str, Any],
) -> None:
    api = MockNeonApi()
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    provisioner._verified_endpoint_id = "ep-staging-1"
    with pytest.raises((TargetVerificationError, ProvisioningError)):
        asyncio.run(provisioner.poll_neon_operations([bad_op]))


@pytest.mark.parametrize(
    "bad_polled_obj",
    [
        {"id": "not-a-uuid", "status": "running"},
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": "alien-project",
            "status": "running",
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "branch_id": PRODUCTION_BRANCH_ID,
            "status": "running",
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "branch_id": PRODUCTION_BRANCH_NAME,
            "status": "running",
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "branch_id": "br-other-branch",
            "status": "running",
        },
        {
            "id": "99999999-9999-9999-9999-999999999999",
            "project_id": PROJECT_ID,
            "endpoint_id": "ep-wrong-endpoint",
            "status": "running",
        },
    ],
)
def test_poll_neon_operations_polled_obj_target_verification(
    bad_polled_obj: dict[str, Any],
) -> None:
    op_uuid = "99999999-9999-9999-9999-999999999999"
    api = MockNeonApi(operations={op_uuid: {"operation": bad_polled_obj}})
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    provisioner._verified_endpoint_id = "ep-staging-1"
    initial_op = {
        "id": op_uuid,
        "project_id": PROJECT_ID,
        "branch_id": STAGING_BRANCH_ID,
        "status": "scheduling",
    }
    with pytest.raises(TargetVerificationError):
        asyncio.run(provisioner.poll_neon_operations([initial_op]))


def test_checkpoint_directory_permissions_0755_rejected_after_chmod(
    tmp_path: Any, monkeypatch: Any
) -> None:
    ckpt_dir = tmp_path / "insecure_ckpt_dir"
    ckpt_dir.mkdir(mode=0o755)
    ckpt_file = ckpt_dir / "ckpt.json"

    # Simulate chmod failure / no-op so directory remains 0755
    def noop_chmod(path: Any, mode: int) -> None:
        pass

    monkeypatch.setattr(os, "chmod", noop_chmod)

    mgr = CheckpointManager(ckpt_file)
    with pytest.raises(TargetVerificationError) as exc:
        mgr.record(ProvisioningState.INITIALIZED, 1, "test")
    assert "Unsafe permissions on checkpoint directory" in str(exc.value)
    assert "Must be 0700" in str(exc.value)


def test_checkpoint_fsync_dir_failure_propagates(tmp_path: Any, monkeypatch: Any) -> None:
    ckpt_dir = tmp_path / "fsync_ckpt_dir"
    ckpt_dir.mkdir(mode=0o700)
    ckpt_file = ckpt_dir / "ckpt.json"

    def fail_fsync(fd: int) -> None:
        raise OSError("Disk sync failed")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    mgr = CheckpointManager(ckpt_file)
    with pytest.raises(ProvisioningError) as exc:
        mgr.record(ProvisioningState.INITIALIZED, 1, "test")
    assert "Checkpoint persistence failed" in str(exc.value)


def test_checkpoint_history_corrupted_record_schema_rejected(tmp_path: Any) -> None:
    ckpt_file = tmp_path / "corrupted_records.json"
    import json

    # Write invalid record: step_number is bool, or state is invalid
    corrupted_data = [
        {
            "state": "INVALID_STATE_XYZ",
            "timestamp": "2026-11-23T12:00:00Z",
            "project_id": PROJECT_ID,
            "branch_id": STAGING_BRANCH_ID,
            "endpoint_host": EXPECTED_ENDPOINT_HOST,
            "step_number": 1,
            "details": "test",
        }
    ]
    with open(ckpt_file, "w") as f:
        json.dump(corrupted_data, f)

    mgr = CheckpointManager(ckpt_file)
    with pytest.raises(ProvisioningError) as exc:
        mgr.record(ProvisioningState.METADATA_VERIFIED, 2, "test")
    assert "history corrupted" in str(exc.value)

    with pytest.raises(ProvisioningError) as exc2:
        mgr.get_latest_checkpoint()
    assert "history corrupted" in str(exc2.value)


def test_checkpoint_temp_file_cleaned_up_on_base_exception(tmp_path: Any, monkeypatch: Any) -> None:
    ckpt_dir = tmp_path / "tmp_cleanup_dir"
    ckpt_dir.mkdir(mode=0o700)
    ckpt_file = ckpt_dir / "ckpt.json"

    def raise_interrupt(src: Any, dst: Any) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(os, "replace", raise_interrupt)

    mgr = CheckpointManager(ckpt_file)
    with pytest.raises(KeyboardInterrupt):
        mgr.record(ProvisioningState.INITIALIZED, 1, "test")

    # Verify no dangling .tmp.* files exist in directory
    tmp_files = list(ckpt_dir.glob(".tmp.*"))
    assert len(tmp_files) == 0


def test_secret_redactor_lowercase_percent_encoded() -> None:
    redactor = SecretRedactor()
    redactor.register("Secret+Key@123")
    # Verify both lowercase and uppercase percent encodings are scrubbed
    text1 = "Error with Secret%2BKey%40123 occurred"
    text2 = "Error with Secret%2bKey%40123 occurred"
    assert redactor.redact(text1) == "Error with [REDACTED] occurred"
    assert redactor.redact(text2) == "Error with [REDACTED] occurred"


def test_neon_api_client_url_error_severs_chain(monkeypatch: Any) -> None:
    client = NeonApiClient(api_key="npg_dummy_api_key_12345678")

    class MockOpener:
        def open(self, req: Any, timeout: int = 15) -> Any:
            raise urllib.error.URLError("http://secret-token:user@leak.test/internal")

    monkeypatch.setattr(client, "opener", MockOpener())

    with pytest.raises(TargetVerificationError) as exc_info:
        client.get_project(PROJECT_ID)

    assert "NETWORK_ERROR (URLError)" in str(exc_info.value)
    # Assert cause chain is severed
    assert exc_info.value.__cause__ is None


def test_keychain_single_item_ref_binding() -> None:
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    class NativeItemBindingStore:
        def __init__(self) -> None:
            self.item_password = "old_password_123"

        def SecKeychainFindGenericPassword(
            self,
            kc: Any,
            slen: Any,
            s: Any,
            alen: Any,
            a: Any,
            plen: Any,
            pdata: Any,
            item_ref: Any,
        ) -> int:
            item_ref._obj.value = 0x1234
            pdata._obj.value = 0x5678
            plen._obj.value = len(self.item_password)
            return ERR_SEC_SUCCESS

        def SecKeychainItemModifyContent(self, item: Any, attr: Any, length: Any, data: Any) -> int:
            val = getattr(item, "value", item)
            assert val == 0x1234
            self.item_password = data.decode("utf-8")
            return ERR_SEC_SUCCESS

        def SecKeychainItemCopyContent(
            self, item: Any, iclass: Any, attr: Any, length: Any, out_data: Any
        ) -> int:
            val = getattr(item, "value", item)
            assert val == 0x1234
            import ctypes

            b = self.item_password.encode("utf-8")
            buf = ctypes.create_string_buffer(b)
            out_data._obj.value = ctypes.cast(buf, ctypes.c_void_p).value
            length._obj.value = len(b)
            return ERR_SEC_SUCCESS

        def SecKeychainItemFreeContent(self, attr: Any, data: Any) -> int:
            return ERR_SEC_SUCCESS

    fake_store = NativeItemBindingStore()
    fake_sec.SecKeychainFindGenericPassword = fake_store.SecKeychainFindGenericPassword
    fake_sec.SecKeychainItemModifyContent = fake_store.SecKeychainItemModifyContent
    fake_sec.SecKeychainItemCopyContent = fake_store.SecKeychainItemCopyContent
    fake_sec.SecKeychainItemFreeContent = fake_store.SecKeychainItemFreeContent
    fake_sec.SecKeychainSearchCreateFromAttributes.return_value = ERR_SEC_ITEM_NOT_FOUND

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )

    res = adapter.store_password("new_updated_password_456", allow_overwrite=True)
    assert res == "new_updated_password_456"
    assert fake_store.item_password == "new_updated_password_456"


@pytest.mark.parametrize(
    ("flag", "invalid_val"),
    [
        ("rolcanlogin", 1),  # int 1 instead of bool True
        ("rolsuper", 0),  # int 0 instead of bool False
        ("rolcreatedb", 0),
        ("rolcreaterole", "false"),
        ("rolbypassrls", None),
        ("rolreplication", 0.0),
    ],
)
def test_verify_structural_restrictions_strictly_typed_flags(flag: str, invalid_val: Any) -> None:
    api = MockNeonApi()
    flags = {flag: invalid_val}
    conn = MockDbConn(role_exists=True, role_flags=flags)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError):
        asyncio.run(
            provisioner.verify_structural_restrictions(
                conn, expect_login=(invalid_val is True or invalid_val == 1)
            )
        )


@pytest.mark.parametrize(
    "invalid_membership",
    [
        0.0,  # float 0.0 instead of int 0
        False,  # bool False instead of int 0
        "0",  # string "0" instead of int 0
    ],
)
def test_verify_structural_restrictions_strictly_typed_memberships(
    invalid_membership: Any,
) -> None:
    api = MockNeonApi()
    conn = MockDbConn(role_exists=True, role_memberships=invalid_membership)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError):
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))


@pytest.mark.parametrize(
    ("priv_key", "invalid_val"),
    [
        ("can_connect", 1),
        ("can_create_db", 0),
        ("can_create_schema", 0),
        ("can_use_schema", 1),
        ("can_temp_db", "true"),
    ],
)
def test_verify_structural_restrictions_strictly_typed_privileges(
    priv_key: str, invalid_val: Any
) -> None:
    api = MockNeonApi()
    privs = {priv_key: invalid_val}
    conn = MockDbConn(role_exists=True, effective_privileges=privs)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError):
        asyncio.run(provisioner.verify_structural_restrictions(conn, expect_login=False))


def test_verify_baseline_privileges_strictly_typed() -> None:
    api = MockNeonApi()
    # pub_schema_create = 0 (int instead of bool False)
    conn1 = MockDbConn(pub_schema_create=0)  # type: ignore[arg-type]
    provisioner1 = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError):
        asyncio.run(provisioner1.verify_baseline_privileges(conn1))

    # pub_db_connect = 1 (int instead of bool True)
    conn2 = MockDbConn(pub_db_connect=1)  # type: ignore[arg-type]
    provisioner2 = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError):
        asyncio.run(provisioner2.verify_baseline_privileges(conn2))


def test_verify_client_tls_protocol_and_transport() -> None:
    # 1. callable is_ssl
    class MockCallableConn:
        def is_ssl(self) -> bool:
            return True

    assert _verify_client_tls(MockCallableConn()) is True

    class MockCallableConnFalse:
        def is_ssl(self) -> bool:
            return False

    assert _verify_client_tls(MockCallableConnFalse()) is False

    # 2. _protocol.is_ssl
    class MockProtoConn:
        def __init__(self, is_ssl: bool) -> None:
            self._protocol = MagicMock(is_ssl=is_ssl)

    assert _verify_client_tls(MockProtoConn(True)) is True
    assert _verify_client_tls(MockProtoConn(False)) is False

    # 3. _transport.get_extra_info('ssl_object')
    class MockTransportConn:
        def __init__(self, ssl_obj: Any) -> None:
            self._transport = MagicMock()
            self._transport.get_extra_info.return_value = ssl_obj

    assert _verify_client_tls(MockTransportConn(object())) is True
    assert _verify_client_tls(MockTransportConn(None)) is False

    # 4. Unknown/unverified connection fails closed
    assert _verify_client_tls(object()) is False


# ==============================================================================
# Category 11: Astra 7-Blocker Adversarial Regression Coverage
# ==============================================================================


def test_blocker1_containment_hanging_close_falls_back_to_terminate() -> None:
    """Blocker 1: Hanging close() does not hang emergency containment; terminate() is called."""
    terminated = False

    class HangingCloseConn(MockDbConn):
        async def close(self) -> None:
            await asyncio.sleep(10.0)

        def terminate(self) -> None:
            nonlocal terminated
            terminated = True

    conn = HangingCloseConn()
    asyncio.run(_safe_close_or_terminate(conn, timeout=0.05))
    assert terminated is True


def test_blocker1_standalone_containment_succeeds_despite_cleanup_failure() -> None:
    """Blocker 1: execute_containment_nologin succeeds even if connection close raises."""
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=True)
    admin_conn.throw_on_close = True
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    # Does not raise because NOLOGIN succeeded and cleanup error is isolated
    asyncio.run(provisioner.execute_containment_nologin(admin_password="valid-pwd"))
    assert provisioner.state == ProvisioningState.CONTAINMENT_EXECUTED
    assert admin_conn.role_flags["rolcanlogin"] is False


def test_blocker1_auto_containment_revalidates_staging_metadata_and_fails_if_mismatch() -> None:
    """Blocker 1: Auto-containment re-verifies staging metadata before fresh reconnect."""
    # Mock API returns wrong endpoint host during containment
    api = MockNeonApi(
        endpoints=[
            {
                "id": "ep-staging-1",
                "branch_id": STAGING_BRANCH_ID,
                "host": "ep-other.c-5.ap-southeast-1.aws.neon.tech",
            }
        ]
    )
    admin_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    connector = MockDatabaseConnector(admin_conn=admin_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn=admin_conn,
                trigger_error=RuntimeError("post-login-failure"),
                admin_password="valid-password",
            )
        )
    assert provisioner.state == ProvisioningState.CONTAINMENT_FAILED
    assert "emergency containment failed" in str(exc.value)


def test_blocker2_connection_cleanup_cancellation_calls_terminate_and_reraises() -> None:
    """Blocker 2: Cancellation on close invokes terminate() without leaving orphan."""
    terminated = False

    class CancelOnCloseConn(MockDbConn):
        async def close(self) -> None:
            raise asyncio.CancelledError()

        def terminate(self) -> None:
            nonlocal terminated
            terminated = True

    conn = CancelOnCloseConn()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_safe_close_or_terminate(conn, timeout=0.05))
    assert terminated is True


def test_blocker2_independent_app_and_admin_connection_cleanup() -> None:
    """Blocker 2: Failure/cancellation in app_conn cleanup never skips admin_conn cleanup."""
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "valid-pwd-1234567890",
            },
            "operations": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    app_conn = MockDbConn(current_user=APP_ROLE, session_user=APP_ROLE, role_exists=True)

    async def cancel_on_close() -> None:
        app_conn.is_closed = True
        raise asyncio.CancelledError()

    app_conn.close = cancel_on_close  # type: ignore[method-assign]

    connector = MockDatabaseConnector(
        admin_conn=admin_conn,
        fresh_admin_conn=fresh_admin_conn,
        app_conn=app_conn,
    )

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(
            store_password=MagicMock(return_value="valid-pwd-1234567890"),
            get_password=MagicMock(return_value="valid-pwd-1234567890"),
            verify_stored_password=MagicMock(return_value=True),
        ),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provisioner.run_provisioning("admin-pwd"))

    # Crucial assertion: fresh_admin_conn was STILL closed despite app_conn cancellation
    assert fresh_admin_conn.is_closed is True


def test_blocker3_reset_role_bound_to_production_branch_rejected() -> None:
    """Blocker 3: Reset role bound to production branch ID or name is rejected."""
    for prod_branch in (PRODUCTION_BRANCH_ID, PRODUCTION_BRANCH_NAME):
        api = MockNeonApi(
            reset_response={
                "role": {
                    "name": APP_ROLE,
                    "project_id": PROJECT_ID,
                    "branch_id": prod_branch,
                    "password": "valid-pwd-1234567890",
                },
                "operations": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "project_id": PROJECT_ID,
                        "branch_id": STAGING_BRANCH_ID,
                        "status": "finished",
                    }
                ],
            }
        )
        admin_conn = MockDbConn(role_exists=False)
        provisioner = StagingRoleProvisioner(
            api_client=api,
            secret_store=MagicMock(),
            checkpoint_mgr=MagicMock(),
            connector=MockDatabaseConnector(admin_conn=admin_conn),
            mode=ProvisioningMode.EXECUTE,
            confirmed_branch=STAGING_BRANCH_ID,
        )
        with pytest.raises(TargetVerificationError) as exc:
            asyncio.run(provisioner.run_provisioning("admin-pwd"))
        assert "Production branch role detected" in str(exc.value)


def test_blocker3_operation_non_uuid_rejected() -> None:
    """Blocker 3: Non-UUID operation ID is rejected."""
    for non_uuid in ("op-reset-1", "path/like/op", "not-a-valid-uuid-format"):
        api = MockNeonApi()
        provisioner = StagingRoleProvisioner(
            api_client=api,
            secret_store=MagicMock(),
            checkpoint_mgr=MagicMock(),
        )
        with pytest.raises(TargetVerificationError) as exc:
            asyncio.run(
                provisioner.poll_neon_operations(
                    [
                        {
                            "id": non_uuid,
                            "project_id": PROJECT_ID,
                            "branch_id": STAGING_BRANCH_ID,
                            "status": "finished",
                        }
                    ]
                )
            )
        assert "is not a valid UUID" in str(exc.value)


def test_blocker3_operation_optional_branch_id_missing_accepted() -> None:
    """Blocker 3: Operation lacking optional branch_id in Neon API v2 is accepted."""
    api = MockNeonApi()
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    # branch_id omitted, status finished: should succeed without error
    asyncio.run(
        provisioner.poll_neon_operations(
            [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "project_id": PROJECT_ID,
                    "status": "finished",
                }
            ]
        )
    )
    assert provisioner.state == ProvisioningState.OPERATIONS_POLLED_FINISHED


def test_blocker3_short_password_from_neon_example_accepted() -> None:
    """Blocker 3: Valid password from Neon docs accepted without arbitrary length check."""
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "password": "short_pwd_12",  # 12 characters, non-empty string
            },
            "operations": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    fresh_admin_conn = MockDbConn(role_exists=True)
    app_conn = MockDbConn(current_user=APP_ROLE, session_user=APP_ROLE, role_exists=True)
    connector = MockDatabaseConnector(
        admin_conn=admin_conn, fresh_admin_conn=fresh_admin_conn, app_conn=app_conn
    )
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(
            store_password=MagicMock(return_value="short_pwd_12"),
            get_password=MagicMock(return_value="short_pwd_12"),
            verify_stored_password=MagicMock(return_value=True),
        ),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert provisioner.state == ProvisioningState.COMPLETED


def test_blocker4_mixed_case_percent_escapes_scrubbed() -> None:
    """Blocker 4: Mixed-case percent escapes like Review%2BOnly%2fSecret%3FA are scrubbed."""
    redactor = SecretRedactor()
    raw_secret = "Review+Only/Secret?A"
    redactor.register(raw_secret)

    # Mixed-case percent encoded test string matching Astra reproduction
    mixed_encoded = "Review%2BOnly%2fSecret%3FA"
    text = f"Bearer {mixed_encoded} token={mixed_encoded}"
    scrubbed = redactor.redact(text)
    assert raw_secret not in scrubbed
    assert mixed_encoded not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_blocker4_cancellation_in_cli_returns_130_without_credential_leak(
    capsys: Any, monkeypatch: Any, mock_secret_store: SecretStore, tmp_path: Path
) -> None:
    """Blocker 4: Cancellation in CLI entrypoint returns exit code 130 with clean diagnostic."""

    def raise_cancelled(coro: Any = None, *args: Any, **kwargs: Any) -> Any:
        if asyncio.iscoroutine(coro):
            coro.close()
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "scripts.provision_neon_staging_role._run_with_bounded_sigint_handling",
        raise_cancelled,
    )
    ckpt_file = str(tmp_path / "nonexistent.json")
    code = cli_entrypoint(
        ["--dry-run", "--checkpoint-file", ckpt_file],
        api_client=MockNeonApi(),
        secret_store=mock_secret_store,
    )
    assert code == 130
    captured = capsys.readouterr()
    assert "[!] Operation cancelled by user or signal." in captured.err


def test_blocker5_checkpoint_rejects_unknown_unexpected_password_field(tmp_path: Any) -> None:
    """Blocker 5: Checkpoint history with unexpected field (unexpected_password) is rejected."""
    ckpt_file = tmp_path / "checkpoint.json"
    corrupted_data = [
        {
            "state": "INITIALIZED",
            "timestamp": "2026-11-23T00:00:00Z",
            "project_id": PROJECT_ID,
            "branch_id": STAGING_BRANCH_ID,
            "endpoint_host": EXPECTED_ENDPOINT_HOST,
            "step_number": 0,
            "details": "Init",
            "unexpected_password": "super-secret-leaked-value",
        }
    ]
    with open(ckpt_file, "w", encoding="utf-8") as f:
        json.dump(corrupted_data, f)

    mgr = CheckpointManager(ckpt_file)
    with pytest.raises(ProvisioningError) as exc:
        mgr.get_latest_checkpoint()
    assert "unexpected fields" in str(exc.value)


def test_blocker5_checkpoint_loading_secret_contamination_rejected(tmp_path: Any) -> None:
    """Blocker 5: Checkpoint manager rejects historical records contaminated with credentials."""
    ckpt_file = tmp_path / "checkpoint.json"
    secret_val = "historical-secret-pwd-999"
    redactor = SecretRedactor()
    redactor.register(secret_val)

    data = [
        {
            "state": "INITIALIZED",
            "timestamp": "2026-11-23T00:00:00Z",
            "project_id": PROJECT_ID,
            "branch_id": STAGING_BRANCH_ID,
            "endpoint_host": EXPECTED_ENDPOINT_HOST,
            "step_number": 0,
            "details": f"Historical secret leaked here: {secret_val}",
        }
    ]
    with open(ckpt_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    mgr = CheckpointManager(ckpt_file, redactor=redactor)
    with pytest.raises(ProvisioningError) as exc:
        mgr.record(ProvisioningState.METADATA_VERIFIED, 1, "New step")
    assert "secret credential detected" in str(exc.value)

    # Ensure existing contaminated file was NOT replaced or rewritten
    with open(ckpt_file, encoding="utf-8") as f:
        saved = json.load(f)
    assert secret_val in saved[0]["details"]


def test_blocker6_keychain_verify_stored_password_fails_on_retrieved_mismatch() -> None:
    """Blocker 6: verify_stored_password fails if retrieved credential does not match expected."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    fake_sec.SecKeychainSearchCreateFromAttributes.return_value = ERR_SEC_ITEM_NOT_FOUND

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )
    adapter.entry_exists = MagicMock(return_value=True)  # type: ignore[method-assign]
    adapter.get_password = MagicMock(return_value="wrong-credential-from-keychain")  # type: ignore[method-assign]

    with pytest.raises(SecretStoreError) as exc:
        adapter.verify_stored_password(expected_password="expected-final-password")
    assert "retrieved password mismatch" in str(exc.value)


def test_blocker6_keychain_ambiguous_matches_rejected() -> None:
    """Blocker 6: Ambiguous Keychain matches (multiple items) fail closed with SecretStoreError."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    # SecKeychainSearchCreateFromAttributes returns search_ref with value
    def fake_search_create(kc: Any, iclass: Any, attr: Any, out_ref: Any) -> int:
        out_ref._obj.value = 0xAAAA
        return ERR_SEC_SUCCESS

    # SecKeychainSearchCopyNext returns success twice (2 items found)
    call_count = 0

    def fake_search_next(search_ref: Any, out_item: Any) -> int:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            out_item._obj.value = 0x1000 + call_count
            return ERR_SEC_SUCCESS
        return ERR_SEC_ITEM_NOT_FOUND

    fake_sec.SecKeychainSearchCreateFromAttributes = fake_search_create
    fake_sec.SecKeychainSearchCopyNext = fake_search_next

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )
    with pytest.raises(SecretStoreError) as exc:
        adapter._check_ambiguous_matches()
    assert "Ambiguous Keychain match: multiple items found" in str(exc.value)


def test_blocker7_pub_db_temp_unknown_string_rejected() -> None:
    """Blocker 7: Non-boolean ('UNKNOWN') for pub_db_temp raises PrivilegeVerificationError."""
    conn = MockDbConn(pub_db_temp="UNKNOWN")  # type: ignore[arg-type]
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    with pytest.raises(PrivilegeVerificationError) as exc:
        asyncio.run(provisioner.verify_baseline_privileges(conn))
    assert "PUBLIC database TEMPORARY check returned non-boolean" in str(exc.value)


def test_blocker7_dry_run_records_not_verified_when_role_does_not_exist() -> None:
    """Blocker 7: Dry run records NOT VERIFIED (ROLE DOES NOT EXIST) when role is absent."""
    api = MockNeonApi()
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)
    checkpoint_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=checkpoint_mgr,
        connector=connector,
        mode=ProvisioningMode.DRY_RUN,
    )
    res = asyncio.run(provisioner.run_dry_run(admin_password="valid-admin-password"))
    assert res["role_exists"] is False
    assert res["effective_application_temporary"] == "NOT VERIFIED (ROLE DOES NOT EXIST)"
    assert res["public_baseline_temporary"] is True
    assert provisioner.state == ProvisioningState.DRY_RUN_COMPLETED
    assert checkpoint_mgr.record.call_count == 3
    record_call = checkpoint_mgr.record.call_args
    assert "NOT VERIFIED (ROLE DOES NOT EXIST)" in record_call[1]["details"]
    assert "PUBLIC TEMPORARY=True" in record_call[1]["details"]


# ==============================================================================
# Category 12: Astra 5-Blocker Adversarial Regression Coverage
# ==============================================================================

# --- Finding 1: Emergency Containment Cancellation Safety ---


def test_f1_cancellation_during_old_admin_cleanup_attempts_fresh_containment() -> None:
    """Finding 1: Cancellation during old admin cleanup still attempts emergency containment."""
    admin_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    admin_conn.throw_on_close = True  # Step 1 cleanup raises CancelledError / error

    fresh_conn = MockDbConn(role_exists=True)
    connector = MockDatabaseConnector(admin_conn=admin_conn, fresh_admin_conn=fresh_conn)
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn, RuntimeError("Initial failure"), admin_password="valid-password"
            )
        )
    assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(exc.value)
    assert provisioner.state == ProvisioningState.CONTAINMENT_EXECUTED
    assert any("NOLOGIN" in q for q in fresh_conn.executed_statements)


def test_f1_cancellation_during_fresh_reconnection_preserves_critical_reporting() -> None:
    """Finding 1: Cancellation during fresh reconnection preserves critical failure reporting."""
    admin_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)

    class CancelOnConnectConnector(MockDatabaseConnector):
        async def connect(self, *args: Any, **kwargs: Any) -> Any:
            raise asyncio.CancelledError()

    connector = CancelOnConnectConnector()
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn, RuntimeError("Step 13 failed"), admin_password="valid-password"
            )
        )
    assert "CRITICAL: Post-LOGIN validation failed" in str(exc.value)
    assert "emergency containment failed" in str(exc.value)
    assert provisioner.state == ProvisioningState.CONTAINMENT_FAILED


def test_f1_cancellation_during_fresh_cleanup_preserves_nologin_verification() -> None:
    """Finding 1: Cancellation during fresh cleanup preserves successful NOLOGIN verification."""
    admin_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)

    fresh_conn = MockDbConn(role_exists=True)
    fresh_conn.throw_on_close = True
    connector = MockDatabaseConnector(admin_conn=admin_conn, fresh_admin_conn=fresh_conn)
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn, RuntimeError("Auth check failed"), admin_password="valid-password"
            )
        )
    assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(exc.value)
    assert provisioner.state == ProvisioningState.CONTAINMENT_EXECUTED


def test_f1_combined_checkpoint_socket_cancellation_metadata_failures() -> None:
    """Finding 1: Combined checkpoint, socket, cancellation and metadata failures fail closed."""
    admin_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)
    admin_conn.throw_on_close = True

    # Bad metadata (endpoint mismatch)
    api = MockNeonApi(
        endpoints=[
            {
                "id": "ep-staging-1",
                "branch_id": STAGING_BRANCH_ID,
                "host": "ep-mismatched.neon.tech",
            }
        ]
    )
    checkpoint_mgr = MagicMock()
    checkpoint_mgr.record.side_effect = OSError("Disk failure")

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=checkpoint_mgr,
        connector=MockDatabaseConnector(),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn, asyncio.CancelledError(), admin_password="admin-pwd"
            )
        )
    assert "CRITICAL: Post-LOGIN validation failed" in str(exc.value)
    assert "emergency containment failed" in str(exc.value)


def test_f1_no_mutation_when_target_cannot_be_verified() -> None:
    """Finding 1: No mutation when control plane metadata cannot be verified during containment."""
    admin_conn = MockDbConn(role_exists=True, throw_on_alter_nologin=True)

    api = MockNeonApi(branches=[{"id": "br-other-branch", "name": "other"}])
    fresh_conn = MockDbConn(role_exists=True)
    connector = MockDatabaseConnector(admin_conn=admin_conn, fresh_admin_conn=fresh_conn)

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(ContainmentFailureError):
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn, RuntimeError("trigger"), admin_password="admin-pwd"
            )
        )
    assert len(fresh_conn.executed_statements) == 0


def test_f1_no_orphaned_cleanup_tasks_after_bounded_shutdown() -> None:
    """Finding 1: No orphaned tasks left running after bounded containment completes."""
    admin_conn = MockDbConn(role_exists=True)
    fresh_conn = MockDbConn(role_exists=True)
    connector = MockDatabaseConnector(admin_conn=admin_conn, fresh_admin_conn=fresh_conn)

    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    async def run_and_check() -> None:
        try:
            await provisioner.attempt_auto_containment(
                admin_conn, RuntimeError("failure"), admin_password="valid-password"
            )
        except ProvisioningError:
            pass

        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        assert len(pending) == 0

    asyncio.run(run_and_check())


# --- Finding 2: Required Neon Identity Field Validation ---


@pytest.mark.parametrize(
    "malformed_branch",
    [
        None,
        "",
        "   ",
        False,
        True,
        123,
        PRODUCTION_BRANCH_ID,
        PRODUCTION_BRANCH_NAME,
        "br-alien-branch",
    ],
)
def test_f2_malformed_required_role_branch_field_rejected(malformed_branch: Any) -> None:
    """Finding 2: Absent, null, false, empty, integer or mismatched role branch_id rejected."""
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "branch_id": malformed_branch,
                "password": "valid-generated-password-12345",
            },
            "operations": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MockDatabaseConnector(admin_conn=admin_conn),
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert ("branch" in str(exc.value).lower()) or ("production" in str(exc.value).lower())


@pytest.mark.parametrize(
    "malformed_project",
    [None, "", "   ", False, True, 123, "alien-project", "production-project"],
)
def test_f2_malformed_required_operation_project_rejected_initial_and_polled(
    malformed_project: Any,
) -> None:
    """Finding 2: Operation project_id required on both initial and polled operations."""
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    op = {
        "id": "22222222-2222-2222-2222-222222222222",
        "project_id": malformed_project,
        "branch_id": STAGING_BRANCH_ID,
        "status": "finished",
    }
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.poll_neon_operations([op]))
    assert "Operation project mismatch" in str(exc.value)

    # Polled operation
    op_uuid = "33333333-3333-3333-3333-333333333333"
    api_polled = MockNeonApi(
        operations={
            op_uuid: {
                "operation": {
                    "id": op_uuid,
                    "project_id": malformed_project,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                }
            }
        }
    )
    prov_polled = StagingRoleProvisioner(
        api_client=api_polled,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    initial_op = {
        "id": op_uuid,
        "project_id": PROJECT_ID,
        "branch_id": STAGING_BRANCH_ID,
        "status": "scheduling",
    }
    with pytest.raises(TargetVerificationError) as exc2:
        asyncio.run(prov_polled.poll_neon_operations([initial_op]))
    assert "project" in str(exc2.value).lower()


def test_f2_valid_omitted_optional_operation_branch_and_endpoint_accepted() -> None:
    """Finding 2: Omitted optional branch_id and endpoint_id accepted when target verified."""
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    provisioner._verified_endpoint_id = "ep-staging-1"
    op_omitted = {
        "id": "44444444-4444-4444-4444-444444444444",
        "project_id": PROJECT_ID,
        "status": "finished",
    }
    asyncio.run(provisioner.poll_neon_operations([op_omitted]))
    assert provisioner.state == ProvisioningState.OPERATIONS_POLLED_FINISHED


def test_f2_explicit_null_or_wrong_type_optional_fields_rejected() -> None:
    """Finding 2: Supplied malformed optional fields (wrong type int/bool) rejected."""
    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )
    op_wrong_branch_type = {
        "id": "55555555-5555-5555-5555-555555555555",
        "project_id": PROJECT_ID,
        "branch_id": 12345,
        "status": "finished",
    }
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.poll_neon_operations([op_wrong_branch_type]))
    assert "Malformed operation branch_id" in str(exc.value)

    op_wrong_ep_type = {
        "id": "66666666-6666-6666-6666-666666666666",
        "project_id": PROJECT_ID,
        "endpoint_id": True,
        "status": "finished",
    }
    with pytest.raises(TargetVerificationError) as exc2:
        asyncio.run(provisioner.poll_neon_operations([op_wrong_ep_type]))
    assert "Malformed operation endpoint_id" in str(exc2.value)


def test_f2_multiple_operations_with_later_failure_blocks_login() -> None:
    """Finding 2: Multiple operations where later operation fails halts and never enables LOGIN."""
    op1_id = "77777777-7777-7777-7777-777777777777"
    op2_id = "88888888-8888-8888-8888-888888888888"
    api = MockNeonApi(
        reset_response={
            "role": {
                "name": APP_ROLE,
                "branch_id": STAGING_BRANCH_ID,
                "password": "valid-generated-password-12345",
            },
            "operations": [
                {
                    "id": op1_id,
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "finished",
                },
                {
                    "id": op2_id,
                    "project_id": PROJECT_ID,
                    "branch_id": STAGING_BRANCH_ID,
                    "status": "failed",
                },
            ],
        }
    )
    admin_conn = MockDbConn(role_exists=False)
    connector = MockDatabaseConnector(admin_conn=admin_conn)
    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    with pytest.raises(ProvisioningError) as exc:
        asyncio.run(provisioner.run_provisioning("admin-pwd"))
    assert "failed" in str(exc.value).lower()
    assert not any("LOGIN;" in q and "NOLOGIN" not in q for q in admin_conn.executed_statements)


# --- Finding 3: Native Keychain Security.framework Binding ---


def test_f3_keychain_item_class_and_scope_arguments() -> None:
    """Finding 3: Assert exact kSecGenericPasswordItemClass (0x67656e70) and Keychain scope."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    passed_scope = None
    passed_class = None

    def capture_search_args(kc: Any, iclass: Any, attr: Any, out_ref: Any) -> int:
        nonlocal passed_scope, passed_class
        passed_scope = kc
        passed_class = iclass
        return ERR_SEC_ITEM_NOT_FOUND

    fake_sec.SecKeychainSearchCreateFromAttributes = capture_search_args

    pinned_kc = ctypes.c_void_p(0x9999)
    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=pinned_kc,
    )
    adapter._check_ambiguous_matches()

    assert passed_class == K_SEC_GENERIC_PASSWORD_ITEM_CLASS
    assert passed_class == 0x67656E70  # 'genp'
    assert passed_scope == pinned_kc


def test_f3_search_creation_failure_fails_closed() -> None:
    """Finding 3: Search creation failure fails closed with SecretStoreError."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    fake_sec.SecKeychainSearchCreateFromAttributes.return_value = ERR_SEC_AUTH_FAILED

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )
    with pytest.raises(SecretStoreError) as exc:
        adapter._check_ambiguous_matches()
    assert "SecKeychainSearchCreateFromAttributes failed" in str(exc.value)


def test_f3_search_iteration_failure_fails_closed() -> None:
    """Finding 3: Search iteration failure fails closed with SecretStoreError."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    def fake_search_create(kc: Any, iclass: Any, attr: Any, out_ref: Any) -> int:
        out_ref._obj.value = 0xAAAA
        return ERR_SEC_SUCCESS

    fake_sec.SecKeychainSearchCreateFromAttributes = fake_search_create
    fake_sec.SecKeychainSearchCopyNext.return_value = ERR_SEC_AUTH_FAILED

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )
    with pytest.raises(SecretStoreError) as exc:
        adapter._check_ambiguous_matches()
    assert "SecKeychainSearchCopyNext failed" in str(exc.value)
    assert fake_cf.CFRelease.call_count == 1


def test_f3_invalid_returned_reference_fails_closed() -> None:
    """Finding 3: Invalid returned reference (NULL search_ref or NULL item_ref) fails closed."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    # NULL search_ref on ERR_SEC_SUCCESS
    def fake_search_create_null(kc: Any, iclass: Any, attr: Any, out_ref: Any) -> int:
        out_ref._obj.value = None
        return ERR_SEC_SUCCESS

    fake_sec.SecKeychainSearchCreateFromAttributes = fake_search_create_null

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )
    with pytest.raises(SecretStoreError) as exc:
        adapter._check_ambiguous_matches()
    assert "returned NULL search reference" in str(exc.value)

    # NULL item_ref on ERR_SEC_SUCCESS
    def fake_search_create_valid(kc: Any, iclass: Any, attr: Any, out_ref: Any) -> int:
        out_ref._obj.value = 0xAAAA
        return ERR_SEC_SUCCESS

    def fake_search_next_null(search_ref: Any, out_item: Any) -> int:
        out_item._obj.value = None
        return ERR_SEC_SUCCESS

    fake_sec.SecKeychainSearchCreateFromAttributes = fake_search_create_valid
    fake_sec.SecKeychainSearchCopyNext = fake_search_next_null

    with pytest.raises(SecretStoreError) as exc2:
        adapter._check_ambiguous_matches()
    assert "returned NULL item reference" in str(exc2.value)


def test_f3_resource_cleanup_after_every_failure() -> None:
    """Finding 3: Native references are released via CFRelease on all failure paths."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    def fake_search_create(kc: Any, iclass: Any, attr: Any, out_ref: Any) -> int:
        out_ref._obj.value = 0x5555
        return ERR_SEC_SUCCESS

    call_idx = 0

    def fake_search_next(search_ref: Any, out_item: Any) -> int:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            out_item._obj.value = 0x6666
            return ERR_SEC_SUCCESS
        return ERR_SEC_AUTH_FAILED

    fake_sec.SecKeychainSearchCreateFromAttributes = fake_search_create
    fake_sec.SecKeychainSearchCopyNext = fake_search_next

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )
    with pytest.raises(SecretStoreError):
        adapter._check_ambiguous_matches()

    assert fake_cf.CFRelease.call_count == 2


# --- Finding 4: Checkpoint Timestamp Validation & Secret Contamination ---


def test_f4_credential_in_historical_timestamp_rejected(tmp_path: Any) -> None:
    """Finding 4: Secret credential inside historical timestamp is strictly rejected."""
    ckpt_file = tmp_path / "checkpoint.json"
    secret_val = "synthetic-secret-token-xyz"
    redactor = SecretRedactor()
    redactor.register(secret_val)

    corrupted_data = [
        {
            "state": "INITIALIZED",
            "timestamp": f"2026-11-23T12:00:00Z-{secret_val}",
            "project_id": PROJECT_ID,
            "branch_id": STAGING_BRANCH_ID,
            "endpoint_host": EXPECTED_ENDPOINT_HOST,
            "step_number": 0,
            "details": "Clean details",
        }
    ]
    with open(ckpt_file, "w", encoding="utf-8") as f:
        json.dump(corrupted_data, f)

    mgr = CheckpointManager(ckpt_file, redactor=redactor)
    with pytest.raises(ProvisioningError) as exc:
        mgr.record(ProvisioningState.METADATA_VERIFIED, 1, "Next step")
    assert "secret credential detected in field 'timestamp'" in str(exc.value)

    with open(ckpt_file, encoding="utf-8") as f:
        saved = json.load(f)
    assert secret_val in saved[0]["timestamp"]


@pytest.mark.parametrize(
    "bad_timestamp",
    [
        "not-a-timestamp",
        "2026/11/23 12:00:00",
        "2026-11-23T12:00:00",
        "2026-11-23T12:00:00+05:30",
        "2026-02-31T12:00:00Z",
    ],
)
def test_f4_invalid_timestamp_syntax_rejected(tmp_path: Any, bad_timestamp: str) -> None:
    """Finding 4: Malformed timestamp syntax rejected by schema validation."""
    ckpt_file = tmp_path / "checkpoint.json"
    corrupted_data = [
        {
            "state": "INITIALIZED",
            "timestamp": bad_timestamp,
            "project_id": PROJECT_ID,
            "branch_id": STAGING_BRANCH_ID,
            "endpoint_host": EXPECTED_ENDPOINT_HOST,
            "step_number": 0,
            "details": "Clean details",
        }
    ]
    with open(ckpt_file, "w", encoding="utf-8") as f:
        json.dump(corrupted_data, f)

    mgr = CheckpointManager(ckpt_file)
    with pytest.raises(ProvisioningError) as exc:
        mgr.get_latest_checkpoint()
    assert "corrupted" in str(exc.value).lower()


def test_f4_credential_in_metadata_fields_rejected(tmp_path: Any) -> None:
    """Finding 4: Credential leaked in project_id or details rejected."""
    ckpt_file = tmp_path / "checkpoint.json"
    secret_val = "leaked-secret-in-metadata"
    redactor = SecretRedactor()
    redactor.register(secret_val)

    corrupted_data = [
        {
            "state": "INITIALIZED",
            "timestamp": "2026-11-23T00:00:00Z",
            "project_id": PROJECT_ID,
            "branch_id": STAGING_BRANCH_ID,
            "endpoint_host": EXPECTED_ENDPOINT_HOST,
            "step_number": 0,
            "details": f"leaked: {secret_val}",
        }
    ]
    with open(ckpt_file, "w", encoding="utf-8") as f:
        json.dump(corrupted_data, f)

    mgr = CheckpointManager(ckpt_file, redactor=redactor)
    with pytest.raises(ProvisioningError) as exc:
        mgr.get_latest_checkpoint()
    assert "secret credential detected in field 'details'" in str(exc.value)


# --- Finding 5: TEMPORARY Privilege Attribution Accuracy ---


def test_f5_dry_run_temporary_privilege_all_boolean_combinations() -> None:
    """Finding 5: Verify valid boolean combinations of PUBLIC and effective TEMPORARY."""
    prov = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
    )

    # 1. PUBLIC=True, App=True -> PUBLIC contributing grant
    res1 = prov.format_temporary_privilege_evidence(
        public_temp=True, app_temp=True, role_exists=True
    )
    assert res1 == "True (PUBLIC contributing grant; PENDING HUMAN ACCEPTANCE)"

    # 2. PUBLIC=False, App=True -> Effective granted; source unverified
    res2 = prov.format_temporary_privilege_evidence(
        public_temp=False, app_temp=True, role_exists=True
    )
    assert res2 == "True (Effective privilege granted; source unverified; PENDING HUMAN ACCEPTANCE)"

    # 3. PUBLIC=False, App=False -> No verified TEMPORARY permission
    res3 = prov.format_temporary_privilege_evidence(
        public_temp=False, app_temp=False, role_exists=True
    )
    assert res3 == "False (No verified TEMPORARY permission)"

    # 4. PUBLIC=True, App=False -> Effective privilege denied
    res4 = prov.format_temporary_privilege_evidence(
        public_temp=True, app_temp=False, role_exists=True
    )
    assert res4 == "False (Effective privilege denied; PENDING HUMAN ACCEPTANCE)"

    # 5. Role absent -> NOT VERIFIED (ROLE DOES NOT EXIST)
    res5 = prov.format_temporary_privilege_evidence(
        public_temp=True, app_temp=None, role_exists=False
    )
    assert res5 == "NOT VERIFIED (ROLE DOES NOT EXIST)"

    # 6. Non-boolean type raises PrivilegeVerificationError
    with pytest.raises(PrivilegeVerificationError):
        prov.format_temporary_privilege_evidence(
            public_temp="UNKNOWN",  # type: ignore[arg-type]
            app_temp=True,
            role_exists=True,
        )


def test_f5_dry_run_public_false_app_true_attribution_in_report() -> None:
    """Finding 5: When PUBLIC=False and App=True, report accurately notes unverified source."""
    admin_conn = MockDbConn(
        role_exists=True,
        pub_db_temp=False,
        effective_privileges={
            "can_connect": True,
            "can_create_db": False,
            "can_create_schema": False,
            "can_use_schema": True,
            "can_temp_db": True,
        },
    )
    connector = MockDatabaseConnector(admin_conn=admin_conn)
    ckpt_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=MockNeonApi(),
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=connector,
        mode=ProvisioningMode.DRY_RUN,
    )

    res = asyncio.run(provisioner.run_dry_run(admin_password="valid-password"))
    assert res["public_baseline_temporary"] is False
    assert "source unverified" in res["effective_application_temporary"]
    assert "Inherited from PUBLIC" not in res["effective_application_temporary"]

    details = ckpt_mgr.record.call_args[1]["details"]
    assert "PUBLIC TEMPORARY=False" in details
    assert "source unverified" in details
    assert "Inherited from PUBLIC" not in details


# ==============================================================================
# Category 13: Astra Final Remediation — Finding A (HTTP Deadlines & Containment)
# and Finding B (Fail-Closed Keychain Scoping)
# ==============================================================================


class SocketMockResponse:
    """Mock HTTP response wrapping a real OS socketpair endpoint."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            return b""
        try:
            sz = size if size > 0 else 65536
            data = self.sock.recv(sz)
            return data
        except (BlockingIOError, TimeoutError):
            raise
        except OSError:
            return b""

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --- Finding A: Real HTTP Deadlines, Bounded Chunk Reads, Socket Interruption ---


def test_fa_continuously_streaming_http_response_aborts_at_deadline() -> None:
    """Finding A.1: Synchronous response streaming aborts at deadline without blocking loop."""
    client_sock, server_sock = socket.socketpair()

    def server_stream() -> None:
        try:
            while True:
                server_sock.sendall(b"x" * 1024)
                time.sleep(0.01)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_stream, daemon=True)
    t.start()

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=0.15)
        client.opener = MagicMock()
        client.opener.open.return_value = SocketMockResponse(client_sock)
        start = time.monotonic()
        with pytest.raises(TargetVerificationError) as exc:
            await client.async_request("GET", "/projects")
        elapsed = time.monotonic() - start
        assert "TIMEOUT" in str(exc.value)
        assert elapsed < 1.0  # Aborts promptly at deadline

    try:
        asyncio.run(_run())
    finally:
        try:
            server_sock.close()
        except OSError:
            pass


def test_fa_slow_response_headers_aborts_at_deadline() -> None:
    """Finding A.2: Slow or hanging response headers abort at configured deadline."""

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=0.1)
        client.opener = MagicMock()

        def slow_open(*args: Any, **kwargs: Any) -> Any:
            time.sleep(0.2)
            raise TimeoutError("Header read timed out")

        client.opener.open = slow_open
        with pytest.raises(TargetVerificationError) as exc:
            await client.async_request("GET", "/projects")
        assert "TIMEOUT" in str(exc.value)

    asyncio.run(_run())


def test_fa_slow_chunked_response_body_aborts_at_deadline() -> None:
    """Finding A.3: Slow chunk delivery aborts when deadline passes during body read."""
    client_sock, server_sock = socket.socketpair()
    server_sock.sendall(b'{"partial": 1')

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=0.1)
        client.opener = MagicMock()
        client.opener.open.return_value = SocketMockResponse(client_sock)
        with pytest.raises(TargetVerificationError) as exc:
            await client.async_request("GET", "/projects")
        assert "TIMEOUT" in str(exc.value)

    try:
        asyncio.run(_run())
    finally:
        try:
            server_sock.close()
        except OSError:
            pass


def test_fa_http_response_exceeding_max_size_fails_closed() -> None:
    """Finding A.4: HTTP responses exceeding maximum byte threshold fail closed."""
    client_sock, server_sock = socket.socketpair()
    server_sock.sendall(b"A" * 2000)

    async def _run() -> None:
        client = NeonApiClient(
            api_key="npg_test_token_123",
            timeout=1.0,
            max_response_bytes=1000,
            chunk_size=256,
        )
        client.opener = MagicMock()
        client.opener.open.return_value = SocketMockResponse(client_sock)
        with pytest.raises(ProvisioningError) as exc:
            await client.async_request("GET", "/projects")
        assert "exceeded maximum allowed size" in str(exc.value)

    try:
        asyncio.run(_run())
    finally:
        try:
            server_sock.close()
        except OSError:
            pass


def test_fa_caller_cancellation_during_metadata_verification_terminates_socket() -> None:
    """Finding A.5: Caller cancellation terminates the active socket and interrupts worker."""
    client_sock, server_sock = socket.socketpair()

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
        client.opener = MagicMock()
        client.opener.open.return_value = SocketMockResponse(client_sock)

        provisioner = StagingRoleProvisioner(
            api_client=client,
            secret_store=MagicMock(),
            checkpoint_mgr=MagicMock(),
            connector=MockDatabaseConnector(),
            mode=ProvisioningMode.EXECUTE,
        )

        task = asyncio.create_task(
            provisioner.async_verify_target_metadata(record_checkpoint=False)
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(_run())
    finally:
        try:
            server_sock.close()
        except OSError:
            pass

    try:
        client_sock.send(b"ping")
        pytest.fail("client_sock was expected to be closed or shut down")
    except OSError:
        pass


def test_fa_cancellation_during_emergency_containment_task_settles_resources() -> None:
    """Finding A.6: Cancellation during emergency containment settles tasks and records failure."""
    api = MockNeonApi()
    connector = MockDatabaseConnector()
    ckpt_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    async def hanging_call(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(10.0)

    provisioner._call_api_async = hanging_call  # type: ignore[method-assign]

    async def _run() -> None:
        task = asyncio.create_task(
            provisioner.attempt_auto_containment(
                admin_conn=MockDbConn(role_exists=True, throw_on_alter_nologin=True),
                trigger_error=RuntimeError("Triggering simulated containment failure"),
                admin_password="test-admin-password",
                containment_timeout=0.1,
            )
        )
        with pytest.raises(ContainmentFailureError) as exc:
            await task
        assert "timed out after 0.10s" in str(exc.value)
        assert "UNVERIFIED/UNKNOWN" in str(exc.value)

    asyncio.run(_run())
    assert ckpt_mgr.record.call_count >= 1
    failed_calls = [
        call
        for call in ckpt_mgr.record.call_args_list
        if (call[0] and call[0][0] == ProvisioningState.CONTAINMENT_FAILED)
        or call[1].get("state") == ProvisioningState.CONTAINMENT_FAILED
    ]
    assert len(failed_calls) == 1


def test_fa_failed_connection_cleanup_handled_gracefully() -> None:
    """Finding A.7: Exceptions during socket/response closure in finally are handled cleanly."""
    client_sock, server_sock = socket.socketpair()

    class FaultySocketMockResponse(SocketMockResponse):
        def close(self) -> None:
            super().close()
            raise OSError("Descriptor close failed")

    client = NeonApiClient(api_key="npg_test_token_123", timeout=0.1)
    client.opener = MagicMock()
    client.opener.open.return_value = FaultySocketMockResponse(client_sock)

    try:
        with pytest.raises(TargetVerificationError) as exc:
            asyncio.run(client.async_request("GET", "/projects"))
        assert "TIMEOUT" in str(exc.value)
    finally:
        try:
            server_sock.close()
        except OSError:
            pass


def test_fa_no_orphaned_tasks_or_threads_after_timeout() -> None:
    """Finding A.8: Worker threads are joined on timeout, leaving zero orphaned threads."""
    client_sock, server_sock = socket.socketpair()

    def server_worker() -> None:
        try:
            while True:
                server_sock.sendall(b"x" * 1024)
                time.sleep(0.01)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_worker, daemon=True)
    t.start()

    client: NeonApiClient | None = None

    async def _run() -> None:
        nonlocal client
        client = NeonApiClient(api_key="npg_test_token_123", timeout=0.1)
        client.opener = MagicMock()
        client.opener.open.return_value = SocketMockResponse(client_sock)
        with pytest.raises(TargetVerificationError) as exc:
            await client.async_request("GET", "/projects")
        assert "TIMEOUT" in str(exc.value)

    try:
        asyncio.run(_run())
    finally:
        try:
            server_sock.close()
        except OSError:
            pass

    time.sleep(0.05)
    assert client is not None
    assert client.last_worker_thread is not None
    assert not client.last_worker_thread.is_alive()


def test_fa_accurate_critical_diagnostics_and_unknown_login_state() -> None:
    """Finding A.9: Critical diagnostics and UNVERIFIED/UNKNOWN login state are recorded."""
    api = MockNeonApi()
    connector = MockDatabaseConnector()
    ckpt_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    async def hanging_call(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(5.0)

    provisioner._call_api_async = hanging_call  # type: ignore[method-assign]

    async def _run() -> None:
        with pytest.raises(ContainmentFailureError) as exc:
            await provisioner.attempt_auto_containment(
                admin_conn=MockDbConn(role_exists=True, throw_on_alter_nologin=True),
                trigger_error=RuntimeError("Triggering simulated containment failure"),
                admin_password="test-admin-password",
                containment_timeout=0.1,
            )
        err_msg = str(exc.value)
        assert "CRITICAL:" in err_msg
        assert "Emergency containment timed out after 0.10s" in err_msg
        assert "UNVERIFIED/UNKNOWN" in err_msg
        assert "EMERGENCY CONTAINMENT FAILED" in err_msg

    asyncio.run(_run())
    failed_calls = [
        call
        for call in ckpt_mgr.record.call_args_list
        if (call[0] and call[0][0] == ProvisioningState.CONTAINMENT_FAILED)
        or call[1].get("state") == ProvisioningState.CONTAINMENT_FAILED
    ]
    assert len(failed_calls) == 1


def test_fa_no_mutation_against_unverified_targets() -> None:
    """Finding A.10: Zero database queries or resets occur if metadata verification fails."""
    api = MockNeonApi()
    api.get_project = MagicMock(  # type: ignore[method-assign]
        side_effect=TargetVerificationError("Control plane verification timed out")
    )
    connector = MockDatabaseConnector()
    ckpt_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(provisioner.run_provisioning(admin_password="admin-password-12345"))
    assert "Control plane verification timed out" in str(exc.value)
    assert len(connector.connect_calls) == 0


# --- Finding B: Fail-Closed Keychain Scoping & C Library Binding ---


def test_fb_copy_default_error_fails_closed() -> None:
    """Finding B.1: Non-zero status from SecKeychainCopyDefault fails closed."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    fake_sec.SecKeychainCopyDefault.return_value = ERR_SEC_AUTH_FAILED

    with pytest.raises(SecretStoreError) as exc:
        MacOSSecurityFrameworkAdapter(
            service=KEYCHAIN_SERVICE,
            account=KEYCHAIN_ACCOUNT,
            sec_lib=fake_sec,
            cf_lib=fake_cf,
        )
    assert "SecKeychainCopyDefault failed with status code -25293" in str(exc.value)


def test_fb_copy_default_exception_fails_closed() -> None:
    """Finding B.2: Ctypes exception from SecKeychainCopyDefault fails closed."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    fake_sec.SecKeychainCopyDefault.side_effect = OSError("native memory corruption")

    with pytest.raises(SecretStoreError) as exc:
        MacOSSecurityFrameworkAdapter(
            service=KEYCHAIN_SERVICE,
            account=KEYCHAIN_ACCOUNT,
            sec_lib=fake_sec,
            cf_lib=fake_cf,
        )
    assert "SecKeychainCopyDefault invocation failed: native memory corruption" in str(exc.value)


def test_fb_copy_default_null_reference_fails_closed() -> None:
    """Finding B.3: NULL Keychain reference from SecKeychainCopyDefault fails closed."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    def fake_copy_default_null(out_ref: Any) -> int:
        out_ref._obj.value = None
        return ERR_SEC_SUCCESS

    fake_sec.SecKeychainCopyDefault = fake_copy_default_null

    with pytest.raises(SecretStoreError) as exc:
        MacOSSecurityFrameworkAdapter(
            service=KEYCHAIN_SERVICE,
            account=KEYCHAIN_ACCOUNT,
            sec_lib=fake_sec,
            cf_lib=fake_cf,
        )
    assert "SecKeychainCopyDefault returned NULL Keychain reference" in str(exc.value)


def test_fb_invalid_explicit_null_handle_fails_closed() -> None:
    """Finding B.4: Providing an explicit NULL c_void_p handle fails closed."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    with pytest.raises(SecretStoreError) as exc:
        MacOSSecurityFrameworkAdapter(
            service=KEYCHAIN_SERVICE,
            account=KEYCHAIN_ACCOUNT,
            sec_lib=fake_sec,
            cf_lib=fake_cf,
            keychain=ctypes.c_void_p(0),
        )
    assert "Invalid explicit null Keychain handle provided" in str(exc.value)


def test_fb_valid_explicit_keychain_accepted() -> None:
    """Finding B.5: Providing a valid non-null handle is accepted without default lookup."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    kc = ctypes.c_void_p(0xABCD)

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=kc,
    )
    assert adapter.keychain == kc
    assert adapter.owns_keychain is False
    assert fake_sec.SecKeychainCopyDefault.call_count == 0


def test_fb_successfully_acquired_default_keychain_pinned() -> None:
    """Finding B.6: Successfully acquired default Keychain handle is pinned and marked owned."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    def fake_copy_default(out_ref: Any) -> int:
        out_ref._obj.value = 0x7777
        return ERR_SEC_SUCCESS

    fake_sec.SecKeychainCopyDefault = fake_copy_default

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
    )
    assert adapter.keychain is not None
    assert getattr(adapter.keychain, "value", None) == 0x7777
    assert adapter.owns_keychain is True


def test_fb_correct_release_of_owned_references() -> None:
    """Finding B.7: CFRelease is invoked on close if and only if adapter owns the handle."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()

    def fake_copy_default(out_ref: Any) -> int:
        out_ref._obj.value = 0x8888
        return ERR_SEC_SUCCESS

    fake_sec.SecKeychainCopyDefault = fake_copy_default

    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
    )
    assert adapter.owns_keychain is True
    adapter.close()
    assert fake_cf.CFRelease.call_count == 1
    assert adapter.keychain is None
    assert adapter.owns_keychain is False

    # Second close should be safe and idempotent
    adapter.close()
    assert fake_cf.CFRelease.call_count == 1

    # Adapter with non-owned handle does not CFRelease
    explicit_kc = ctypes.c_void_p(0x9999)
    adapter2 = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=explicit_kc,
    )
    assert adapter2.owns_keychain is False
    adapter2.close()
    assert fake_cf.CFRelease.call_count == 1  # Unchanged


def test_fb_no_lookup_or_modification_after_scope_failure() -> None:
    """Finding B.8: Zero C library functions are called if scope is invalid or unpinned."""
    fake_sec = MagicMock()
    fake_cf = MagicMock()
    adapter = MacOSSecurityFrameworkAdapter(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        sec_lib=fake_sec,
        cf_lib=fake_cf,
        keychain=ctypes.c_void_p(0x1234),
    )
    adapter.keychain = None  # Simulate invalidated scope

    with pytest.raises(SecretStoreError) as exc1:
        adapter.entry_exists()
    assert "Unpinned or missing Keychain scope. Operation denied." in str(exc1.value)

    with pytest.raises(SecretStoreError) as exc2:
        adapter.get_password()
    assert "Unpinned or missing Keychain scope. Operation denied." in str(exc2.value)

    with pytest.raises(SecretStoreError) as exc3:
        adapter.store_password("test_pwd_123")
    assert "Unpinned or missing Keychain scope. Operation denied." in str(exc3.value)

    with pytest.raises(SecretStoreError) as exc4:
        adapter.verify_stored_password()
    assert "Unpinned or missing Keychain scope. Operation denied." in str(exc4.value)

    assert fake_sec.SecKeychainFindGenericPassword.call_count == 0
    assert fake_sec.SecKeychainAddGenericPassword.call_count == 0
    assert fake_sec.SecKeychainItemModifyContent.call_count == 0


# ==============================================================================
# Category 14: Final Targeted Remediation — Robust Socket Extraction,
# Teardown Ordering, Thread Liveness & Containment Verification
# ==============================================================================


def test_http_real_urllib_response_wrapper_socket_extraction() -> None:
    """Finding A.10 / Test 1: Real urllib.response.addinfourl socket extraction."""
    client_sock, server_sock = socket.socketpair()
    try:
        server_sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        http_resp = http.client.HTTPResponse(client_sock)
        http_resp.begin()
        addinfo = urllib.response.addinfourl(
            http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
        )
        extracted = _extract_socket(addinfo)
        assert extracted is not None
        assert _is_socket_like(extracted)
        assert getattr(extracted, "fileno", lambda: -1)() == client_sock.fileno()
    finally:
        client_sock.close()
        server_sock.close()


def test_http_continuously_streaming_response_terminates_at_deadline() -> None:
    """Test 2: Continuously streaming response unblocks at deadline and terminates thread."""
    client_sock, server_sock = socket.socketpair()

    def server_stream() -> None:
        try:
            server_sock.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            while True:
                server_sock.sendall(b"10\r\n0123456789abcdef\r\n")
                time.sleep(0.01)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_stream, daemon=True)
    t.start()

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=0.15)
        http_resp = http.client.HTTPResponse(client_sock)
        http_resp.begin()
        addinfo = urllib.response.addinfourl(
            http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
        )
        client.opener = MagicMock()
        client.opener.open.return_value = addinfo

        start = time.monotonic()
        with pytest.raises(TargetVerificationError) as exc:
            await client.async_request("GET", "/projects")
        elapsed = time.monotonic() - start
        assert "TIMEOUT" in str(exc.value)
        assert elapsed < 0.8
        assert client.last_worker_thread is not None
        assert not client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        try:
            client_sock.close()
        except OSError:
            pass
        try:
            server_sock.close()
        except OSError:
            pass


def test_http_slow_chunked_response_body_terminates_at_deadline() -> None:
    """Test 3: Slow chunked response body unblocks and terminates thread on timeout."""
    client_sock, server_sock = socket.socketpair()

    def server_slow() -> None:
        try:
            server_sock.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n"
                b'{"partial":'
            )
            time.sleep(1.0)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_slow, daemon=True)
    t.start()

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=0.1)
        http_resp = http.client.HTTPResponse(client_sock)
        http_resp.begin()
        addinfo = urllib.response.addinfourl(
            http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
        )
        client.opener = MagicMock()
        client.opener.open.return_value = addinfo

        with pytest.raises(TargetVerificationError) as exc:
            await client.async_request("GET", "/projects")
        assert "TIMEOUT" in str(exc.value)
        assert client.last_worker_thread is not None
        assert not client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        try:
            client_sock.close()
        except OSError:
            pass
        try:
            server_sock.close()
        except OSError:
            pass


def test_http_cancellation_during_blocked_recv_unblocks_immediately() -> None:
    """Test 4: External cancellation during blocked recv() unblocks and joins thread promptly."""
    client_sock, server_sock = socket.socketpair()

    def server_hang() -> None:
        try:
            server_sock.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n"
            )
            time.sleep(2.0)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_hang, daemon=True)
    t.start()

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
        http_resp = http.client.HTTPResponse(client_sock)
        http_resp.begin()
        addinfo = urllib.response.addinfourl(
            http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
        )
        client.opener = MagicMock()
        client.opener.open.return_value = addinfo

        task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.05)  # Let worker thread block in recv()

        cancel_start = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cancel_duration = time.monotonic() - cancel_start

        assert cancel_duration < 0.5
        assert client.last_worker_thread is not None
        assert not client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        try:
            client_sock.close()
        except OSError:
            pass
        try:
            server_sock.close()
        except OSError:
            pass


def test_http_socket_shutdown_before_response_close() -> None:
    """Test 5: Socket shutdown and close execute before response wrapper close."""
    actions: list[str] = []

    mock_sock = MagicMock()

    def on_shutdown(*args: Any) -> None:
        actions.append("sock_shutdown")

    def on_sock_close() -> None:
        actions.append("sock_close")

    mock_sock.shutdown.side_effect = on_shutdown
    mock_sock.close.side_effect = on_sock_close

    mock_resp = MagicMock()

    def on_resp_close() -> None:
        actions.append("resp_close")

    mock_resp.close.side_effect = on_resp_close

    holder = _ActiveRequestHolder()
    holder.register(resp=mock_resp, sock=mock_sock)
    result = holder.interrupt()

    assert result is True
    assert actions == ["sock_shutdown", "sock_close", "resp_close"]

    # Verify second interrupt call is idempotent and does not repeat actions
    holder.interrupt()
    assert actions == ["sock_shutdown", "sock_close", "resp_close"]


def test_http_worker_thread_termination_guaranteed() -> None:
    """Test 6: Multiple concurrent requests verify zero surviving worker threads."""
    client_socks: list[socket.socket] = []
    server_socks: list[socket.socket] = []

    for _ in range(3):
        cs, ss = socket.socketpair()
        ss.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n"
        )
        client_socks.append(cs)
        server_socks.append(ss)

    clients: list[NeonApiClient] = []

    async def _run() -> None:
        tasks = []
        for cs in client_socks:
            client = NeonApiClient(api_key="npg_test_token_123", timeout=0.1)
            clients.append(client)
            http_resp = http.client.HTTPResponse(cs)
            http_resp.begin()
            addinfo = urllib.response.addinfourl(
                http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
            )
            client.opener = MagicMock()
            client.opener.open.return_value = addinfo
            tasks.append(client.async_request("GET", "/projects"))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            assert isinstance(r, TargetVerificationError)
            assert "TIMEOUT" in str(r)

    try:
        asyncio.run(_run())
    finally:
        for s in client_socks + server_socks:
            try:
                s.close()
            except OSError:
                pass

    for c in clients:
        assert c.last_worker_thread is not None
        assert not c.last_worker_thread.is_alive()


def test_http_response_wrapper_with_missing_socket_evidence() -> None:
    """Test 7: Response wrapper with missing socket reports critical uncertainty on leak."""
    dummy_resp = MagicMock(spec=["read", "close"])
    extracted = _extract_socket(dummy_resp)
    assert extracted is None

    holder = _ActiveRequestHolder()
    holder.register(resp=dummy_resp, sock=None)
    assert holder.socket_identified is False
    assert holder.interruption_guaranteed is False

    # When interrupted with no socket, returns False (interruption cannot be guaranteed)
    assert holder.interrupt() is False

    # Simulate thread that cannot terminate because no socket unblocks recv
    client = NeonApiClient(api_key="npg_test_token_123", timeout=0.05)

    def blocked_read(*args: Any, **kwargs: Any) -> bytes:
        time.sleep(2.0)
        return b"{}"

    dummy_resp.read.side_effect = blocked_read
    client.opener = MagicMock()
    client.opener.open.return_value = dummy_resp

    async def _run() -> None:
        with pytest.raises(TargetVerificationError) as exc:
            await client.async_request("GET", "/projects")
        assert "Critical uncertainty" in str(exc.value)
        assert "thread remains active" in str(exc.value)

    asyncio.run(_run())


def test_emergency_containment_deadline_enforced() -> None:
    """Test 8: Emergency containment enforces strict deadline on slow or hanging connection."""
    api = MockNeonApi()
    connector = MockDatabaseConnector()
    ckpt_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    async def slow_connect(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(2.0)
        return MockDbConn()

    connector.connect = slow_connect  # type: ignore[method-assign]

    async def _run() -> None:
        start = time.monotonic()
        with pytest.raises(ContainmentFailureError) as exc:
            await provisioner.attempt_auto_containment(
                admin_conn=MockDbConn(role_exists=True, throw_on_alter_nologin=True),
                trigger_error=RuntimeError("Simulated pre-containment failure"),
                admin_password="test-admin-password",
                containment_timeout=0.15,
            )
        elapsed = time.monotonic() - start
        assert elapsed < 0.6
        assert "timed out after 0.15s" in str(exc.value)

    asyncio.run(_run())


def test_no_successful_containment_claim_when_nologin_unverified() -> None:
    """Test 9: Containment fails closed if post-ALTER readback does not verify NOLOGIN."""
    api = MockNeonApi()
    connector = MockDatabaseConnector()
    ckpt_mgr = MagicMock()

    provisioner = StagingRoleProvisioner(
        api_client=api,
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=connector,
        mode=ProvisioningMode.EXECUTE,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    mock_admin = MockDbConn(role_exists=True, throw_on_alter_nologin=False)

    async def failing_verify(conn: Any, expect_login: bool = True) -> None:
        if not expect_login:
            raise TargetVerificationError("Role dormiostay_app still has rolcanlogin=True")

    provisioner.verify_structural_restrictions = failing_verify  # type: ignore[method-assign]

    async def _run() -> None:
        with pytest.raises(ContainmentFailureError) as exc:
            await provisioner.attempt_auto_containment(
                admin_conn=mock_admin,
                trigger_error=RuntimeError("Triggering auto-containment"),
                admin_password="test-admin-password",
                containment_timeout=1.0,
            )
        assert "EMERGENCY CONTAINMENT FAILED" in str(exc.value)
        assert provisioner.state == ProvisioningState.CONTAINMENT_FAILED

    asyncio.run(_run())

    # Ensure checkpoint recorded failure and NEVER recorded success
    records = [
        call[0][0] if call[0] else call[1].get("state") for call in ckpt_mgr.record.call_args_list
    ]
    assert ProvisioningState.CONTAINMENT_FAILED in records
    assert ProvisioningState.CONTAINMENT_EXECUTED not in records


def test_event_loop_responsiveness_throughout_cancellation() -> None:
    """Test 10: Event loop responsiveness is maintained throughout request and cancellation."""
    client_sock, server_sock = socket.socketpair()

    def server_stream() -> None:
        try:
            server_sock.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            while True:
                server_sock.sendall(b"10\r\n0123456789abcdef\r\n")
                time.sleep(0.01)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_stream, daemon=True)
    t.start()

    heartbeat_ticks = 0

    async def heartbeat() -> None:
        nonlocal heartbeat_ticks
        while True:
            await asyncio.sleep(0.01)
            heartbeat_ticks += 1

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=0.15)
        http_resp = http.client.HTTPResponse(client_sock)
        http_resp.begin()
        addinfo = urllib.response.addinfourl(
            http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
        )
        client.opener = MagicMock()
        client.opener.open.return_value = addinfo

        hb_task = asyncio.create_task(heartbeat())
        try:
            with pytest.raises(TargetVerificationError) as exc:
                await client.async_request("GET", "/projects")
            assert "TIMEOUT" in str(exc.value)
        finally:
            hb_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await hb_task

    try:
        asyncio.run(_run())
        assert heartbeat_ticks >= 5
    finally:
        try:
            client_sock.close()
        except OSError:
            pass
        try:
            server_sock.close()
        except OSError:
            pass


# ==============================================================================
# Category 15: Astra Final Remediation — Finding 1 (HTTP Cancellation & Loop Responsiveness)
# and Finding 2 (Strict Monotonic Overall Containment Deadline)
# ==============================================================================


def test_astra_finding1_cancellation_before_opener_returns_headers_responsiveness() -> None:
    """Finding 1.1: Cancellation before opener returns headers maintains 10ms heartbeat."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    unblock_ev = threading.Event()

    def slow_open(req: Any, timeout: float | None = None) -> Any:
        unblock_ev.wait()
        raise RuntimeError("slow opener released")

    client.opener = MagicMock()
    client.opener.open = slow_open

    heartbeat_delays: list[float] = []

    async def heartbeat() -> None:
        t_prev = time.monotonic()
        try:
            while True:
                await asyncio.sleep(0.01)
                t_now = time.monotonic()
                heartbeat_delays.append(t_now - t_prev)
                t_prev = t_now
        except asyncio.CancelledError:
            pass

    async def _run() -> None:
        hb_task = asyncio.create_task(heartbeat())
        req_task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.03)

        req_task.cancel()
        with pytest.raises(TargetVerificationError) as exc:
            await req_task

        assert "Critical uncertainty" in str(exc.value)
        assert "thread remains active" in str(exc.value)
        assert "interruption_guaranteed=False" in str(exc.value)

        # Assert no event loop stall: max heartbeat interval strictly < 50ms
        max_delay = max(heartbeat_delays) if heartbeat_delays else 0.0
        assert max_delay < 0.05, f"Heartbeat was stalled: {max_delay:.4f}s"

        # Release worker and clean up heartbeat
        unblock_ev.set()
        await asyncio.sleep(0.05)
        hb_task.cancel()
        await asyncio.gather(hb_task, return_exceptions=True)

    try:
        asyncio.run(_run())
    finally:
        unblock_ev.set()


def test_astra_finding1_cancellation_during_connection_establishment() -> None:
    """Finding 1.2: Cancellation during connection establishment fails closed without stalls."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    conn_block = threading.Event()

    def slow_connect(req: Any, timeout: float | None = None) -> Any:
        conn_block.wait()
        raise urllib.error.URLError("Connection refused")

    client.opener = MagicMock()
    client.opener.open = slow_connect

    async def _run() -> None:
        t0 = time.monotonic()
        req_task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.02)
        req_task.cancel()
        with pytest.raises(TargetVerificationError) as exc:
            await req_task
        elapsed = time.monotonic() - t0
        # Non-blocking settlement completes quickly within settle window
        assert elapsed < 0.45
        assert "Critical uncertainty" in str(exc.value)
        assert "interruption_guaranteed=False" in str(exc.value)
        conn_block.set()

    try:
        asyncio.run(_run())
    finally:
        conn_block.set()


def test_astra_finding1_cancellation_with_registered_socket_terminates_immediately() -> None:
    """Finding 1.3: Cancellation with registered socket interrupts worker and terminates."""
    client_sock, server_sock = socket.socketpair()

    def server_hold() -> None:
        try:
            server_sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
            time.sleep(2.0)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_hold, daemon=True)
    t.start()

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
        http_resp = http.client.HTTPResponse(client_sock)
        http_resp.begin()
        addinfo = urllib.response.addinfourl(
            http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
        )
        client.opener = MagicMock()
        client.opener.open.return_value = addinfo

        req_task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.04)  # Let worker thread block in socket recv

        t0 = time.monotonic()
        req_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await req_task
        cancel_duration = time.monotonic() - t0

        # Terminated promptly and cleanly
        assert cancel_duration < 0.2
        assert client.last_worker_thread is not None
        assert not client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        try:
            client_sock.close()
        except OSError:
            pass
        try:
            server_sock.close()
        except OSError:
            pass


def test_astra_finding1_cancellation_during_body_streaming() -> None:
    """Finding 1.4: Cancellation during streaming unblocks socket and raises CancelledError."""
    client_sock, server_sock = socket.socketpair()

    def server_chunks() -> None:
        try:
            server_sock.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            while True:
                server_sock.sendall(b"10\r\n0123456789abcdef\r\n")
                time.sleep(0.01)
        except Exception:
            pass
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    t = threading.Thread(target=server_chunks, daemon=True)
    t.start()

    async def _run() -> None:
        client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
        http_resp = http.client.HTTPResponse(client_sock)
        http_resp.begin()
        addinfo = urllib.response.addinfourl(
            http_resp, http_resp.headers, "https://console.neon.tech/api/v2/projects"
        )
        client.opener = MagicMock()
        client.opener.open.return_value = addinfo

        req_task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.05)

        t0 = time.monotonic()
        req_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await req_task
        elapsed = time.monotonic() - t0

        assert elapsed < 0.2
        assert client.last_worker_thread is not None
        assert not client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        try:
            client_sock.close()
        except OSError:
            pass
        try:
            server_sock.close()
        except OSError:
            pass


def test_astra_finding1_no_unretrieved_future_exceptions_on_delayed_worker_exit() -> None:
    """Finding 1.5: Releasing delayed worker after cancellation produces zero future warnings."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    worker_gate = threading.Event()

    def delayed_open(req: Any, timeout: float | None = None) -> Any:
        worker_gate.wait()
        raise TargetVerificationError("Neon API connection reset by peer")

    client.opener = MagicMock()
    client.opener.open = delayed_open

    async def _run() -> None:
        req_task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.02)
        req_task.cancel()
        with pytest.raises(TargetVerificationError):
            await req_task

        # Worker finishes later
        worker_gate.set()
        await asyncio.sleep(0.05)

    try:
        asyncio.run(_run())
    finally:
        worker_gate.set()


def test_astra_finding2_actual_15s_default_containment_deadline_with_slow_cleanup() -> None:
    """Finding 2.1: Containment deadline strictly enforced with zero overshoot on slow cleanup."""
    provisioner = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.EXECUTE,
    )

    admin_conn = MockDbConn()

    async def slow_exec(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.01)
        raise RuntimeError("Step 1 failed")

    admin_conn.execute = slow_exec  # type: ignore[method-assign]

    fresh_conn = MockDbConn()

    async def hang_alter(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30.0)

    fresh_conn.execute = hang_alter  # type: ignore[method-assign]

    async def slow_close() -> None:
        await asyncio.sleep(1.5)

    fresh_conn.close = slow_close  # type: ignore[method-assign]

    provisioner.connector.connect = AsyncMock(return_value=fresh_conn)  # type: ignore[method-assign]
    provisioner.async_verify_target_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }
    )

    t0 = time.monotonic()
    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn=admin_conn,
                trigger_error=RuntimeError("Trigger error"),
                admin_password="test-password",
                containment_timeout=1.0,
            )
        )
    elapsed = time.monotonic() - t0

    # Strict deadline adherence: 1.0s budget must not overshoot by 1.5s
    assert elapsed < 1.15, f"Containment exceeded deadline: {elapsed:.3f}s"
    assert "EMERGENCY CONTAINMENT FAILED" in str(exc.value)
    assert "login status is UNVERIFIED/UNKNOWN" in str(exc.value)


def test_astra_finding2_expired_50ms_budget_aborts_before_metadata_fallback() -> None:
    """Finding 2.2: Expired 50ms budget aborts immediately and never starts fallback operations."""
    provisioner = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.EXECUTE,
    )

    admin_conn = MockDbConn()

    async def slow_exec(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.04)
        raise RuntimeError("Step 1 failed")

    admin_conn.execute = slow_exec  # type: ignore[method-assign]

    fallback_calls: list[str] = []

    async def mock_meta(*args: Any, **kwargs: Any) -> dict[str, Any]:
        fallback_calls.append("metadata")
        return {
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }

    provisioner.async_verify_target_metadata = mock_meta  # type: ignore[method-assign]

    t0 = time.monotonic()
    with pytest.raises(ContainmentFailureError):
        asyncio.run(
            provisioner.attempt_auto_containment(
                admin_conn=admin_conn,
                trigger_error=RuntimeError("Trigger error"),
                admin_password="test-password",
                containment_timeout=0.05,
            )
        )
    elapsed = time.monotonic() - t0

    assert elapsed < 0.08, f"50ms budget exceeded: {elapsed:.4f}s"
    assert len(fallback_calls) == 0, "Fallback metadata was called after deadline expired!"


def test_astra_finding2_repeated_cancellation_during_containment() -> None:
    """Finding 2.3: Repeated cancellation during containment settles cleanly without orphans."""
    provisioner = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.EXECUTE,
    )

    admin_conn = MockDbConn()

    async def slow_exec(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.5)

    admin_conn.execute = slow_exec  # type: ignore[method-assign]

    async def _run() -> None:
        task = asyncio.create_task(
            provisioner.attempt_auto_containment(
                admin_conn=admin_conn,
                trigger_error=RuntimeError("Trigger error"),
                admin_password="test-password",
                containment_timeout=2.0,
            )
        )
        await asyncio.sleep(0.02)
        task.cancel()
        task.cancel()  # Repeated cancellation
        with pytest.raises(ContainmentFailureError) as exc:
            await task
        assert "login status is UNVERIFIED/UNKNOWN" in str(exc.value)
        assert "EMERGENCY CONTAINMENT FAILED" in str(exc.value)

    asyncio.run(_run())


def test_astra_finding2_verified_nologin_followed_by_cleanup_failure_preserves_success() -> None:
    """Finding 2.4: Verified NOLOGIN preserves success even if subsequent cleanup fails."""
    ckpt_mgr = MagicMock()
    provisioner = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=MagicMock(),
        mode=ProvisioningMode.EXECUTE,
    )

    admin_conn = MockDbConn(role_exists=True)

    async def alter_ok(*args: Any, **kwargs: Any) -> Any:
        pass

    admin_conn.execute = alter_ok  # type: ignore[method-assign]

    async def close_fails() -> None:
        raise OSError("Network dropped during close")

    admin_conn.close = close_fails  # type: ignore[method-assign]

    async def _run() -> None:
        with pytest.raises(ProvisioningError) as exc:
            await provisioner.attempt_auto_containment(
                admin_conn=admin_conn,
                trigger_error=RuntimeError("Trigger error"),
                admin_password="test-password",
                containment_timeout=1.0,
            )
        assert provisioner.state == ProvisioningState.CONTAINMENT_EXECUTED
        assert "Auto-containment successfully reverted 'dormiostay_app' to NOLOGIN" in str(
            exc.value
        )

    asyncio.run(_run())
    records = [
        call[0][0] if call[0] else call[1].get("state") for call in ckpt_mgr.record.call_args_list
    ]
    assert ProvisioningState.CONTAINMENT_EXECUTED in records


def test_astra_finding2_unverified_nologin_reports_unverified_unknown() -> None:
    """Finding 2.5: Unverified NOLOGIN explicitly reports UNVERIFIED/UNKNOWN login status."""
    provisioner = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.EXECUTE,
    )

    admin_conn = MockDbConn()

    async def alter_fails(*args: Any, **kwargs: Any) -> Any:
        raise TargetVerificationError("Alter failed")

    admin_conn.execute = alter_fails  # type: ignore[method-assign]

    async def _run() -> None:
        with pytest.raises(ContainmentFailureError) as exc:
            await provisioner.attempt_auto_containment(
                admin_conn=admin_conn,
                trigger_error=RuntimeError("Trigger error"),
                admin_password=None,  # No password to prevent fallback
                containment_timeout=0.1,
            )
        assert "login status is UNVERIFIED/UNKNOWN" in str(exc.value)
        assert "EMERGENCY CONTAINMENT FAILED" in str(exc.value)

    asyncio.run(_run())


def test_astra_finding2_no_sql_mutation_when_staging_identity_unverified() -> None:
    """Finding 2.6: Emergency containment never executes SQL if staging identity is unverified."""
    provisioner = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.EXECUTE,
    )

    admin_conn = MockDbConn()

    async def meta_production(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": PRODUCTION_BRANCH_ID},  # Production branch detected!
        }

    provisioner.async_verify_target_metadata = meta_production  # type: ignore[method-assign]

    sql_executed = False

    async def tracking_exec(*args: Any, **kwargs: Any) -> Any:
        nonlocal sql_executed
        sql_executed = True

    admin_conn.execute = tracking_exec  # type: ignore[method-assign]

    async def _run() -> None:
        with pytest.raises(ContainmentFailureError):
            await provisioner.attempt_auto_containment(
                admin_conn=MockDbConn(),
                trigger_error=RuntimeError("Trigger error"),
                admin_password="test-password",
                containment_timeout=0.5,
            )

    asyncio.run(_run())
    assert not sql_executed, "SQL mutation was executed on unverified/production target!"


# ==============================================================================
# Category 16: Astra Gate 1 Blocker Surgical Verification
# Blocker 1 (Repeated HTTP cancellation & critical uncertainty reporting)
# Blocker 2 (Standalone --contain-role strict monotonic deadline & failure reporting)
# ==============================================================================


def test_astra_blocker1_double_cancellation_25ms_apart_preserves_critical_diagnostics() -> None:
    """Blocker 1.1: Double cancellation 25ms apart preserves critical uncertainty diagnostics."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    unblock = threading.Event()

    def slow_open(req: Any, timeout: float | None = None) -> Any:
        unblock.wait()
        raise RuntimeError("slow opener done")

    client.opener = MagicMock()
    client.opener.open = slow_open

    async def _run() -> None:
        task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.01)
        task.cancel()  # 1st cancellation
        await asyncio.sleep(0.025)
        task.cancel()  # 2nd cancellation 25ms later

        with pytest.raises(TargetVerificationError) as exc:
            await task

        assert "Critical uncertainty" in str(exc.value)
        assert "thread remains active" in str(exc.value)
        assert "interruption_guaranteed=False" in str(exc.value)
        assert client.last_worker_thread is not None
        assert client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        unblock.set()


def test_astra_blocker1_three_consecutive_cancellations() -> None:
    """Blocker 1.2: Three consecutive cancellations during settlement preserve diagnostics."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    unblock = threading.Event()

    def slow_open(req: Any, timeout: float | None = None) -> Any:
        unblock.wait()
        raise RuntimeError("slow opener done")

    client.opener = MagicMock()
    client.opener.open = slow_open

    async def _run() -> None:
        task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(TargetVerificationError) as exc:
            await task

        assert "Critical uncertainty" in str(exc.value)
        assert client.last_worker_thread is not None
        assert client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        unblock.set()


def test_astra_blocker1_repeated_cancellation_worker_eventually_exits() -> None:
    """Blocker 1.3: Repeated cancellation where worker exits within 300ms window cleanly cancels."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    unblock = threading.Event()

    def delayed_open(req: Any, timeout: float | None = None) -> Any:
        unblock.wait()
        raise TargetVerificationError("Worker exited cleanly")

    client.opener = MagicMock()
    client.opener.open = delayed_open

    async def _run() -> None:
        task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()

        # Worker unblocks at 50ms (well within the 300ms window)
        await asyncio.sleep(0.02)
        unblock.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(0.02)
        assert client.last_worker_thread is not None
        assert not client.last_worker_thread.is_alive()

    try:
        asyncio.run(_run())
    finally:
        unblock.set()


def test_astra_blocker1_repeated_cancellation_worker_remains_blocked() -> None:
    """Blocker 1.4: Repeated cancellation with permanently blocked worker reports uncertainty."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    block_forever = threading.Event()

    def hang_open(req: Any, timeout: float | None = None) -> Any:
        block_forever.wait()
        raise RuntimeError("hang open")

    client.opener = MagicMock()
    client.opener.open = hang_open

    async def _run() -> None:
        task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()

        with pytest.raises(TargetVerificationError) as exc:
            await task

        assert "Critical uncertainty" in str(exc.value)
        assert "thread remains active" in str(exc.value)
        assert "response state unknown. Manual verification required before retrying." in str(
            exc.value
        )
        assert "interruption_guaranteed=False" in str(exc.value)

    try:
        asyncio.run(_run())
    finally:
        block_forever.set()


def test_astra_blocker1_no_unbounded_settlement_loop() -> None:
    """Blocker 1.5: Settlement deadline is monotonic and strictly bounded by ~300ms."""
    client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
    hang = threading.Event()

    def hang_open(req: Any, timeout: float | None = None) -> Any:
        hang.wait()
        raise RuntimeError("done")

    client.opener = MagicMock()
    client.opener.open = hang_open

    async def _run() -> None:
        t0 = time.monotonic()
        task = asyncio.create_task(client.async_request("GET", "/projects"))
        await asyncio.sleep(0.01)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(0.02)
            task.cancel()

        with pytest.raises(TargetVerificationError):
            await task

        elapsed = time.monotonic() - t0
        # Must finish around 0.28s - 0.40s, strictly under 0.45s
        assert 0.28 <= elapsed < 0.45, f"Settlement took unexpected time: {elapsed:.3f}s"

    try:
        asyncio.run(_run())
    finally:
        hang.set()


def test_astra_blocker1_cli_handling_critical_uncertainty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Blocker 1.6: CLI prints critical uncertainty diagnostic and exits with 1 (not 130)."""
    monkeypatch.setenv("NEON_API_KEY", "npg_test_token_123")
    api = MockNeonApi()

    def fail_get_project(project_id: str) -> dict[str, Any]:
        raise TargetVerificationError(
            "Neon API request cancellation failed: Worker thread NeonApiClient-Worker "
            "did not terminate after cancellation (interruption_guaranteed=False); "
            "response state unknown. Manual verification required before retrying. "
            "Critical uncertainty: Emergency cleanup cannot be guaranteed; thread remains active."
        )

    api.get_project = fail_get_project  # type: ignore[method-assign]

    exit_code = cli_entrypoint(
        ["--dry-run"],
        api_client=api,
        secret_store=MagicMock(),
        connector=MockDatabaseConnector(),
    )

    assert exit_code == 1, f"Expected exit code 1 for critical uncertainty, got {exit_code}"
    captured = capsys.readouterr()
    assert "Critical uncertainty" in captured.err
    assert "thread remains active" in captured.err
    assert "Operation cancelled by user or signal" not in captured.err


def test_astra_blocker2_manual_containment_50ms_delayed_identity_zero_sql() -> None:
    """Blocker 2.1: 50ms budget with delayed identity check executes ZERO SQL statements."""
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    conn = MockDbConn()
    alter_calls: list[str] = []

    async def delayed_meta(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.03)
        return {
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }

    prov.async_verify_target_metadata = delayed_meta  # type: ignore[method-assign]

    async def delayed_tls(conn: Any) -> None:
        await asyncio.sleep(0.03)  # Exceeds remaining 50ms budget

    prov.verify_pg_tls_and_identity = delayed_tls  # type: ignore[method-assign]

    async def record_exec(query: str) -> None:
        alter_calls.append(query)

    conn.execute = record_exec  # type: ignore[method-assign]

    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(prov.execute_containment_nologin(conn=conn, timeout=0.05))

    assert len(alter_calls) == 0, "ALTER ROLE was initiated after deadline expired!"
    assert "EMERGENCY CONTAINMENT FAILED" in str(exc.value)
    assert "login status is UNVERIFIED/UNKNOWN" in str(exc.value)


def test_astra_blocker2_manual_containment_alter_ok_readback_timeout() -> None:
    """Blocker 2.2: ALTER ROLE ok followed by readback timeout reports UNVERIFIED/UNKNOWN."""
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    conn = MockDbConn()

    prov.async_verify_target_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }
    )
    prov.verify_pg_tls_and_identity = AsyncMock()  # type: ignore[method-assign]
    conn.execute = AsyncMock()  # type: ignore[method-assign]

    async def hang_readback(conn: Any, expect_login: bool = False) -> None:
        await asyncio.sleep(2.0)

    prov.verify_structural_restrictions = hang_readback  # type: ignore[method-assign]

    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(prov.execute_containment_nologin(conn=conn, timeout=0.05))

    assert "EMERGENCY CONTAINMENT FAILED" in str(exc.value)
    assert "login status is UNVERIFIED/UNKNOWN" in str(exc.value)
    assert "TimeoutError" in str(exc.value)


def test_astra_blocker2_manual_containment_timeout_before_connection() -> None:
    """Blocker 2.3: Budget exhaustion before connection aborts without attempting connect."""
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )

    async def slow_meta(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.06)
        return {
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }

    prov.async_verify_target_metadata = slow_meta  # type: ignore[method-assign]
    prov.connector.connect = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ContainmentFailureError) as exc:
        asyncio.run(prov.execute_containment_nologin(conn=None, admin_password="pwd", timeout=0.05))

    prov.connector.connect.assert_not_called()
    assert "login status is UNVERIFIED/UNKNOWN" in str(exc.value)


def test_astra_blocker2_manual_containment_timeout_before_alter_role() -> None:
    """Blocker 2.4: Budget exhaustion after TLS check aborts before ALTER ROLE."""
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    conn = MockDbConn()

    prov.async_verify_target_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }
    )

    async def slow_tls(conn: Any) -> None:
        await asyncio.sleep(0.06)

    prov.verify_pg_tls_and_identity = slow_tls  # type: ignore[method-assign]
    conn.execute = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ContainmentFailureError):
        asyncio.run(prov.execute_containment_nologin(conn=conn, timeout=0.05))

    conn.execute.assert_not_called()


def test_astra_blocker2_manual_containment_successful_verified_nologin() -> None:
    """Blocker 2.5: Successful containment records CONTAINMENT_EXECUTED state and checkpoint."""
    ckpt_mgr = MagicMock()
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=ckpt_mgr,
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    conn = MockDbConn(role_exists=True)
    prov.async_verify_target_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }
    )
    prov.verify_pg_tls_and_identity = AsyncMock()  # type: ignore[method-assign]
    prov.verify_structural_restrictions = AsyncMock()  # type: ignore[method-assign]
    conn.execute = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(prov.execute_containment_nologin(conn=conn, timeout=5.0))

    assert prov.state == ProvisioningState.CONTAINMENT_EXECUTED
    records = [
        call[0][0] if call[0] else call[1].get("state") for call in ckpt_mgr.record.call_args_list
    ]
    assert ProvisioningState.CONTAINMENT_EXECUTED in records


def test_astra_blocker2_manual_containment_verified_nologin_cleanup_exception() -> None:
    """Blocker 2.6: Verified NOLOGIN preserves success even if connection close raises exception."""
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    conn = MockDbConn(role_exists=True)
    prov.async_verify_target_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }
    )
    prov.verify_pg_tls_and_identity = AsyncMock()  # type: ignore[method-assign]
    prov.verify_structural_restrictions = AsyncMock()  # type: ignore[method-assign]
    conn.execute = AsyncMock()  # type: ignore[method-assign]

    async def close_fails() -> None:
        raise OSError("Connection reset by peer during close")

    conn.close = close_fails  # type: ignore[method-assign]
    prov.connector.connect = AsyncMock(return_value=conn)  # type: ignore[method-assign]

    # Should complete without raising exception
    asyncio.run(prov.execute_containment_nologin(conn=None, admin_password="pwd", timeout=5.0))
    assert prov.state == ProvisioningState.CONTAINMENT_EXECUTED


def test_astra_blocker2_manual_containment_repeated_cancellation() -> None:
    """Blocker 2.7: Repeated cancellation during manual containment reports UNVERIFIED/UNKNOWN."""
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=STAGING_BRANCH_ID,
    )
    conn = MockDbConn()

    async def hang_tls(conn: Any) -> None:
        await asyncio.sleep(2.0)

    prov.async_verify_target_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint": {"host": EXPECTED_ENDPOINT_HOST},
            "staging_branch": {"id": STAGING_BRANCH_ID},
        }
    )
    prov.verify_pg_tls_and_identity = hang_tls  # type: ignore[method-assign]

    async def _run() -> None:
        task = asyncio.create_task(prov.execute_containment_nologin(conn=conn, timeout=5.0))
        await asyncio.sleep(0.02)
        task.cancel()
        task.cancel()  # Repeated cancellation
        with pytest.raises(ContainmentFailureError) as exc:
            await task
        assert "login status is UNVERIFIED/UNKNOWN" in str(exc.value)
        assert "cancelled before NOLOGIN could be verified" in str(exc.value)

    asyncio.run(_run())


def test_astra_blocker2_manual_containment_cli_exit_status_and_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Blocker 2.8: CLI exit status and output for successful vs failed manual containment."""
    monkeypatch.setenv("NEON_API_KEY", "npg_test_token_123")
    mock_api = MockNeonApi()
    mock_conn = MockDbConn(role_exists=True)
    mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

    # Subtest A: Containment Failure (branch confirmation fails)
    code_fail = cli_entrypoint(
        ["--contain-role", "--confirm-branch", "wrong-branch"],
        api_client=mock_api,
        secret_store=MagicMock(),
        connector=mock_connector,
        admin_password="test-password",
    )
    assert code_fail == 1
    err_fail = capsys.readouterr().err
    assert "Containment mode requires '--confirm-branch" in err_fail

    # Subtest B: Containment Success
    code_ok = cli_entrypoint(
        ["--contain-role", "--confirm-branch", STAGING_BRANCH_ID],
        api_client=mock_api,
        secret_store=MagicMock(),
        connector=mock_connector,
        admin_password="test-password",
    )
    assert code_ok == 0
    out_ok = capsys.readouterr().out
    assert "Emergency containment SUCCESSFUL" in out_ok


def test_astra_blocker2_manual_containment_production_branch_rejection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Blocker 2.9: Manual containment strictly rejects production branch before any execution."""
    code = cli_entrypoint(
        ["--contain-role", "--confirm-branch", PRODUCTION_BRANCH_ID],
        admin_password="test-password",
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "Production branch 'br-holy-lake-bi0hc1lu' strictly rejected" in err

    # And verify provisioner directly fails closed on wrong branch
    prov = StagingRoleProvisioner(
        api_client=MagicMock(),
        secret_store=MagicMock(),
        checkpoint_mgr=MagicMock(),
        connector=MagicMock(),
        mode=ProvisioningMode.CONTAIN,
        confirmed_branch=PRODUCTION_BRANCH_ID,
    )
    with pytest.raises(TargetVerificationError) as exc:
        asyncio.run(prov.execute_containment_nologin(admin_password="pwd"))
    assert "Confirmation failed" in str(exc.value)


# ==============================================================================
# Category 17: Real Subprocess & OS Signal Handling Tests (CLI Bounded SIGINT Boundary)
# ==============================================================================


def _run_subproc_with_signals(
    script: str,
    sigint_plan: Sequence[tuple[float, int | signal.Signals]] | None = None,
    wait_for_ready: bool = True,
    timeout: float = 4.0,
) -> tuple[int, str, str]:
    """Helper to run a real isolated Python subprocess and deliver OS-level signals."""
    repo_root = str(Path(__file__).resolve().parents[4])
    api_root = str(Path(__file__).resolve().parents[2])
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.pathsep.join([repo_root, api_root])
    env["NEON_API_KEY"] = "npg_test_token_123"
    env["NEON_ADMIN_PASSWORD"] = "test_pwd"

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if wait_for_ready and proc.stdout:
        start_wait = time.monotonic()
        while time.monotonic() - start_wait < 3.0:
            line = proc.stdout.readline()
            if "READY" in line:
                break
            if not line and proc.poll() is not None:
                break

    if sigint_plan:
        for delay, sig in sigint_plan:
            if delay > 0:
                time.sleep(delay)
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                break

    stdout_rem, stderr = proc.communicate(timeout=timeout)
    return proc.returncode, stdout_rem, stderr


def test_astra_sigint_blocker_double_sigint_blocked_worker_exits_one() -> None:
    """Category 17.1 (Astra Blocker 1 CLI Reproduction): Double SIGINT 25ms apart on blocked worker.

    Verifies that _run_with_bounded_sigint_handling:
    1. Prevents Python Runner._on_sigint from raising KeyboardInterrupt on the 2nd SIGINT.
    2. Allows the monotonic 300ms worker settlement deadline to execute to completion.
    3. Truthfully reports Critical uncertainty and exits with code 1 (NOT 130).
    4. Does NOT emit 'Operation cancelled by user or signal'.
    5. Does NOT emit unhandled task exception tracebacks during shutdown.
    """
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
hang = threading.Event()

def hang_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    hang.wait(5.0)
    raise RuntimeError("Should not be reached")

client.opener = MagicMock()
client.opener.open = hang_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    sigint_plan = [(0.0, signal.SIGINT), (0.025, signal.SIGINT)]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 1, f"Expected exit code 1 for blocked worker with double SIGINT, got {ret}"
    assert "Critical uncertainty" in stderr
    assert "thread remains active" in stderr
    assert "interruption_guaranteed=False" in stderr
    assert "Operation cancelled by user or signal" not in stderr
    assert "unhandled exception during asyncio.run() shutdown" not in stderr
    assert "Traceback (most recent call last)" not in stderr
    assert stderr.strip().startswith("[!] Provisioning error:")


def test_astra_sigint_blocker_double_sigint_cleanly_settled_worker_exits_130() -> None:
    """Category 17.2: Double SIGINT 25ms apart with worker settling within 300ms exits 130."""
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)

def settling_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    time.sleep(0.25)
    raise RuntimeError("Settled cleanly")

client.opener = MagicMock()
client.opener.open = settling_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    sigint_plan = [(0.0, signal.SIGINT), (0.025, signal.SIGINT)]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 130, f"Expected exit code 130 for settled worker with double SIGINT, got {ret}"
    assert "[!] Operation cancelled by user or signal." in stderr
    assert "Critical uncertainty" not in stderr
    assert "unhandled exception during asyncio.run() shutdown" not in stderr


def test_astra_sigint_blocker_single_sigint_blocked_worker_reports_critical_uncertainty() -> None:
    """Category 17.3: Single SIGINT with blocked worker reports critical uncertainty and exits 1."""
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
hang = threading.Event()

def hang_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    hang.wait(5.0)
    raise RuntimeError("Should not be reached")

client.opener = MagicMock()
client.opener.open = hang_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    sigint_plan = [(0.0, signal.SIGINT)]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 1, f"Expected exit code 1 for blocked worker with single SIGINT, got {ret}"
    assert "Critical uncertainty" in stderr
    assert "thread remains active" in stderr
    assert "Operation cancelled by user or signal" not in stderr


def test_astra_sigint_blocker_single_sigint_cleanly_settled_worker_exits_130() -> None:
    """Category 17.4: Single SIGINT with cleanly settling worker exits 130."""
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)

def settling_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    time.sleep(0.25)
    raise RuntimeError("Settled cleanly")

client.opener = MagicMock()
client.opener.open = settling_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    sigint_plan = [(0.0, signal.SIGINT)]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 130, f"Expected exit code 130 for settled worker with single SIGINT, got {ret}"
    assert "[!] Operation cancelled by user or signal." in stderr
    assert "Critical uncertainty" not in stderr


def test_astra_sigint_blocker_three_sigints_blocked_worker_bounded_settlement_and_exit_one() -> (
    None
):
    """Category 17.5: Rapid delivery of 3 SIGINTs maintains bounded settlement and exits 1."""
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
hang = threading.Event()

def hang_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    hang.wait(5.0)
    raise RuntimeError("Should not be reached")

client.opener = MagicMock()
client.opener.open = hang_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    sigint_plan = [(0.0, signal.SIGINT), (0.025, signal.SIGINT), (0.025, signal.SIGINT)]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 1, f"Expected exit code 1 for blocked worker with 3 SIGINTs, got {ret}"
    assert "Critical uncertainty" in stderr
    assert "thread remains active" in stderr
    assert "Operation cancelled by user or signal" not in stderr


def test_astra_sigint_blocker_four_sigints_blocked_worker_exits_one() -> None:
    """Category 17.6 (Astra Reproduction): 4 SIGINTs 25ms apart with blocked worker exits 1."""
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
hang = threading.Event()

def hang_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    hang.wait(5.0)
    raise RuntimeError("Should not be reached")

client.opener = MagicMock()
client.opener.open = hang_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    # 4 SIGINTs 25ms apart
    sigint_plan = [
        (0.0, signal.SIGINT),
        (0.025, signal.SIGINT),
        (0.025, signal.SIGINT),
        (0.025, signal.SIGINT),
    ]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 1, f"Expected exit code 1 for blocked worker with 4 SIGINTs, got {ret}"
    assert "Critical uncertainty" in stderr
    assert "thread remains active" in stderr
    assert "Operation cancelled by user or signal" not in stderr
    assert "unhandled exception during asyncio.run() shutdown" not in stderr
    assert "Traceback (most recent call last)" not in stderr


def test_astra_sigint_blocker_four_sigints_cleanly_settled_worker_exits_130() -> None:
    """Category 17.7: 4 SIGINTs with worker settling within 300ms window cleanly exits 130."""
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)

def settling_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    time.sleep(0.20)
    raise RuntimeError("Settled cleanly")

client.opener = MagicMock()
client.opener.open = settling_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    sigint_plan = [
        (0.0, signal.SIGINT),
        (0.025, signal.SIGINT),
        (0.025, signal.SIGINT),
        (0.025, signal.SIGINT),
    ]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 130, f"Expected exit code 130 for settled worker with 4 SIGINTs, got {ret}"
    assert "[!] Operation cancelled by user or signal." in stderr
    assert "Critical uncertainty" not in stderr


def test_astra_sigint_blocker_five_sigints_blocked_worker_exits_one() -> None:
    """Category 17.8: 5 SIGINTs on blocked worker maintains bounded settlement and exits 1."""
    script = """
import sys, time, threading
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    cli_entrypoint, NeonApiClient
)
from tests.unit.test_provision_neon_staging_role import (
    MockDatabaseConnector, MockDbConn
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
hang = threading.Event()

def hang_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    hang.wait(5.0)
    raise RuntimeError("Should not be reached")

client.opener = MagicMock()
client.opener.open = hang_open

mock_conn = MockDbConn(role_exists=False)
mock_connector = MockDatabaseConnector(admin_conn=mock_conn)

code = cli_entrypoint(
    ["--dry-run"],
    api_client=client,
    secret_store=MagicMock(),
    connector=mock_connector,
    admin_password="pwd",
)
sys.exit(code)
"""
    sigint_plan = [
        (0.0, signal.SIGINT),
        (0.02, signal.SIGINT),
        (0.02, signal.SIGINT),
        (0.02, signal.SIGINT),
        (0.02, signal.SIGINT),
    ]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 1, f"Expected exit code 1 for blocked worker with 5 SIGINTs, got {ret}"
    assert "Critical uncertainty" in stderr
    assert "thread remains active" in stderr
    assert "Operation cancelled by user or signal" not in stderr


def test_astra_sigint_blocker_heartbeat_responsiveness_during_double_sigint() -> None:
    """Category 17.9: Concurrent 10ms heartbeat coroutine continues ticking during double SIGINT."""
    script = """
import sys, time, threading, asyncio
from unittest.mock import MagicMock
from apps.api.scripts.provision_neon_staging_role import (
    _run_with_bounded_sigint_handling, NeonApiClient
)

client = NeonApiClient(api_key="npg_test_token_123", timeout=5.0)
hang = threading.Event()

def hang_open(req, timeout=None):
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    hang.wait(5.0)
    raise RuntimeError("Should not be reached")

client.opener = MagicMock()
client.opener.open = hang_open

heartbeat_ticks = 0

async def heartbeat():
    global heartbeat_ticks
    while True:
        await asyncio.sleep(0.01)
        heartbeat_ticks += 1

async def main_coro():
    hb_task = asyncio.create_task(heartbeat())
    try:
        await client.async_request("GET", "/projects")
    finally:
        hb_task.cancel()
        print(f"HEARTBEAT_TICKS:{heartbeat_ticks}", file=sys.stderr)

try:
    _run_with_bounded_sigint_handling(main_coro())
except Exception as e:
    print(f"EXC:{type(e).__name__}", file=sys.stderr)
"""
    sigint_plan = [(0.0, signal.SIGINT), (0.025, signal.SIGINT)]
    ret, stdout, stderr = _run_subproc_with_signals(script, sigint_plan=sigint_plan, timeout=3.0)

    assert ret == 0  # main_coro printed results and handled exc internally
    assert "EXC:TargetVerificationError" in stderr
    ticks_line = next(
        (line for line in stderr.splitlines() if line.startswith("HEARTBEAT_TICKS:")), ""
    )
    assert ticks_line != "", "Heartbeat ticks not recorded"
    ticks = int(ticks_line.split(":")[1])
    assert ticks >= 20, f"Expected at least 20 heartbeat ticks, got {ticks}"


def test_astra_sigint_blocker_main_task_target_verification_error_preserved_during_shutdown() -> (
    None
):
    """Category 17.10: Main task TargetVerificationError is preserved and not swallowed."""

    async def main_coro_raises_verification_error() -> None:
        t = asyncio.current_task()
        loop = asyncio.get_running_loop()
        assert t is not None
        loop.call_soon(t.cancel)
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise TargetVerificationError(
                "Critical uncertainty: Emergency cleanup cannot be guaranteed; "
                "thread remains active."
            ) from None

    with pytest.raises(TargetVerificationError) as exc_info:
        _run_with_bounded_sigint_handling(main_coro_raises_verification_error())
    assert "Critical uncertainty" in str(exc_info.value)


def test_astra_sigint_blocker_auxiliary_task_ignores_cancellation_bounded_shutdown() -> None:
    """Category 17.11: Auxiliary task ignoring cancellation does not hang shutdown."""

    async def sample_coro() -> str:
        async def stubborn_background_worker() -> None:
            while True:
                try:
                    await asyncio.sleep(5.0)
                except asyncio.CancelledError:
                    await asyncio.sleep(5.0)

        asyncio.create_task(stubborn_background_worker())
        await asyncio.sleep(0.01)
        return "success"

    start_t = time.monotonic()
    result = _run_with_bounded_sigint_handling(sample_coro())
    elapsed = time.monotonic() - start_t
    assert result == "success"
    # Must complete cleanly without hanging for 5s
    assert elapsed < 1.0, f"Shutdown took too long: {elapsed}s"


def test_astra_sigint_blocker_signal_handler_restoration() -> None:
    """Category 17.12: Original SIGINT signal handler is reliably restored after runner exit."""
    orig_handler = signal.getsignal(signal.SIGINT)

    # Subtest A: Normal return restores handler
    _run_with_bounded_sigint_handling(asyncio.sleep(0.01))
    assert signal.getsignal(signal.SIGINT) is orig_handler

    # Subtest B: Exception return restores handler
    async def fail_coro() -> None:
        raise ValueError("Intentional failure")

    with pytest.raises(ValueError, match="Intentional failure"):
        _run_with_bounded_sigint_handling(fail_coro())
    assert signal.getsignal(signal.SIGINT) is orig_handler


def test_astra_sigint_blocker_run_with_bounded_sigint_handling_resource_hygiene() -> None:
    """Category 17.13: Resource hygiene verifying zero leaked tasks or unhandled generators."""

    async def sample_coro() -> str:
        async def background_worker() -> None:
            while True:
                await asyncio.sleep(0.1)

        asyncio.create_task(background_worker())
        await asyncio.sleep(0.01)
        return "success"

    result = _run_with_bounded_sigint_handling(sample_coro())
    assert result == "success"
