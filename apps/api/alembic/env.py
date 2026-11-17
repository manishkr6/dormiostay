"""Migrations exclusively use the direct migration-role connection."""

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.db.models import Base
from app.db.session import async_url
from app.db.test_target import validate_test_url, verify_test_connection

load_dotenv()
direct_url = os.environ.get("DATABASE_URL_DIRECT")
if not direct_url:
    raise RuntimeError("DATABASE_URL_DIRECT is required for migrations")

# T-03 permits DDL only on an explicitly approved local disposable target.
# Development/production migration authorization is intentionally a separate future decision.
approved = os.environ.get("TEST_DATABASE_NAME")
try:
    target = validate_test_url(direct_url, approved)
except Exception:
    raise SystemExit("MIGRATION_TARGET_REJECTED") from None


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    assert direct_url is not None
    engine = create_async_engine(target, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            assert approved is not None
            await verify_test_connection(connection, approved)
            await connection.rollback()
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=async_url(direct_url), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    try:
        asyncio.run(online())
    except Exception:
        raise SystemExit(
            "MIGRATION_FAILED: inspect schema and privileges; details suppressed"
        ) from None
