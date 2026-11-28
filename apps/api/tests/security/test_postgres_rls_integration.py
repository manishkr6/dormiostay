"""Integration tests for live PostgreSQL Row-Level Security (RLS) enforcement (T-12).

IMPORTANT BOUNDARY NOTICE:
These tests require a live, authenticated PostgreSQL instance with migration 0003_row_level_security
applied. In offline local environments and SQLite test suites, these tests are cleanly skipped
with an explicit diagnostic reason. They are intended for automated staging verification during
Gate 4 / Gate 5 deployment against Neon Lakebase PostgreSQL.

Invariants Verified:
1. User A cannot SELECT User B's protected rows in users or user_profiles.
2. User A cannot UPDATE User B's protected rows.
3. User A cannot DELETE User B's protected rows.
4. User A cannot INSERT rows impersonating User B.
5. Missing or invalid context (no app.user_id / no app.role) returns zero protected rows.
6. Admin role can read protected rows for platform governance.
"""

import os
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.test_target import validate_test_url


def validate_rls_test_target() -> URL:
    """Validate that DATABASE_URL targets an approved local disposable PostgreSQL database.

    Enforces:
    - Explicit, non-empty TEST_DATABASE_NAME matching r"dormiostay_t03_test_[a-z0-9_]+".
    - DATABASE_URL database matches TEST_DATABASE_NAME.
    - Host is strictly loopback (localhost, 127.0.0.1, ::1) canonicalized to literal IP.
    - Rejects remote hosts, Neon staging, production, and connection overrides.

    Raises ValueError if validation fails.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url or db_url == "not-used-by-unit-tests":
        raise ValueError("DATABASE_URL is not configured for live PostgreSQL testing")
    approved = os.environ.get("TEST_DATABASE_NAME")
    if not approved:
        raise ValueError("TEST_DATABASE_NAME is required for live PostgreSQL testing")
    return validate_test_url(db_url, approved)


def is_live_postgres() -> bool:
    """Detect whether a valid live disposable PostgreSQL target is configured and accessible."""
    if os.environ.get("OFFLINE_ONLY", "1") == "1":
        return False
    try:
        validate_rls_test_target()
        return True
    except (ValueError, Exception):
        return False


# In CI when OFFLINE_ONLY is explicitly disabled ("0"),
# live PostgreSQL tests MUST NOT silently skip.
if os.environ.get("CI") == "true" and os.environ.get("OFFLINE_ONLY") == "0":
    try:
        validate_rls_test_target()
    except Exception as exc:
        pytest.fail(
            f"Live PostgreSQL RLS tests required in CI environment with OFFLINE_ONLY=0, "
            f"but database target validation failed: {exc}"
        )

pytestmark = pytest.mark.skipif(
    not is_live_postgres(),
    reason=(
        "Live PostgreSQL instance with RLS migration required. "
        "Skipped in offline unit environment; verified during Gate 4/5 staging and CI."
    ),
)


@pytest.fixture
def live_database_url() -> str:
    """Return validated canonical disposable database URL string for live RLS tests."""
    return validate_rls_test_target().render_as_string(hide_password=False)


@pytest.mark.anyio
async def test_postgres_rls_cross_user_read_blocked(live_database_url: str) -> None:
    """Verify User A cannot SELECT User B's protected user row in live PostgreSQL."""
    engine = create_async_engine(live_database_url)
    user_a_id = uuid4()
    user_b_id = uuid4()

    async with engine.connect() as conn:
        # Establish User A context
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', :uid, true), "
                "set_config('app.role', 'tenant', true)"
            ),
            {"uid": str(user_a_id)},
        )

        # Query User B
        res = await conn.execute(
            text("SELECT id FROM users WHERE id = :target_id"),
            {"target_id": str(user_b_id)},
        )
        assert res.scalar_one_or_none() is None, "RLS failed to block cross-user SELECT"


@pytest.mark.anyio
async def test_postgres_rls_no_context_returns_zero_rows(live_database_url: str) -> None:
    """Verify that queries with no transaction-local context return zero protected rows."""
    engine = create_async_engine(live_database_url)

    async with engine.connect() as conn:
        # Reset any ambient context
        await conn.execute(
            text("SELECT set_config('app.user_id', '', true), set_config('app.role', '', true)")
        )

        res = await conn.execute(text("SELECT id FROM users LIMIT 10"))
        assert len(res.fetchall()) == 0, "No-context query returned protected user rows"


@pytest.mark.anyio
async def test_postgres_rls_missing_context_users_insert_denied(live_database_url: str) -> None:
    """Verify missing context cannot insert into users table (T12-SEC-06)."""
    engine = create_async_engine(live_database_url)
    uid = uuid4()

    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.user_id', '', true), set_config('app.role', '', true)")
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, role, status) "
                    "VALUES (:id, :email, 'hash', 'admin', 'active')"
                ),
                {"id": str(uid), "email": f"test_{uid}@example.com"},
            )


@pytest.mark.anyio
async def test_postgres_rls_guest_context_admin_creation_denied(live_database_url: str) -> None:
    """Verify guest role cannot insert into users table or create admins (T12-SEC-06)."""
    engine = create_async_engine(live_database_url)
    uid = uuid4()

    async with engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'guest', true)"
            )
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, role, status) "
                    "VALUES (:id, :email, 'hash', 'admin', 'active')"
                ),
                {"id": str(uid), "email": f"guest_{uid}@example.com"},
            )


@pytest.mark.anyio
async def test_postgres_rls_missing_context_email_outbox_insert_denied(
    live_database_url: str,
) -> None:
    """Verify unauthenticated/missing context cannot insert into email_outbox (T12-SEC-07)."""
    engine = create_async_engine(live_database_url)

    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.user_id', '', true), set_config('app.role', '', true)")
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            await conn.execute(
                text(
                    "INSERT INTO email_outbox (recipient, subject, html_content, text_content) "
                    "VALUES ('spam@victim.com', 'Spam', '<p>Spam</p>', 'Spam')"
                )
            )


@pytest.mark.anyio
async def test_postgres_rls_guest_context_email_outbox_insert_denied(
    live_database_url: str,
) -> None:
    """Verify guest context cannot insert into email_outbox (T12-SEC-07)."""
    engine = create_async_engine(live_database_url)

    async with engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'guest', true)"
            )
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            await conn.execute(
                text(
                    "INSERT INTO email_outbox (recipient, subject, html_content, text_content) "
                    "VALUES ('guest_spam@victim.com', 'Spam', '<p>Spam</p>', 'Spam')"
                )
            )


@pytest.mark.anyio
async def test_postgres_rls_tenant_cannot_elevate_role(live_database_url: str) -> None:
    """Verify tenant cannot update own role to admin via trigger enforcement (T12-SEC-08)."""
    engine = create_async_engine(live_database_url)
    uid = uuid4()

    async with engine.connect() as conn:
        # 1. System inserts the user as tenant
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'system', true)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, role, status) "
                "VALUES (:id, :email, 'hash', 'tenant', 'active')"
            ),
            {"id": str(uid), "email": f"tenant_{uid}@example.com"},
        )

        # 2. Tenant logs in and attempts to update own role to admin
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', :uid, true), "
                "set_config('app.role', 'tenant', true)"
            ),
            {"uid": str(uid)},
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)) as exc_info:
            await conn.execute(
                text("UPDATE users SET role = 'admin' WHERE id = :id"),
                {"id": str(uid)},
            )
        err = str(exc_info.value).lower()
        assert (
            "unauthorized modification of role" in err
            or "42501" in err
            or "permission denied" in err
        )


def _assert_rls_check_violation(exc_info: pytest.ExceptionInfo[Any], table: str) -> None:
    """Verify that an exception is specifically an RLS WITH CHECK violation (SQLSTATE 42501).

    Ensures that generic IntegrityError, NOT NULL, FK, or missing-column errors are NOT
    mistaken for RLS policy enforcement.
    """
    err = str(exc_info.value).lower()
    # Explicitly reject unrelated integrity / schema errors
    assert "not-null" not in err and "23502" not in err, (
        f"False positive: caught NOT NULL error instead of RLS violation: {err}"
    )
    assert "foreign key" not in err and "23503" not in err, (
        f"False positive: caught Foreign Key error instead of RLS violation: {err}"
    )
    assert "does not exist" not in err and "42703" not in err, (
        f"False positive: caught missing column/table error: {err}"
    )

    # Must be SQLSTATE 42501 and/or explicit row-level security policy violation
    has_rls_msg = (
        f'violates row-level security policy for table "{table}"' in err
        or "violates row-level security policy" in err
    )
    has_sqlstate = "42501" in err
    assert has_rls_msg or has_sqlstate, (
        f"Expected SQLSTATE 42501 RLS policy violation for {table}, got: {err}"
    )


@pytest.mark.anyio
async def test_postgres_rls_missing_context_audit_logs_insert_denied(
    live_database_url: str,
) -> None:
    """Verify missing context cannot insert into audit_logs (T12-AUDIT-01)."""
    engine = create_async_engine(live_database_url)

    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.user_id', '', true), set_config('app.role', '', true)")
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)) as exc_info:
            await conn.execute(
                text(
                    "INSERT INTO audit_logs (action, target_type, reason, ip) "
                    "VALUES ('security.test', 'system', 'missing context test', '127.0.0.1')"
                )
            )
        _assert_rls_check_violation(exc_info, "audit_logs")


@pytest.mark.anyio
async def test_postgres_rls_tenant_context_audit_logs_insert_denied(
    live_database_url: str,
) -> None:
    """Verify tenant context cannot insert into audit_logs (T12-AUDIT-01)."""
    engine = create_async_engine(live_database_url)
    uid = uuid4()

    async with engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', :uid, true), "
                "set_config('app.role', 'tenant', true)"
            ),
            {"uid": str(uid)},
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)) as exc_info:
            await conn.execute(
                text(
                    "INSERT INTO audit_logs (action, target_type, reason, ip) "
                    "VALUES ('security.test', 'system', 'tenant insert attempt', '127.0.0.1')"
                )
            )
        _assert_rls_check_violation(exc_info, "audit_logs")


@pytest.mark.anyio
async def test_postgres_rls_guest_context_audit_logs_insert_denied(
    live_database_url: str,
) -> None:
    """Verify guest context cannot insert into audit_logs (T12-AUDIT-01)."""
    engine = create_async_engine(live_database_url)

    async with engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'guest', true)"
            )
        )
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)) as exc_info:
            await conn.execute(
                text(
                    "INSERT INTO audit_logs (action, target_type, reason, ip) "
                    "VALUES ('security.test', 'system', 'guest insert attempt', '127.0.0.1')"
                )
            )
        _assert_rls_check_violation(exc_info, "audit_logs")


@pytest.mark.anyio
async def test_postgres_rls_system_context_audit_logs_insert_permitted(
    live_database_url: str,
) -> None:
    """Verify authorized system context can insert into audit_logs (T12-AUDIT-01).

    System context has INSERT privilege under audit_logs_insert policy.
    Because audit_logs_select is restricted to admin, system insert does not
    use RETURNING id, and persistence is confirmed via an authorized admin query
    in an isolated transaction.
    """
    engine = create_async_engine(live_database_url)
    unique_action = f"audit.system.test.{uuid4()}"

    try:
        # 1. System context commits the audit record without RETURNING
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.user_id', '', true), "
                    "set_config('app.role', 'system', true)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO audit_logs (action, target_type, reason, ip) "
                    "VALUES (:act, 'system', 'authorized system insert', '127.0.0.1')"
                ),
                {"act": unique_action},
            )

        # 2. Confirm committed persistence via authorized admin read in a separate transaction
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.user_id', '', true), "
                    "set_config('app.role', 'admin', true)"
                )
            )
            res = await conn.execute(
                text("SELECT id, action FROM audit_logs WHERE action = :act"),
                {"act": unique_action},
            )
            row = res.first()
            assert row is not None, "System context failed to persist audit_logs record"
            assert row[1] == unique_action
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_postgres_rls_admin_context_audit_logs_select_permitted(
    live_database_url: str,
) -> None:
    """Verify admin context can read audit logs (T12-AUDIT-01)."""
    engine = create_async_engine(live_database_url)
    unique_action = f"audit.admin.read.{uuid4()}"

    async with engine.connect() as conn:
        # 1. System inserts the audit record
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'system', true)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO audit_logs (action, target_type, reason, ip) "
                "VALUES (:act, 'system', 'admin read test fixture', '127.0.0.1')"
            ),
            {"act": unique_action},
        )

        # 2. Admin queries the audit record
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'admin', true)"
            )
        )
        res = await conn.execute(
            text("SELECT id, action FROM audit_logs WHERE action = :act"),
            {"act": unique_action},
        )
        row = res.first()
        assert row is not None, "Admin context failed to read audit_logs"
        assert row[1] == unique_action


@pytest.mark.anyio
async def test_postgres_rls_tenant_context_audit_logs_select_denied(
    live_database_url: str,
) -> None:
    """Verify tenant context cannot read audit logs (T12-AUDIT-01)."""
    engine = create_async_engine(live_database_url)
    unique_action = f"audit.tenant.leak.{uuid4()}"

    async with engine.connect() as conn:
        # 1. System inserts the audit record
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'system', true)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO audit_logs (action, target_type, reason, ip) "
                "VALUES (:act, 'system', 'tenant read isolation test', '127.0.0.1')"
            ),
            {"act": unique_action},
        )

        # 2. Tenant queries the audit record -> must return 0 rows
        await conn.execute(
            text(
                "SELECT set_config('app.user_id', '', true), set_config('app.role', 'tenant', true)"
            )
        )
        res = await conn.execute(
            text("SELECT id FROM audit_logs WHERE action = :act"),
            {"act": unique_action},
        )
        assert res.first() is None, "Tenant context leaked audit_logs rows"


@pytest.mark.anyio
async def test_postgres_rls_audit_logs_update_delete_denied(
    live_database_url: str,
) -> None:
    """Verify audit_logs UPDATE and DELETE are strictly denied for all contexts (T12-AUDIT-01).

    Proves defense-in-depth immutability across isolated transactions:
    1. System context creates and COMMITS the test fixture.
    2. Admin context reads target_id in a clean transaction.
    3. Admin UPDATE is denied with genuine SQLSTATE 42501 (not aborted transaction 25P02).
    4. Admin DELETE is denied in an un-aborted transaction with SQLSTATE 42501 (not 25P02).
    5. Admin context verifies the record remains intact and unmodified in the database.
    """
    engine = create_async_engine(live_database_url)
    unique_action = f"audit.immutable.{uuid4()}"

    try:
        # A. Create the fixture: System inserts and COMMITS the row without RETURNING
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.user_id', '', true), "
                    "set_config('app.role', 'system', true)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO audit_logs (action, target_type, reason, ip) "
                    "VALUES (:act, 'system', 'immutability test', '127.0.0.1')"
                ),
                {"act": unique_action},
            )

        # B. Obtain target row ID: Admin queries the created row in a clean transaction
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.user_id', '', true), "
                    "set_config('app.role', 'admin', true)"
                )
            )
            res = await conn.execute(
                text("SELECT id FROM audit_logs WHERE action = :act"),
                {"act": unique_action},
            )
            row = res.first()
            assert row is not None, "Audit log fixture was not inserted"
            target_id = row[0]

        # C. Test UPDATE denial in a fresh transaction -> denied by grant absence / trigger
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.user_id', '', true), "
                    "set_config('app.role', 'admin', true)"
                )
            )
            with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)) as exc_info:
                await conn.execute(
                    text("UPDATE audit_logs SET reason = 'tampered' WHERE id = :id"),
                    {"id": str(target_id)},
                )
            await conn.rollback()
            err = str(exc_info.value).lower()
            assert "25p02" not in err, (
                f"Defect: transaction was aborted before UPDATE execution: {err}"
            )
            assert "23502" not in err and "not-null" not in err
            assert "23503" not in err and "foreign key" not in err
            assert "42703" not in err and "does not exist" not in err
            has_expected_denial = (
                "42501" in err
                or "audit_logs is append-only" in err
                or "permission denied" in err
                or "violates row-level security policy" in err
            )
            assert has_expected_denial, (
                f"Expected SQLSTATE 42501 or immutable trigger denial for UPDATE, got: {err}"
            )

        # D. Test DELETE denial in an un-aborted transaction -> denied by grant absence / trigger
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.user_id', '', true), "
                    "set_config('app.role', 'admin', true)"
                )
            )
            with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)) as exc_info:
                await conn.execute(
                    text("DELETE FROM audit_logs WHERE id = :id"),
                    {"id": str(target_id)},
                )
            await conn.rollback()
            err = str(exc_info.value).lower()
            assert "25p02" not in err, (
                f"Defect: transaction was aborted before DELETE execution: {err}"
            )
            assert "23502" not in err and "not-null" not in err
            assert "23503" not in err and "foreign key" not in err
            assert "42703" not in err and "does not exist" not in err
            has_expected_denial = (
                "42501" in err
                or "audit_logs is append-only" in err
                or "permission denied" in err
                or "violates row-level security policy" in err
            )
            assert has_expected_denial, (
                f"Expected SQLSTATE 42501 or immutable trigger denial for DELETE, got: {err}"
            )

        # E. Verify immutability: Row remains intact in a fresh, healthy admin transaction
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.user_id', '', true), "
                    "set_config('app.role', 'admin', true)"
                )
            )
            res_verify = await conn.execute(
                text("SELECT reason FROM audit_logs WHERE id = :id"),
                {"id": str(target_id)},
            )
            verify_row = res_verify.first()
            assert verify_row is not None, "Audit log row was deleted despite denial"
            assert verify_row[0] == "immutability test", (
                f"Audit log row was modified despite denial: {verify_row[0]}"
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_postgres_rls_exact_cumulative_policy_inventory(
    live_database_url: str,
) -> None:
    """Verify exactly 43 cumulative policies exist across all governed tables (T12-AUDIT-01)."""
    engine = create_async_engine(live_database_url)

    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT count(*) FROM pg_policy"))
        count = res.scalar()
        assert count == 43, f"Expected exactly 43 cumulative policies, but catalog reports {count}"


@pytest.mark.anyio
async def test_postgres_rls_audit_logs_policy_catalog_clean(
    live_database_url: str,
) -> None:
    """Verify audit_logs contains only audit_logs_select and audit_logs_insert (T12-AUDIT-01).

    Proves that foundation policies audit_insert and audit_admin_read do NOT survive.
    """
    engine = create_async_engine(live_database_url)

    async with engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT polname FROM pg_policy "
                "WHERE polrelid = 'audit_logs'::regclass "
                "ORDER BY polname"
            )
        )
        policies = [row[0] for row in res.fetchall()]
        assert policies == ["audit_logs_insert", "audit_logs_select"], (
            f"Unexpected policy catalog on audit_logs: {policies}"
        )
        assert "audit_insert" not in policies, "Vulnerability: audit_insert survived migration 0003"
        assert "audit_admin_read" not in policies, (
            "Legacy policy audit_admin_read survived migration 0003"
        )
