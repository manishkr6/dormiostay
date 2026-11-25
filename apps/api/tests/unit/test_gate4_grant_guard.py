"""Focused regression tests for Gate 4 zero-table-grant security guard and execution wrapper.

Verifies:
1. Fix 1: Single transaction boundary in execute_gate4_grants_async, preflight in transaction,
   automatic rollback on exception, and real SQLAlchemy transaction lifecycle without autobegin.
2. Fix 2: Effective column privilege checks (has_any_column_privilege, audit_logs.action,
   raw pg_attribute.attacl, raw pg_class.relacl, 7 table privileges, GRANT OPTION, default ACLs).
3. Fix 3: Object ownership (relations, schemas, routines, types), memberships, non-system schemas,
   and callable SECURITY DEFINER function inspection.
4. Fix 4: Removal of standalone psql builder, non-executable SQL template, fail-closed metadata,
   and rejection of protected production branch.
5. Fix 5: Complete governance of 11 application tables and alembic_version (12 governed tables).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InvalidRequestError

from app.db.grant_guard import (
    ALL_GOVERNED_TABLES,
    ALL_TABLE_PRIVILEGES,
    ALLOWED_SECURITY_DEFINER_ROUTINES,
    APPLICATION_TABLES,
    APPROVED_APPLICATION_ROLE,
    COLUMN_PRIVILEGES,
    MONITORED_RELATION_KINDS,
    execute_gate4_grants_async,
    resolve_grant_target,
    verify_catalog_privileges,
    verify_grant_connection,
)
from app.db.migration_guard import (
    APPROVED_STAGING_BRANCH_ID,
    APPROVED_STAGING_DATABASE,
    APPROVED_STAGING_ENDPOINT_ID,
    APPROVED_STAGING_HOST,
    APPROVED_STAGING_MIGRATION_ROLE,
    APPROVED_STAGING_PROJECT_ID,
    PROTECTED_PRODUCTION_BRANCH_ID,
    ApprovedTargetIdentity,
)

VALID_DIRECT_URL = (
    f"postgresql+asyncpg://{APPROVED_STAGING_MIGRATION_ROLE}:secret@"
    f"{APPROVED_STAGING_HOST}:5432/{APPROVED_STAGING_DATABASE}?sslmode=verify-full"
)


def _mock_transport() -> MagicMock:
    transport = MagicMock()

    def _get(path: str) -> dict[str, Any]:
        if path == f"/projects/{APPROVED_STAGING_PROJECT_ID}":
            return {"project": {"id": APPROVED_STAGING_PROJECT_ID}}
        if path == f"/projects/{APPROVED_STAGING_PROJECT_ID}/branches":
            return {
                "branches": [
                    {"id": APPROVED_STAGING_BRANCH_ID, "name": "staging"},
                    {"id": PROTECTED_PRODUCTION_BRANCH_ID, "name": "production"},
                ]
            }
        if path == f"/projects/{APPROVED_STAGING_PROJECT_ID}/endpoints":
            return {
                "endpoints": [
                    {
                        "id": APPROVED_STAGING_ENDPOINT_ID,
                        "branch_id": APPROVED_STAGING_BRANCH_ID,
                        "host": APPROVED_STAGING_HOST,
                    }
                ]
            }
        raise ValueError(f"Unexpected path: {path}")

    transport.get.side_effect = _get
    return transport


def _make_approved_target() -> ApprovedTargetIdentity:
    return ApprovedTargetIdentity(
        host=APPROVED_STAGING_HOST,
        database=APPROVED_STAGING_DATABASE,
        migration_role=APPROVED_STAGING_MIGRATION_ROLE,
        project_id=APPROVED_STAGING_PROJECT_ID,
        branch_id=APPROVED_STAGING_BRANCH_ID,
        endpoint_id=APPROVED_STAGING_ENDPOINT_ID,
        control_plane_verified=True,
    )


def _make_mock_connection(
    app_table_privs: dict[tuple[str, str], bool] | None = None,
    public_table_privs: dict[tuple[str, str], bool] | None = None,
    app_col_privs: dict[tuple[str, str], bool] | None = None,
    public_col_privs: dict[tuple[str, str], bool] | None = None,
    audit_action_select: bool = False,
    raw_col_acls: list[Any] | None = None,
    raw_tbl_acls: list[Any] | None = None,
    grant_opts: list[Any] | None = None,
    defacls: list[Any] | None = None,
    non_sys_schemas: list[str] | None = None,
    secdefs: list[Any] | None = None,
    app_fn_exec: bool = False,
    pub_fn_exec: bool = False,
    db_connect: bool = True,
    db_create: bool = False,
    sch_usage: bool = True,
    sch_create: bool = False,
    owned_tables: int = 0,
    owned_rels: int = 0,
    owned_schemas: int = 0,
    owned_procs: int = 0,
    owned_types: int = 0,
    relations: list[Any] | None = None,
    branch_id: str = APPROVED_STAGING_BRANCH_ID,
    project_id: str = APPROVED_STAGING_PROJECT_ID,
    endpoint_id: str = APPROVED_STAGING_ENDPOINT_ID,
) -> AsyncMock:
    """Build a mock AsyncConnection configured for catalog verification."""
    conn = AsyncMock()
    tx = AsyncMock()
    tx.__aenter__.return_value = tx
    tx.__aexit__.return_value = None
    conn.begin = MagicMock(return_value=tx)
    conn.in_transaction = MagicMock(return_value=False)

    app_priv_map = app_table_privs or {}
    pub_priv_map = public_table_privs or {}
    app_col_map = app_col_privs or {}
    pub_col_map = public_col_privs or {}
    schemas_list = non_sys_schemas if non_sys_schemas is not None else ["public"]
    default_relations = [
        ("public", t, "r", APPROVED_STAGING_MIGRATION_ROLE) for t in ALL_GOVERNED_TABLES
    ]
    rel_rows = relations if relations is not None else default_relations

    async def mock_scalar(statement: Any, params: Any = None) -> Any:
        stmt = str(statement)
        p = params or {}
        role = p.get("role", "") or p.get("app_role", "")
        tbl = p.get("tbl", "")
        priv = p.get("priv", "")
        sch = p.get("sch", "")

        # 1. has_table_privilege
        if "has_table_privilege" in stmt:
            if "has_table_privilege('public'" in stmt:
                return pub_priv_map.get((tbl, priv), False)
            if role == APPROVED_APPLICATION_ROLE:
                return app_priv_map.get((tbl, priv), False)
            return False

        # 2. has_any_column_privilege
        if "has_any_column_privilege" in stmt:
            if "has_any_column_privilege('public'" in stmt:
                return pub_col_map.get((tbl, priv), False)
            if role == APPROVED_APPLICATION_ROLE:
                return app_col_map.get((tbl, priv), False)
            return False

        # 3. has_column_privilege on audit_logs.action
        if "has_column_privilege" in stmt and "audit_logs" in stmt:
            return audit_action_select

        # 4. Database privileges
        if "has_database_privilege" in stmt:
            if "'CONNECT'" in stmt:
                return db_connect
            if "'CREATE'" in stmt:
                return db_create
            return False

        # 5. Schema privileges
        if "has_schema_privilege" in stmt:
            if "'USAGE'" in stmt:
                if sch == "public":
                    return sch_usage
                return False
            if "'CREATE'" in stmt:
                return sch_create
            return False

        # 6. Function execution privileges
        if "has_function_privilege" in stmt:
            if "'public'" in stmt:
                return pub_fn_exec
            return app_fn_exec

        # 7. Ownership checks
        if "count(*) FROM pg_class" in stmt:
            return owned_rels
        if "count(*) FROM pg_namespace" in stmt:
            return owned_schemas
        if "count(*) FROM pg_proc" in stmt:
            return owned_procs
        if "count(*) FROM pg_type" in stmt:
            return owned_types
        if "count(*) FROM pg_tables" in stmt:
            return owned_tables

        # 8. Role memberships
        if "EXISTS (SELECT 1 FROM pg_auth_members" in stmt:
            return False

        # 9. current_database()
        if "current_database()" in stmt:
            return APPROVED_STAGING_DATABASE

        return False

    async def mock_execute(statement: Any, params: Any = None) -> Any:
        stmt = str(statement)
        res = MagicMock()
        empty_rows: list[Any] = []

        # Database admin identity
        if "SELECT current_user, session_user" in stmt:
            res.first.return_value = (
                APPROVED_STAGING_MIGRATION_ROLE,
                APPROVED_STAGING_MIGRATION_ROLE,
            )
            return res

        # pg_settings for Neon metadata
        if "FROM pg_settings WHERE name IN" in stmt:
            settings_data: list[tuple[str, str]] = []
            if branch_id:
                settings_data.append(("neon.branch_id", branch_id))
            if project_id:
                settings_data.append(("neon.project_id", project_id))
            if endpoint_id:
                settings_data.append(("neon.endpoint_id", endpoint_id))
            res.fetchall.return_value = settings_data
            return res

        # pg_roles
        if "FROM pg_roles WHERE rolname = :app_role" in stmt:
            # rolcanlogin=True, all elevated attributes False
            res.first.return_value = (True, False, False, False, False, False)
            return res

        # Relation inventory
        if "FROM pg_class c" in stmt and "c.relkind" in stmt:
            res.fetchall.return_value = rel_rows
            return res

        # Raw column ACLs
        if "FROM pg_attribute a" in stmt:
            res.fetchall.return_value = raw_col_acls if raw_col_acls is not None else empty_rows
            return res

        # Raw table ACLs
        if "FROM pg_class c" in stmt and "c.relacl" in stmt:
            res.fetchall.return_value = raw_tbl_acls if raw_tbl_acls is not None else empty_rows
            return res

        # GRANT OPTION
        if "is_grantable = 'YES'" in stmt:
            res.fetchall.return_value = grant_opts if grant_opts is not None else empty_rows
            return res

        # Default ACLs
        if "FROM pg_default_acl" in stmt:
            res.fetchall.return_value = defacls if defacls is not None else empty_rows
            return res

        # Non-system schemas inventory
        if "FROM pg_namespace" in stmt and "left(nspname, 3)" in stmt:
            res.fetchall.return_value = [(s,) for s in schemas_list]
            return res

        # SECURITY DEFINER functions & non-system schema routines
        if "FROM pg_proc p" in stmt:
            res.fetchall.return_value = secdefs if secdefs is not None else empty_rows
            return res

        res.fetchall.return_value = empty_rows
        return res

    conn.scalar.side_effect = mock_scalar
    conn.execute.side_effect = mock_execute
    return conn


# =====================================================================
# 1. Foundation Schema Inventory (Fix 5)
# =====================================================================


def test_zero_grant_policy_constants_and_foundation_inventory() -> None:
    """Fix 5: Exactly 11 foundation application tables + alembic_version governed."""
    expected_app_tables = (
        "amenities",
        "locations",
        "platform_settings",
        "property_categories",
        "institutions",
        "users",
        "user_profiles",
        "user_sessions",
        "email_verification_tokens",
        "password_reset_tokens",
        "audit_logs",
    )
    assert APPLICATION_TABLES == expected_app_tables
    assert len(APPLICATION_TABLES) == 11
    assert "alembic_version" not in APPLICATION_TABLES

    # Nonexistent tables must NOT be present
    for nonexistent in ("user_identities", "password_credentials", "outbox_events", "properties"):
        assert nonexistent not in APPLICATION_TABLES
        assert nonexistent not in ALL_GOVERNED_TABLES

    # 12 governed tables total
    assert len(ALL_GOVERNED_TABLES) == 12
    assert "alembic_version" in ALL_GOVERNED_TABLES

    assert set(ALL_TABLE_PRIVILEGES) == {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    }
    assert set(COLUMN_PRIVILEGES) == {"SELECT", "INSERT", "UPDATE", "REFERENCES"}
    assert ALLOWED_SECURITY_DEFINER_ROUTINES == frozenset()


# =====================================================================
# 2. Single-Transaction Boundary & Simulated AsyncConnection Lifecycle (Simulated Unit Mocks)
# =====================================================================


class StatefulTransactionConnection:
    """Simulated async connection mock modeling autobegin and transaction state transitions.

    Note: This is a stateful unit mock for testing execute_gate4_grants_async control flow,
    NOT a live PostgreSQL integration test. Genuine SQLAlchemy 2.0 transaction machinery is
    tested separately in test_genuine_sqlalchemy_transaction_machinery_offline.
    """

    def __init__(self) -> None:
        self._in_transaction = False
        self.committed = False
        self.rolled_back = False
        self.executed_statements: list[str] = []

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def execute(self, statement: Any, params: Any = None) -> Any:
        stmt = str(statement)
        self.executed_statements.append(stmt)
        # SQLAlchemy 2.0 autobegin: executing a query starts an implicit tx
        if not self._in_transaction:
            self._in_transaction = True
        res = MagicMock()
        res.first.return_value = (
            APPROVED_STAGING_MIGRATION_ROLE,
            APPROVED_STAGING_MIGRATION_ROLE,
        )
        res.fetchall.return_value = [("neon.branch_id", APPROVED_STAGING_BRANCH_ID)]
        return res

    async def scalar(self, statement: Any, params: Any = None) -> Any:
        stmt = str(statement)
        self.executed_statements.append(stmt)
        if not self._in_transaction:
            self._in_transaction = True
        if "current_database()" in stmt:
            return APPROVED_STAGING_DATABASE
        if "has_database_privilege" in stmt and "'CONNECT'" in stmt:
            return True
        if "has_schema_privilege" in stmt and "'USAGE'" in stmt and "'public'" in stmt:
            return True
        return False

    def begin(self) -> Any:
        if self._in_transaction:
            raise InvalidRequestError("A transaction is already begun on this connection.")
        self._in_transaction = True
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=tx)

        async def _aexit(exc_type: Any, exc_val: Any, tb: Any) -> None:
            if exc_type is not None:
                self.rolled_back = True
                self._in_transaction = False
            else:
                self.committed = True
                self._in_transaction = False

        tx.__aexit__ = AsyncMock(side_effect=_aexit)
        return tx


def test_genuine_sqlalchemy_transaction_machinery_offline() -> None:
    """Fix 3: Verify genuine SQLAlchemy 2.0 transaction machinery offline on SQLite in-memory.

    Demonstrates:
    1. Autobegin collision: Executing a query triggers autobegin, causing subsequent begin()
       to fail with InvalidRequestError (Astra reproduction).
    2. Single begin() boundary: Opening transaction with begin() before any SQL succeeds.
    3. Automatic rollback: Raising an exception inside begin() rolls back uncommitted changes.

    Note: This test exercises genuine SQLAlchemy 2.0 transaction state machine machinery offline.
    Live PostgreSQL integration tests remain explicitly unverified without disposable local DB.
    """
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite:///:memory:")

    # 1. Autobegin collision: executing a query before begin()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        with pytest.raises(
            InvalidRequestError,
            match="This connection has already initialized a SQLAlchemy Transaction",
        ):
            with conn.begin():
                pass

    # 2. Single begin() boundary: transaction opened before any query
    with engine.connect() as conn:
        with conn.begin():
            res = conn.execute(text("SELECT 42")).scalar()
            assert res == 42

    # 3. Automatic rollback on exception
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id int)"))
        conn.commit()
        with pytest.raises(ValueError, match="Simulated error"):
            with conn.begin():
                conn.execute(text("INSERT INTO t VALUES (1)"))
                raise ValueError("Simulated error")

        count = conn.execute(text("SELECT count(*) FROM t")).scalar()
        assert count == 0


@pytest.mark.anyio
async def test_sqlalchemy_autobegin_conflict_reproduced() -> None:
    """Fix 1: Reproduce Astra's InvalidRequestError when queries execute before begin()."""
    conn = StatefulTransactionConnection()

    # Preflight query runs outside helper -> causes autobegin
    await conn.execute("SELECT current_user, session_user")
    assert conn.in_transaction() is True

    # Helper calling conn.begin() after preflight query must raise InvalidRequestError
    with pytest.raises(
        InvalidRequestError, match="A transaction is already begun on this connection"
    ):
        conn.begin()


@pytest.mark.anyio
async def test_execute_gate4_grants_async_enforces_clean_initial_state() -> None:
    """Fix 1: execute_gate4_grants_async rejects connection if transaction already begun."""
    conn = AsyncMock()
    conn.in_transaction = MagicMock(return_value=True)  # Already begun

    target = _make_approved_target()
    with pytest.raises(InvalidRequestError, match="Connection is already in an active transaction"):
        await execute_gate4_grants_async(conn, expected_target=target)


@pytest.mark.anyio
async def test_execute_gate4_grants_async_single_transaction_lifecycle() -> None:
    """Fix 1: Starts exactly ONE transaction, executes preflight inside, verifies, and commits."""
    conn = _make_mock_connection()
    target = _make_approved_target()

    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        results = await execute_gate4_grants_async(
            conn,
            expected_target=target,
            target_role=APPROVED_APPLICATION_ROLE,
        )

    # 1. begin() called exactly once
    conn.begin.assert_called_once()
    assert len(results["table_privileges"]) == 12
    assert "zero_effective_column_privileges_verified" in results["catalog_assertions"]


@pytest.mark.anyio
async def test_execute_gate4_grants_async_automatic_rollback_on_preflight_failure() -> None:
    """Fix 1: Preflight failure inside single transaction triggers automatic rollback."""
    # Simulate wrong administrator in database
    conn = _make_mock_connection()
    user_res = MagicMock()
    user_res.first.return_value = ("unauthorized_user", "unauthorized_user")
    conn.execute.side_effect = None
    conn.execute.return_value = user_res

    target = _make_approved_target()
    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="differs from approved administrator"):
            await execute_gate4_grants_async(conn, expected_target=target)

    # begin context entered and exited with exception -> automatic rollback
    conn.begin.return_value.__aenter__.assert_called_once()
    conn.begin.return_value.__aexit__.assert_called_once()


# =====================================================================
# 3. Fix 2 — Effective Column Privilege Leakage & Raw ACL Inspection
# =====================================================================


@pytest.mark.anyio
async def test_effective_column_privilege_has_any_column_privilege_rejected() -> None:
    """Fix 2: has_any_column_privilege detecting column grant on any table is rejected."""
    for priv in COLUMN_PRIVILEGES:
        conn = _make_mock_connection(app_col_privs={("public.users", priv): True})
        with pytest.raises(
            RuntimeError,
            match=f"Effective column privilege leakage: dormiostay_app has unexpected {priv}",
        ):
            await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_effective_column_privilege_public_has_any_column_privilege_rejected() -> None:
    """Fix 2: has_any_column_privilege detecting PUBLIC column grant is rejected."""
    conn = _make_mock_connection(public_col_privs={("public.audit_logs", "SELECT"): True})
    with pytest.raises(
        RuntimeError,
        match="Effective column privilege leakage: PUBLIC has unexpected SELECT",
    ):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_audit_logs_action_column_select_rejection() -> None:
    """Fix 2: Specific column-level SELECT on audit_logs.action is rejected."""
    conn = _make_mock_connection(audit_action_select=True)
    with pytest.raises(
        RuntimeError,
        match="Effective column privilege leakage: dormiostay_app has SELECT on audit_logs.action",
    ):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_raw_column_attacl_leakage_rejected() -> None:
    """Fix 2: Inspect raw pg_attribute.attacl and reject any grant to dormiostay_app or PUBLIC."""
    # Raw attacl entry granting SELECT to dormiostay_app
    mock_row = ("users", "email", ["dormiostay_app=r/neondb_owner"])
    conn = _make_mock_connection(raw_col_acls=[mock_row])
    with pytest.raises(
        RuntimeError, match="Raw column ACL leakage: public.users.email has unexpected ACL"
    ):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)

    # Raw attacl entry granting to PUBLIC
    mock_row_pub = ("audit_logs", "action", ["=r/neondb_owner"])
    conn_pub = _make_mock_connection(raw_col_acls=[mock_row_pub])
    with pytest.raises(
        RuntimeError, match="Raw column ACL leakage: public.audit_logs.action has unexpected ACL"
    ):
        await verify_catalog_privileges(conn_pub, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_raw_table_relacl_leakage_rejected() -> None:
    """Fix 2: Inspect raw pg_class.relacl and reject any grant to dormiostay_app or PUBLIC."""
    mock_row = ("users", ["dormiostay_app=r/neondb_owner"])
    conn = _make_mock_connection(raw_tbl_acls=[mock_row])
    with pytest.raises(
        RuntimeError, match="Raw table ACL leakage: public.users has unexpected relacl"
    ):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_all_seven_table_privileges_rejected() -> None:
    """Fix 2: Verify all 7 table privilege types are rejected across governed tables."""
    for priv in ALL_TABLE_PRIVILEGES:
        conn = _make_mock_connection(app_table_privs={("public.users", priv): True})
        with pytest.raises(
            RuntimeError, match=f"dormiostay_app has unexpected {priv} privilege on public.users"
        ):
            await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_grant_option_rejected() -> None:
    """Fix 2: Table or column GRANT OPTION is strictly rejected."""
    mock_row = MagicMock()
    conn = _make_mock_connection(grant_opts=[mock_row])
    with pytest.raises(RuntimeError, match="holds GRANT OPTION"):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_default_acl_leakage_rejected() -> None:
    """Fix 2: Default ACLs granting privileges to dormiostay_app or PUBLIC are rejected."""
    conn = _make_mock_connection(defacls=[("neondb_owner", "r", ["dormiostay_app=r/neondb_owner"])])
    with pytest.raises(RuntimeError, match="Default ACL leaks privileges"):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


# =====================================================================
# 4. Fix 3 — Object Ownership, Schemas, & SECURITY DEFINER Functions
# =====================================================================


@pytest.mark.anyio
async def test_object_ownership_in_pg_class_rejected() -> None:
    """Fix 3: Application role owning relations in pg_class is rejected."""
    conn = _make_mock_connection(owned_rels=1)
    target = _make_approved_target()
    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="owns 1 relation\\(s\\)"):
            await verify_grant_connection(conn, expected_target=target)


@pytest.mark.anyio
async def test_object_ownership_in_pg_namespace_rejected() -> None:
    """Fix 3: Application role owning schemas in pg_namespace is rejected."""
    conn = _make_mock_connection(owned_schemas=1)
    target = _make_approved_target()
    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="owns 1 schema\\(s\\)"):
            await verify_grant_connection(conn, expected_target=target)


@pytest.mark.anyio
async def test_object_ownership_in_pg_proc_rejected() -> None:
    """Fix 3: Application role owning routines in pg_proc is rejected."""
    conn = _make_mock_connection(owned_procs=1)
    target = _make_approved_target()
    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="owns 1 routine\\(s\\)"):
            await verify_grant_connection(conn, expected_target=target)


@pytest.mark.anyio
async def test_object_ownership_in_pg_type_rejected() -> None:
    """Fix 3: Application role owning types in pg_type is rejected."""
    conn = _make_mock_connection(owned_types=1)
    target = _make_approved_target()
    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="owns 1 type\\(s\\)"):
            await verify_grant_connection(conn, expected_target=target)


@pytest.mark.anyio
async def test_unexpected_non_system_schema_usage_rejected() -> None:
    """Fix 3: Non-system schemas other than public cannot have USAGE privilege."""
    conn = _make_mock_connection(non_sys_schemas=["public", "internal_secret"])

    async def mock_schema_scalar(statement: Any, params: Any = None) -> Any:
        stmt = str(statement)
        p = params or {}
        if "has_schema_privilege" in stmt and "'USAGE'" in stmt:
            if p.get("sch") == "internal_secret":
                return True  # Unauthorized USAGE on internal_secret
            if p.get("sch") == "public":
                return True
        return False

    conn.scalar.side_effect = mock_schema_scalar
    with pytest.raises(RuntimeError, match="unexpected USAGE on schema 'internal_secret'"):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_security_definer_function_outside_allowlist_rejected() -> None:
    """Fix 3: SECURITY DEFINER routines in non-system schemas outside allowlist are rejected."""
    mock_secdef = ("public", "escalate_to_superuser", 12345)
    conn = _make_mock_connection(secdefs=[mock_secdef])
    with pytest.raises(
        RuntimeError,
        match="Unexpected SECURITY DEFINER routine 'public.escalate_to_superuser' found",
    ):
        await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


@pytest.mark.anyio
async def test_security_definer_function_executable_by_app_or_public_rejected() -> None:
    """Fix 3: Even allowed SECURITY DEFINER functions cannot be executable by app or PUBLIC."""
    mock_secdef = ("public", "dormiostay_approved_fn", 99999)
    conn = _make_mock_connection(secdefs=[mock_secdef], app_fn_exec=True)

    with patch(
        "app.db.grant_guard.ALLOWED_SECURITY_DEFINER_ROUTINES",
        {("public", "dormiostay_approved_fn")},
    ):
        with pytest.raises(RuntimeError, match="has EXECUTE on SECURITY DEFINER routine"):
            await verify_catalog_privileges(conn, target_role=APPROVED_APPLICATION_ROLE)


# =====================================================================
# 5. Fix 4 — Standalone Execution Removal & Fail-Closed Neon Checks
# =====================================================================


def test_grant_application_role_sql_is_non_executable() -> None:
    """Fix 4: grant_application_role.sql contains an immediate RAISE EXCEPTION."""
    from pathlib import Path

    sql_path = (
        Path(__file__).resolve().parent.parent.parent / "scripts" / "grant_application_role.sql"
    )
    assert sql_path.is_file()
    content = sql_path.read_text(encoding="utf-8")

    # Immediate exception to block psql execution
    assert "RAISE EXCEPTION" in content
    assert "Direct SQL execution of grant_application_role.sql is strictly prohibited" in content

    # No executable copy-paste psql instructions
    assert "psql -X" not in content
    assert "GRANT SELECT ON" not in content


def test_psql_command_builder_removed_from_module() -> None:
    """Fix 4: build_safe_psql_command removed to enforce single Python execution path."""
    import app.db.grant_guard as mod

    assert not hasattr(mod, "build_safe_psql_command")


@pytest.mark.anyio
async def test_fail_closed_on_missing_neon_branch_in_pg_settings() -> None:
    """Fix 4: Fail closed if neon.branch_id is missing from pg_settings."""
    conn = _make_mock_connection(branch_id="")  # Missing branch_id
    target = _make_approved_target()

    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        with pytest.raises(
            RuntimeError, match="Missing authoritative Neon metadata in pg_settings"
        ):
            await verify_grant_connection(conn, expected_target=target)


@pytest.mark.anyio
async def test_reject_production_branch_in_pg_settings() -> None:
    """Fix 4: Reject connection immediately if pg_settings indicates production branch."""
    conn = _make_mock_connection(branch_id=PROTECTED_PRODUCTION_BRANCH_ID)
    target = _make_approved_target()

    with patch("app.db.grant_guard._verify_client_tls", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="Connected to protected production branch"):
            await verify_grant_connection(conn, expected_target=target)


def test_resolve_grant_target_rejects_insecure_transport_and_production() -> None:
    """Fix 4: resolve_grant_target enforces TLS, staging confirmation, and rejects production."""
    prod_url = (
        f"postgresql+asyncpg://{APPROVED_STAGING_MIGRATION_ROLE}:secret@"
        f"ep-prod.neon.tech:5432/neondb?sslmode=verify-full&branch={PROTECTED_PRODUCTION_BRANCH_ID}"
    )
    with pytest.raises(ValueError, match="protected production branch is strictly forbidden"):
        resolve_grant_target(
            direct_url=prod_url,
            migration_env="staging",
            migration_confirmation="staging",
            target_role=APPROVED_APPLICATION_ROLE,
            control_plane_transport=_mock_transport(),
        )

    insecure_url = (
        f"postgresql+asyncpg://{APPROVED_STAGING_MIGRATION_ROLE}:secret@"
        f"{APPROVED_STAGING_HOST}:5432/{APPROVED_STAGING_DATABASE}?sslmode=require"
    )
    with pytest.raises(ValueError, match="require authentic TLS"):
        resolve_grant_target(
            direct_url=insecure_url,
            migration_env="staging",
            migration_confirmation="staging",
            target_role=APPROVED_APPLICATION_ROLE,
            control_plane_transport=_mock_transport(),
        )


# =====================================================================
# 7. Unexpected View & Relation Governance (Fix 1)
# =====================================================================


@pytest.mark.anyio
async def test_unexpected_admin_owned_view_exposing_users_rejected() -> None:
    """Fix 1: Administrator-owned view public.users_export is rejected if accessible."""
    unexpected_relations = [
        ("public", t, "r", APPROVED_STAGING_MIGRATION_ROLE) for t in ALL_GOVERNED_TABLES
    ] + [("public", "users_export", "v", APPROVED_STAGING_MIGRATION_ROLE)]

    # dormiostay_app has SELECT privilege on users_export
    app_privs = {('"public"."users_export"', "SELECT"): True}
    conn = _make_mock_connection(
        relations=unexpected_relations,
        app_table_privs=app_privs,
    )

    expected_msg = (
        "unexpected relation 'public.users_export' \\(kind 'v', owner 'neondb_owner'\\) "
        "is accessible to dormiostay_app"
    )
    with pytest.raises(RuntimeError, match=expected_msg):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_unexpected_admin_owned_view_accessible_via_public_rejected() -> None:
    """Fix 1: View public.users_export accessible to PUBLIC is detected and rejected."""
    unexpected_relations = [
        ("public", t, "r", APPROVED_STAGING_MIGRATION_ROLE) for t in ALL_GOVERNED_TABLES
    ] + [("public", "users_export", "v", APPROVED_STAGING_MIGRATION_ROLE)]

    # PUBLIC has SELECT privilege on users_export (via explicit grant, altered default ACLs,
    # or inheritance; PostgreSQL does not automatically grant PUBLIC SELECT on new views)
    pub_privs = {('"public"."users_export"', "SELECT"): True}
    conn = _make_mock_connection(
        relations=unexpected_relations,
        public_table_privs=pub_privs,
    )

    expected_msg = (
        "unexpected relation 'public.users_export' \\(kind 'v', owner 'neondb_owner'\\) "
        "is accessible to PUBLIC"
    )
    with pytest.raises(RuntimeError, match=expected_msg):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_unexpected_materialized_view_rejected_if_accessible() -> None:
    """Fix 1: Unexpected materialized view in public is rejected if accessible."""
    unexpected_relations = [
        ("public", t, "r", APPROVED_STAGING_MIGRATION_ROLE) for t in ALL_GOVERNED_TABLES
    ] + [("public", "user_metrics_mat", "m", APPROVED_STAGING_MIGRATION_ROLE)]

    conn = _make_mock_connection(
        relations=unexpected_relations,
        app_table_privs={('"public"."user_metrics_mat"', "SELECT"): True},
    )

    with pytest.raises(
        RuntimeError,
        match="unexpected relation 'public.user_metrics_mat' \\(kind 'm'",
    ):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_unexpected_relation_column_privilege_leakage_rejected() -> None:
    """Fix 1: Unexpected relation leaking column-level privilege is rejected."""
    unexpected_relations = [
        ("public", t, "r", APPROVED_STAGING_MIGRATION_ROLE) for t in ALL_GOVERNED_TABLES
    ] + [("public", "custom_audit_view", "v", APPROVED_STAGING_MIGRATION_ROLE)]

    conn = _make_mock_connection(
        relations=unexpected_relations,
        app_col_privs={('"public"."custom_audit_view"', "SELECT"): True},
    )

    with pytest.raises(
        RuntimeError,
        match="unexpected relation 'public.custom_audit_view' .* leaks column privilege SELECT",
    ):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_relation_inventory_fails_closed_on_missing_governed_table() -> None:
    """Fix 1: Relation inventory fails closed if any of the 12 governed tables is missing."""
    incomplete_relations = [
        ("public", t, "r", APPROVED_STAGING_MIGRATION_ROLE)
        for t in ALL_GOVERNED_TABLES
        if t != "audit_logs"
    ]
    conn = _make_mock_connection(relations=incomplete_relations)

    with pytest.raises(
        RuntimeError,
        match="expected governed table 'public.audit_logs' missing from relation inventory",
    ):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_relation_inventory_fails_closed_on_empty_catalog() -> None:
    """Fix 1: Relation inventory fails closed on empty catalog result."""
    conn = _make_mock_connection(relations=[])

    with pytest.raises(
        RuntimeError,
        match="relation inventory returned zero rows",
    ):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_relation_inventory_fails_closed_on_unexpected_relkind_for_governed_table() -> None:
    """Fix 1: Governed table must be an ordinary table ('r'), not a view or foreign table."""
    bad_kind_relations = [
        ("public", t, "v" if t == "users" else "r", APPROVED_STAGING_MIGRATION_ROLE)
        for t in ALL_GOVERNED_TABLES
    ]
    conn = _make_mock_connection(relations=bad_kind_relations)

    with pytest.raises(
        RuntimeError,
        match="expected table 'public.users' has unexpected relkind 'v'",
    ):
        await verify_catalog_privileges(conn)


# =====================================================================
# 8. Literal Schema Prefix Matching & Unexpected Schema Routines (Fix 2)
# =====================================================================


@pytest.mark.anyio
async def test_literal_schema_prefix_matching_includes_pgextra() -> None:
    """Fix 2: left(nspname, 3) <> 'pg_' includes pgextra (unlike wildcard NOT LIKE 'pg_%')."""
    conn = _make_mock_connection(
        non_sys_schemas=["public", "pgextra"],
    )

    async def mock_scalar_with_pgextra_usage(statement: Any, params: Any = None) -> Any:
        stmt = str(statement)
        if "has_schema_privilege" in stmt and "'USAGE'" in stmt:
            # Both public and pgextra report USAGE = True
            return True
        if "has_schema_privilege" in stmt and "'CREATE'" in stmt:
            return False
        if "has_database_privilege" in stmt and "'CONNECT'" in stmt:
            return True
        if "count(*) FROM pg_tables" in stmt:
            return 0
        return False

    conn.scalar.side_effect = mock_scalar_with_pgextra_usage

    with pytest.raises(
        RuntimeError,
        match="Security violation: role 'dormiostay_app' has unexpected USAGE on schema 'pgextra'",
    ):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_unexpected_schema_accessible_routine_rejected() -> None:
    """Fix 2: Callable routine in unexpected schema pgextra is rejected."""
    pgextra_routine = [("pgextra", "export_data", 12345, False)]
    conn = _make_mock_connection(
        non_sys_schemas=["public", "pgextra"],
        secdefs=pgextra_routine,
        app_fn_exec=True,  # dormiostay_app can execute export_data
    )

    expected_msg = (
        "routine 'pgextra.export_data' in unexpected schema 'pgextra' "
        "is executable by dormiostay_app"
    )
    with pytest.raises(RuntimeError, match=expected_msg):
        await verify_catalog_privileges(conn)


@pytest.mark.anyio
async def test_unexpected_schema_routine_accessible_to_public_rejected() -> None:
    """Fix 2: Routine in unexpected schema pgextra accessible to PUBLIC is rejected."""
    pgextra_routine = [("pgextra", "get_status", 54321, False)]
    conn = _make_mock_connection(
        non_sys_schemas=["public", "pgextra"],
        secdefs=pgextra_routine,
        pub_fn_exec=True,  # PUBLIC can execute get_status
    )

    with pytest.raises(
        RuntimeError,
        match="routine 'pgextra.get_status' in unexpected schema 'pgextra' is executable by PUBLIC",
    ):
        await verify_catalog_privileges(conn)


# =====================================================================
# 9. asyncpg relkind Array Codec Compatibility
# =====================================================================


@pytest.mark.anyio
async def test_relation_inventory_query_syntax_and_parameter_binding() -> None:
    """Validate relkind query syntax and parameter types for asyncpg compatibility.

    Ensures that:
    1. c.relkind is cast to text (c.relkind::text) in both SELECT and WHERE.
    2. :monitored_kinds is bound to a list of strings and cast via CAST(:monitored_kinds AS text[]).
    3. The query compiles cleanly for PostgreSQL dialect without ambiguous colons.
    4. Passing Python str elements avoids asyncpg internal 'char' array codec (DataError).
    """
    from sqlalchemy.dialects import postgresql

    conn = _make_mock_connection()

    await verify_catalog_privileges(conn)

    # Inspect the calls made to conn.execute
    rel_inv_calls = [
        call
        for call in conn.execute.call_args_list
        if "FROM pg_class c" in str(call[0][0]) and "c.relkind" in str(call[0][0])
    ]
    assert len(rel_inv_calls) == 1
    call_args = rel_inv_calls[0][0]
    sql_stmt = str(call_args[0])
    params = call_args[1] if len(call_args) > 1 else {}

    assert "c.relkind::text AS relkind" in sql_stmt
    assert "c.relkind::text = ANY(CAST(:monitored_kinds AS text[]))" in sql_stmt
    assert "monitored_kinds" in params
    assert isinstance(params["monitored_kinds"], list)
    assert all(isinstance(k, str) for k in params["monitored_kinds"])
    assert params["monitored_kinds"] == list(MONITORED_RELATION_KINDS)

    compiled = text(sql_stmt).compile(
        dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
    )
    compiled_str = str(compiled)
    assert "c.relkind::text = ANY(CAST(%(monitored_kinds)s AS text[]))" in compiled_str
    assert "monitored_kinds" in compiled.params
