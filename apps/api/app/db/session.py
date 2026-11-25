"""Async sessions with transaction-local, trusted server-side identity."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
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

Role = Literal["guest", "tenant", "owner", "admin"]


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
    if role not in {"guest", "tenant", "owner", "admin"} or (role != "guest" and user_id is None):
        raise ValueError("Authenticated roles require a user ID")
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "SELECT set_config('app.user_id', :user_id, true), "
                "set_config('app.role', :role, true)"
            ),
            {"user_id": str(user_id) if user_id else "", "role": role},
        )
        yield session


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Return the configured async sessionmaker factory from application state."""
    if not hasattr(request.app.state, "database_engine"):
        request.app.state.database_engine = create_engine(request.app.state.settings.database_url)
    return async_sessionmaker(request.app.state.database_engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Guest by default; future authentication supplies trusted request.state identity."""
    factory = get_session_factory(request)
    async with transaction(
        factory, getattr(request.state, "user_id", None), getattr(request.state, "role", "guest")
    ) as session:
        await verify_runtime_role(session)
        yield session
