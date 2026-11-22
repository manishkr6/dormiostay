"""Migrations exclusively use the direct migration-role connection."""

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.db.migration_guard import (
    detect_migration_action,
    resolve_migration_target,
    verify_migration_connection,
)
from app.db.models import Base

load_dotenv()
direct_url = os.environ.get("DATABASE_URL_DIRECT")
if not direct_url:
    raise SystemExit("MIGRATION_TARGET_REJECTED")

test_approved = os.environ.get("TEST_DATABASE_NAME")
migration_env = os.environ.get("MIGRATION_ENVIRONMENT")
migration_confirm = os.environ.get("MIGRATION_CONFIRMATION")
migration_action = detect_migration_action(
    getattr(context, "config", None),
    getattr(context, "_proxy", None),
)

try:
    target, approved_identity = resolve_migration_target(
        direct_url,
        test_db_name=test_approved,
        migration_env=migration_env,
        migration_confirmation=migration_confirm,
        action=migration_action,
    )
except Exception:
    raise SystemExit("MIGRATION_TARGET_REJECTED") from None

if not test_approved and migration_action == "downgrade":
    raise SystemExit("MIGRATION_TARGET_REJECTED")


def migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    assert direct_url is not None
    engine = create_async_engine(target, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await verify_migration_connection(connection, approved_identity, test_approved)
            await connection.rollback()
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=target,
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    try:
        asyncio.run(online())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(
            "MIGRATION_FAILED: inspect schema and privileges; details suppressed"
        ) from None
