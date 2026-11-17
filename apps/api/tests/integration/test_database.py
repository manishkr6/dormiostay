"""Opt-in tests: only a uniquely named disposable database may be migrated."""

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Base
from app.db.session import async_url, transaction, verify_runtime_role
from app.db.test_target import validate_test_url, verify_test_connection


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required: disposable PostgreSQL database only")
    approved = os.environ.get("TEST_DATABASE_NAME")
    validate_test_url(url, approved)

    async def check_empty() -> None:
        engine = create_async_engine(async_url(url), poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                assert approved is not None
                await verify_test_connection(connection, approved)
                names = (
                    (
                        await connection.execute(
                            text(
                                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                                "AND tablename <> 'alembic_version'"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if names:
                    pytest.fail("Refusing migration tests: disposable database is not empty")
        finally:
            await engine.dispose()

    asyncio.run(check_empty())
    env = {**os.environ, "DATABASE_URL_DIRECT": url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    subprocess.run([sys.executable, "-m", "alembic", "check"], env=env, check=True)
    yield url
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], env=env, check=True)

    async def verify_empty() -> None:
        engine = create_async_engine(async_url(url), poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                names = (
                    (
                        await connection.execute(
                            text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert set(names) == {"alembic_version"}
        finally:
            await engine.dispose()

    asyncio.run(verify_empty())


def test_schema_and_email_uniqueness(database: str) -> None:
    async def run() -> None:
        engine = create_async_engine(async_url(database), poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                names = (
                    (
                        await connection.execute(
                            text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert set(Base.metadata.tables) <= set(names)
                await connection.execute(
                    text(
                        "INSERT INTO users(email,password_hash) "
                        "VALUES ('Case@example.invalid','hash')"
                    )
                )
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO users(email,password_hash) "
                            "VALUES ('case@example.invalid','hash')"
                        )
                    )
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(
                        text("INSERT INTO locations(name,type,latitude) VALUES ('bad','city',91)")
                    )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_actual_application_login(database: str) -> None:
    async def run() -> None:
        app_url = os.environ["TEST_APP_DATABASE_URL"]
        validate_test_url(app_url, os.environ.get("TEST_DATABASE_NAME"))
        admin = create_async_engine(async_url(database), poolclass=NullPool)
        app_engine = create_async_engine(async_url(app_url), poolclass=NullPool)
        try:
            async with admin.begin() as connection:
                await connection.execute(text("GRANT USAGE ON SCHEMA public TO dormiostay_app"))
                await connection.execute(
                    text("GRANT SELECT, INSERT ON audit_logs TO dormiostay_app")
                )
            async with async_sessionmaker(admin)() as session:
                with pytest.raises(RuntimeError, match="DATABASE_ROLE_UNSAFE"):
                    await verify_runtime_role(session)
            factory = async_sessionmaker(app_engine)
            async with transaction(factory) as session:
                await verify_runtime_role(session)
                assert await session.scalar(text("SELECT current_user")) == "dormiostay_app"
                await session.execute(
                    text("INSERT INTO audit_logs(action,target_type) VALUES ('local_login','test')")
                )
                assert await session.scalar(text("SELECT count(*) FROM audit_logs")) == 0
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    assert (
                        await session.scalar(
                            text(
                                "SELECT has_table_privilege(current_user,'audit_logs',:privilege)"
                            ),
                            {"privilege": privilege},
                        )
                        is False
                    )
            for sql in (
                "UPDATE audit_logs SET reason='x'",
                "DELETE FROM audit_logs",
                "TRUNCATE audit_logs",
            ):
                with pytest.raises(DBAPIError):
                    async with transaction(factory) as session:
                        await session.execute(text(sql))
            async with transaction(factory, uuid4(), "admin") as session:
                assert await session.scalar(text("SELECT count(*) FROM audit_logs")) == 1
        finally:
            await app_engine.dispose()
            await admin.dispose()

    asyncio.run(run())


def test_context_commit_and_rollback_do_not_leak(database: str) -> None:
    async def run() -> None:
        engine = create_async_engine(async_url(database), pool_size=1, max_overflow=0)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        query = text("SELECT current_setting('app.user_id',true), current_setting('app.role',true)")
        try:
            for rollback in (False, True):
                identity = uuid4()
                try:
                    async with transaction(factory, identity, "tenant") as session:
                        assert tuple((await session.execute(query)).one()) == (
                            str(identity),
                            "tenant",
                        )
                        if rollback:
                            raise RuntimeError("rollback test")
                except RuntimeError:
                    pass
                async with engine.connect() as connection:
                    assert all(
                        value in (None, "") for value in (await connection.execute(query)).one()
                    )
                async with transaction(factory) as session:
                    assert tuple((await session.execute(query)).one()) == ("", "guest")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_ordinary_application_role_audit_permissions(database: str) -> None:
    async def run() -> None:
        engine = create_async_engine(async_url(database), poolclass=NullPool)
        # Unique cluster role is removed after the test; no existing role is altered.
        role = "t03_test_" + uuid4().hex
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                )
                await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
                await connection.execute(text(f'GRANT SELECT, INSERT ON audit_logs TO "{role}"'))
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                flags = (
                    await connection.execute(
                        text(
                            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                        )
                    )
                ).one()
                assert tuple(flags) == (False, False)
                await connection.execute(
                    text("INSERT INTO audit_logs(action,target_type) VALUES ('test','test')")
                )
                assert (
                    await connection.execute(text("SELECT count(*) FROM audit_logs"))
                ).scalar() == 0
                await connection.execute(text("SELECT set_config('app.role','admin',true)"))
                assert (
                    await connection.execute(text("SELECT count(*) FROM audit_logs"))
                ).scalar_one() >= 1
            for sql in (
                "UPDATE audit_logs SET reason='tampered'",
                "DELETE FROM audit_logs",
                "TRUNCATE audit_logs",
            ):
                with pytest.raises(DBAPIError):
                    async with engine.begin() as connection:
                        await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                        await connection.execute(text(sql))
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE "{role}"'))
            await engine.dispose()

    asyncio.run(run())
