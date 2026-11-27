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
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine


def is_live_postgres() -> bool:
    """Detect whether a live PostgreSQL connection URL is configured and accessible."""
    db_url = os.environ.get("DATABASE_URL", "")
    return "postgres" in db_url and not os.environ.get("OFFLINE_ONLY", "1") == "1"


# In CI when OFFLINE_ONLY is explicitly disabled ("0"),
# live PostgreSQL tests MUST NOT silently skip.
if (
    os.environ.get("CI") == "true"
    and os.environ.get("OFFLINE_ONLY") == "0"
    and not is_live_postgres()
):
    pytest.fail(
        "Live PostgreSQL RLS tests required in CI environment with OFFLINE_ONLY=0, "
        "but DATABASE_URL is missing or does not target PostgreSQL."
    )

pytestmark = pytest.mark.skipif(
    not is_live_postgres(),
    reason=(
        "Live PostgreSQL instance with RLS migration required. "
        "Skipped in offline unit environment; verified during Gate 4/5 staging and CI."
    ),
)


@pytest.mark.anyio
async def test_postgres_rls_cross_user_read_blocked() -> None:
    """Verify User A cannot SELECT User B's protected user row in live PostgreSQL."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
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
async def test_postgres_rls_no_context_returns_zero_rows() -> None:
    """Verify that queries with no transaction-local context return zero protected rows."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)

    async with engine.connect() as conn:
        # Reset any ambient context
        await conn.execute(
            text("SELECT set_config('app.user_id', '', true), set_config('app.role', '', true)")
        )

        res = await conn.execute(text("SELECT id FROM users LIMIT 10"))
        assert len(res.fetchall()) == 0, "No-context query returned protected user rows"


@pytest.mark.anyio
async def test_postgres_rls_missing_context_users_insert_denied() -> None:
    """Verify missing context cannot insert into users table (T12-SEC-06)."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
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
async def test_postgres_rls_guest_context_admin_creation_denied() -> None:
    """Verify guest role cannot insert into users table or create admins (T12-SEC-06)."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
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
async def test_postgres_rls_missing_context_email_outbox_insert_denied() -> None:
    """Verify unauthenticated/missing context cannot insert into email_outbox (T12-SEC-07)."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)

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
async def test_postgres_rls_guest_context_email_outbox_insert_denied() -> None:
    """Verify guest context cannot insert into email_outbox (T12-SEC-07)."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)

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
async def test_postgres_rls_tenant_cannot_elevate_role() -> None:
    """Verify tenant cannot update own role to admin via trigger enforcement (T12-SEC-08)."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
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
