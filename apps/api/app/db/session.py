"""Async sessions with transaction-local, trusted server-side identity."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, cast
from uuid import UUID

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

Role = Literal["guest", "tenant", "owner", "admin", "system", "worker"]


def async_url(value: str) -> URL:
    url = make_url(value)
    if url.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("A PostgreSQL connection URL is required")
    query = dict(url.query)
    if set(query) - {"sslmode", "ssl"} or any(not isinstance(v, str) for v in query.values()):
        raise ValueError("Unsupported database connection options")
    if "sslmode" in query and "ssl" in query:
        raise ValueError("Conflicting TLS options")
    if not url.host or not url.database or not url.username:
        raise ValueError("Database host, name and user are required")
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    return url.set(drivername="postgresql+asyncpg", query=query)


async def verify_runtime_role(session: AsyncSession) -> None:
    """Fail closed on elevated roles, role memberships, ownership or schema DDL access."""
    unsafe = await session.scalar(
        text("""
        SELECT r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole
            OR r.rolreplication
            OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid)
            OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database()
                       AND datdba=r.oid)
            OR EXISTS (SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace
                       AND relowner=r.oid)
            OR has_schema_privilege(current_user, 'public', 'CREATE')
        FROM pg_roles r WHERE rolname=current_user
    """)
    )
    if unsafe is not False:
        raise RuntimeError("DATABASE_ROLE_UNSAFE")


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        async_url(database_url),
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )


async def check_runtime_database(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            await verify_runtime_role(session)
            await session.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def set_session_identity(
    session: AsyncSession,
    role: Role,
    user_id: UUID | None = None,
) -> None:
    """Set transaction-local identity GUCs on an active session using parameterized set_config.

    Strict security invariants:
    - Rejects invalid roles.
    - Requires user_id for tenant, owner, and admin roles.
    - set_config(..., true) scopes the setting strictly to the current local transaction.
    - Gracefully no-ops on non-PostgreSQL dialects (e.g. SQLite unit tests).
    """
    if role not in {"guest", "tenant", "owner", "admin", "system", "worker"}:
        raise ValueError(f"Invalid role: {role}")
    if role not in {"guest", "system", "worker"} and user_id is None:
        raise ValueError("Authenticated roles require a user ID")

    # Check for non-PostgreSQL dialect via session, session.bind, or sync_session adapter
    bind = getattr(session, "bind", None)
    if bind is None and hasattr(session, "get_bind"):
        try:
            bind = session.get_bind()
        except Exception:
            bind = None
    if bind is None and hasattr(session, "sync_session"):
        sync_sess = session.sync_session
        bind = getattr(sync_sess, "bind", None)
        if bind is None and hasattr(sync_sess, "get_bind"):
            try:
                bind = sync_sess.get_bind()
            except Exception:
                bind = None

    if bind is not None and hasattr(bind, "dialect"):
        dialect_name = getattr(bind.dialect, "name", None)
        if isinstance(dialect_name, str) and dialect_name != "postgresql":
            return

    try:
        await session.execute(
            text(
                "SELECT set_config('app.user_id', :user_id, true), "
                "set_config('app.role', :role, true)"
            ),
            {"user_id": str(user_id) if user_id else "", "role": role},
        )
    except Exception as exc:
        err_msg = str(exc).lower()
        if "set_config" in err_msg or "no such function" in err_msg:
            return
        raise


@asynccontextmanager
async def transaction(
    factory: async_sessionmaker[AsyncSession],
    user_id: UUID | None = None,
    role: Role = "guest",
) -> AsyncIterator[AsyncSession]:
    """Identity must come from backend authentication, never request input.

    set_config(..., true) is PostgreSQL's parameterized SET LOCAL equivalent.
    Commit and rollback both clear the identity before the connection is reused.
    Callers must not commit early or start another transaction within this scope.
    """
    if role not in {"guest", "tenant", "owner", "admin", "system", "worker"} or (
        role not in {"guest", "system", "worker"} and user_id is None
    ):
        raise ValueError("Authenticated roles require a user ID")
    async with factory() as session, session.begin():
        await set_session_identity(session, role=role, user_id=user_id)
        yield session


@asynccontextmanager
async def system_transaction(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Trusted internal system transaction for authentication and worker operations.

    Establishes transaction-local app.role = 'system' and app.user_id = ''.
    Reverts automatically on commit or rollback before connection reuse.
    Never directly exposed to client request handlers.
    """
    async with factory() as session, session.begin():
        await set_session_identity(session, role="system")
        yield session


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Return the configured async sessionmaker factory from application state."""
    if not hasattr(request.app.state, "database_engine"):
        request.app.state.database_engine = create_engine(request.app.state.settings.database_url)
    return async_sessionmaker(request.app.state.database_engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Guest by default; future authentication supplies trusted request.state identity.

    Strict security invariant:
    Request sessions can NEVER assume 'system' or 'worker' privileged identities.
    If request.state.role is missing or privileged, it defaults safely to 'guest'.
    """
    factory = get_session_factory(request)
    raw_role = getattr(request.state, "role", "guest")
    role: Role = (
        cast(Role, raw_role) if raw_role in {"guest", "tenant", "owner", "admin"} else "guest"
    )
    user_id: UUID | None = getattr(request.state, "user_id", None) if role != "guest" else None

    async with transaction(factory, user_id=user_id, role=role) as session:
        await verify_runtime_role(session)
        yield session
