"""Destructive only inside the named loopback CI fixture initialized at revision 0001."""

import asyncio
import hashlib
import importlib.util
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncTransaction,
    create_async_engine,
)

from alembic import op
from app.db import gate3b
from app.db.recovery import foundation_fingerprint
from app.db.security_catalog import DESTINATION, FOUNDATION, verify_catalog
from app.db.test_target import validate_test_url

DATABASE = "dormiostay_t03_test_gate3b"
pytestmark = pytest.mark.anyio


def _load_migration(filename: str, module_name: str) -> Any:
    mig_path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _reset_to_foundation(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        revs = (await conn.scalars(text("SELECT version_num FROM public.alembic_version"))).all()
        if revs == [DESTINATION]:
            mod_0003 = _load_migration("0003_row_level_security.py", "migration_0003_reset")
            mod_0002 = _load_migration("0002_email_outbox.py", "migration_0002_reset")
            async with engine.begin() as tx:
                for stmt in mod_0003.DOWNGRADE_STATEMENTS:
                    await tx.execute(text(stmt))
                for stmt in mod_0002.DOWNGRADE_STATEMENTS:
                    await tx.execute(text(stmt))
                await tx.execute(
                    text(f"UPDATE public.alembic_version SET version_num = '{FOUNDATION}'")
                )
        async with engine.begin() as cleanup_tx:
            await cleanup_tx.execute(
                text("DELETE FROM public.amenities WHERE name = 'release preservation'")
            )


async def test_contention_and_rollback(engine: AsyncEngine, target: str) -> None:
    async with engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": gate3b.LOCK_KEY})
        await holder.rollback()
        with pytest.raises(RuntimeError, match="lock unavailable"):
            await gate3b.run_disposable(target, DATABASE)
        with pytest.raises(RuntimeError, match="session or lock"):
            await gate3b._check_session(holder, -1, DATABASE)
    async with engine.connect() as conn:
        assert await conn.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": gate3b.LOCK_KEY}
        )


@pytest.mark.parametrize("failure", ["0002", "0003", "catalog", "later_revision"])
async def test_atomic_failure(
    engine: AsyncEngine, target: str, failure: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if failure == "later_revision":
        versions = tmp_path / "versions"
        shutil.copytree(gate3b.VERSIONS, versions)
        (versions / "later.py").write_text(
            "revision='0004_future'\ndown_revision='0003_row_level_security'\n"
        )
        monkeypatch.setattr(gate3b, "VERSIONS", versions)
    elif failure == "catalog":

        async def reject(conn: AsyncConnection, revision: str) -> None:
            await verify_catalog(conn, revision)
            if revision == DESTINATION:
                raise RuntimeError("Injected catalog failure")

        monkeypatch.setattr(gate3b, "verify_catalog", reject)
    else:
        original = op.execute

        def intercept(stmt: str) -> None:
            original(stmt)
            if ("CREATE INDEX ix_email_outbox" if failure == "0002" else "CREATE POLICY") in stmt:
                raise RuntimeError("Injected revision failure")

        monkeypatch.setattr(op, "execute", intercept)
    with pytest.raises(RuntimeError, match="Injected|graph"):
        await gate3b.run_disposable(target, DATABASE)
    async with engine.connect() as observer:
        await verify_catalog(observer, FOUNDATION)
        assert await observer.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": gate3b.LOCK_KEY}
        )


@pytest.mark.parametrize("failure", ["cancel", "disconnect"])
async def test_precommit_interruption(
    engine: AsyncEngine, target: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = []

    async def interrupt(conn: AsyncConnection, revision: str) -> None:
        await verify_catalog(conn, revision)
        observed.append(await conn.scalar(text("SELECT pg_backend_pid()")))
        if revision == DESTINATION:
            if failure == "cancel":
                raise asyncio.CancelledError
            await conn.invalidate()
            raise ConnectionError("Physical connection lost")

    monkeypatch.setattr(gate3b, "verify_catalog", interrupt)
    with pytest.raises((asyncio.CancelledError, ConnectionError)):
        await gate3b.run_disposable(target, DATABASE)
    assert len(observed) == 3 and len(set(observed)) == 1
    async with engine.connect() as observer:
        await verify_catalog(observer, FOUNDATION)
        assert await observer.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": gate3b.LOCK_KEY}
        )


async def test_preddl_recheck_after_preflight(
    engine: AsyncEngine, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight_done = False

    async def change_after_read(conn: AsyncConnection, revision: str) -> None:
        nonlocal preflight_done
        await verify_catalog(conn, revision)
        if not preflight_done:
            preflight_done = True
            async with engine.begin() as concurrent:
                await concurrent.execute(text("ALTER ROLE dormiostay_app CREATEDB"))

    monkeypatch.setattr(gate3b, "verify_catalog", change_after_read)
    try:
        with pytest.raises(RuntimeError, match="catalog mismatch"):
            await gate3b.run_disposable(target, DATABASE)
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(text("ALTER ROLE dormiostay_app NOCREATEDB"))
    async with engine.connect() as observer:
        await verify_catalog(observer, FOUNDATION)


async def test_isolated_rehearsal_comparison_and_acknowledged_upgrade(
    engine: AsyncEngine, target: str
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.db.recovery import RecoveryEvidence, verify_isolated_rehearsal
    from app.db.release_contract import ReleaseBinding, ReleaseDecision, local_input_digests

    other_name = "dormiostay_t03_test_rehearsal"
    other_url = validate_test_url(target, DATABASE).set(database=other_name)
    other = create_async_engine(other_url, poolclass=pool.NullPool)
    try:
        await _reset_to_foundation(other)
        async with engine.connect() as primary, other.connect() as rehearsal:
            for conn in (primary, rehearsal):
                await conn.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                await conn.execute(text("SET LOCAL row_security=off"))
                await conn.execute(text("SET LOCAL timezone='UTC'"))
            now = datetime.now(UTC)
            evidence = RecoveryEvidence(
                project="small-dew-81510029",
                source_branch="br-weathered-dust-bim5fibs",
                endpoint="ep-cold-smoke-bir1s74t",
                database="neondb",
                revision="0001_foundation",
                artifact="snap-unit-test-only",
                rehearsal_branch="br-unit-test-only",
                captured_at=now,
                rehearsed_at=now,
                approved_at=now,
                expires_at=now + timedelta(hours=1),
                retained_until=now + timedelta(days=1),
                recovery_owner="test-owner",
                independent_approver="test-reviewer",
                foundation_digest=await foundation_fingerprint(primary),
                write_freeze_record="test",
                rehearsal_record="test",
            )
            await verify_isolated_rehearsal(primary, rehearsal, evidence)
        dependencies, manifest_hash = local_input_digests()
        binding = ReleaseBinding.model_validate(
            dict(
                repository="manishkr6/dormiostay",
                environment="staging-gate3b",
                project=evidence.project,
                branch=evidence.source_branch,
                endpoint=evidence.endpoint,
                host="ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech",
                database="neondb",
                migration_role="neondb_owner",
                runtime_role="dormiostay_app",
                source=FOUNDATION,
                destination=DESTINATION,
                commit="0" * 40,
                ref="refs/heads/test-only",
                artifact_digest="a" * 64,
                workflow_digest="0" * 64,
                dependency_digest=dependencies,
                manifest_digest=manifest_hash,
                recovery_digest=hashlib.sha256(evidence.model_dump_json().encode()).hexdigest(),
                run_id=1,
                attempt=1,
                initiator_id=1,
            )
        )
        decision = ReleaseDecision(
            binding=binding, reviewer_id=2, issued=now, expires=now + timedelta(minutes=10)
        )
        ledger = sqlite3.connect(":memory:")
        try:
            envelope = gate3b.DisposableApproval(decision, binding, evidence, ledger)
            await gate3b.run_disposable(
                other_url.render_as_string(hide_password=False), other_name, envelope
            )
            assert ledger.execute("SELECT status FROM outcomes").fetchall() == [("committed",)]
            with pytest.raises(sqlite3.IntegrityError):
                await gate3b.run_disposable(
                    other_url.render_as_string(hide_password=False), other_name, envelope
                )
        finally:
            ledger.close()
        async with other.connect() as observer:
            await verify_catalog(observer, DESTINATION)
            assert await observer.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": gate3b.LOCK_KEY}
            )
    finally:
        try:
            await _reset_to_foundation(other)
        finally:
            await other.dispose()


async def test_populated_upgrade_and_lost_ack(target: str) -> None:
    name = "dormiostay_t03_test_commit"
    url = validate_test_url(target, DATABASE).set(database=name)
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        await _reset_to_foundation(engine)
        await check_lost_ack(engine, url.render_as_string(hide_password=False), name)
    finally:
        try:
            await _reset_to_foundation(engine)
        finally:
            await engine.dispose()


async def check_lost_ack(engine: AsyncEngine, target: str, database: str) -> None:
    async with engine.begin() as before:
        await before.execute(text("INSERT INTO amenities(name) VALUES ('release preservation')"))
    original_commit = AsyncTransaction.commit

    async def lost_ack(tx: AsyncTransaction) -> None:
        await original_commit(tx)
        raise ConnectionError("Injected lost acknowledgement")

    with patch.object(AsyncTransaction, "commit", lost_ack):
        with pytest.raises(gate3b.CommitOutcomeUnknown):
            await gate3b.run_disposable(target, database)
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await verify_catalog(conn, DESTINATION)
        assert await conn.scalar(text("SELECT count(*) FROM pg_policy")) == 43
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM amenities WHERE name='release preservation'")
            )
            == 1
        )
        assert await conn.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": gate3b.LOCK_KEY}
        )
        await conn.rollback()
        for change in (
            "ALTER POLICY audit_logs_insert ON audit_logs WITH CHECK (false)",
            "ALTER TABLE users DISABLE TRIGGER protect_users_sensitive_columns",
            "REVOKE SELECT ON users FROM dormiostay_app",
            "GRANT SELECT ON alembic_version TO dormiostay_app",
            "ALTER TABLE amenities NO FORCE ROW LEVEL SECURITY",
        ):
            await conn.execute(text(change))
            with pytest.raises(RuntimeError, match="catalog mismatch"):
                await verify_catalog(conn, DESTINATION)
            await conn.rollback()
    with pytest.raises(RuntimeError, match="catalog mismatch"):
        await gate3b.run_disposable(target, database)
