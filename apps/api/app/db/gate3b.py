"""Gate 3b transactional core. Only validated loopback disposable databases can enter."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from alembic import command
from app.db.recovery import RecoveryEvidence, foundation_fingerprint
from app.db.release_contract import (
    ReleaseBinding,
    ReleaseDecision,
    consume_at_custodian,
    local_input_digests,
    record_outcome,
)
from app.db.security_catalog import DESTINATION, FOUNDATION, verify_catalog
from app.db.test_target import validate_test_url

MIDDLE = "0002_email_outbox"
LOCK_KEY = 731203
VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


class CommitOutcomeUnknown(RuntimeError):
    """An operator must reconcile both catalog states before a new attempt."""


def upgrade_shared(connection: Connection, database: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("gate3b_env")))
    config.set_main_option("path_separator", "os")
    config.set_main_option("version_locations", str(VERSIONS))
    revisions = ScriptDirectory.from_config(config)
    path = [(r.revision, r.down_revision, r.dependencies) for r in revisions.walk_revisions()]
    if path != [(DESTINATION, MIDDLE, None), (MIDDLE, FOUNDATION, None), (FOUNDATION, None, None)]:
        raise RuntimeError("Unexpected migration graph")
    config.attributes.update(connection=connection, disposable_database=database)
    command.upgrade(config, DESTINATION)


async def _check_session(conn: AsyncConnection, pid: int, database: str) -> None:
    if conn.invalidated:
        raise RuntimeError("Migration connection invalidated")
    state = (
        await conn.execute(
            text("""
        SELECT pg_backend_pid(),current_database(),current_user,session_user,
        inet_server_addr() IN (inet '127.0.0.1',inet '::1'),
        EXISTS (SELECT FROM pg_catalog.pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=0 AND objid=:key AND objsubid=1
        AND mode='ExclusiveLock' AND granted)
    """),
            {"key": LOCK_KEY},
        )
    ).one()
    if tuple(state) != (pid, database, "neondb_owner", "neondb_owner", True, True):
        raise RuntimeError("Migration session or lock changed")


@dataclass(frozen=True)
class DisposableApproval:
    """Exercise the custodian contract on disposable data. Never usable for staging."""

    decision: ReleaseDecision
    observed: ReleaseBinding
    recovery: RecoveryEvidence
    ledger: sqlite3.Connection

    def check_inputs(self) -> None:
        self.decision.check(self.observed)
        if local_input_digests() != (
            self.observed.dependency_digest,
            self.observed.manifest_digest,
        ):
            raise ValueError("Release input digest mismatch")
        digest = hashlib.sha256(self.recovery.model_dump_json().encode()).hexdigest()
        if digest != self.observed.recovery_digest:
            raise ValueError("Recovery evidence digest mismatch")

    async def verify_recovery(self, conn: AsyncConnection) -> None:
        self.check_inputs()
        self.recovery.check(await foundation_fingerprint(conn))
        self.decision.check(self.observed)


async def run_disposable(
    url: str, database: str, approval: DisposableApproval | None = None
) -> None:
    target = validate_test_url(url, database)
    if target.username != "neondb_owner" or target.query:
        raise ValueError("Expected direct disposable migration identity")
    if approval:
        approval.check_inputs()
        consume_at_custodian(approval.ledger, approval.decision, approval.observed)
    engine = create_async_engine(target, poolclass=pool.NullPool, hide_parameters=True)
    attempted_connection = False

    @event.listens_for(engine.sync_engine, "do_connect")
    def refuse_reconnect(*args: Any) -> None:
        nonlocal attempted_connection
        if attempted_connection:
            raise RuntimeError("Replacement physical connection forbidden")
        attempted_connection = True

    outcome: Literal["committed", "rolled_back", "unknown", "aborted"] = "aborted"
    pid: int | None = None
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await conn.execute(text("SET LOCAL row_security=off"))
            await conn.execute(text("SET LOCAL timezone='UTC'"))
            # Session lock is obtained once. Closing this physical session also releases it.
            pid, acquired = (
                await conn.execute(
                    text("SELECT pg_backend_pid(),pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
                )
            ).one()
            if acquired is not True:
                raise RuntimeError("Migration lock unavailable")
            await _check_session(conn, pid, database)
            await verify_catalog(conn, FOUNDATION)
            if approval:
                await approval.verify_recovery(conn)
            await conn.rollback()
            tx = await conn.begin()
            try:
                await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                await conn.execute(text("SET LOCAL row_security=off"))
                await conn.execute(text("SET LOCAL timezone='UTC'"))
                await conn.execute(text("SET LOCAL lock_timeout='5s'"))
                await conn.execute(text("SET LOCAL statement_timeout='60s'"))
                await conn.execute(text("SET LOCAL search_path=pg_catalog,public"))
                await conn.execute(text("LOCK TABLE public.alembic_version IN EXCLUSIVE MODE"))
                await _check_session(conn, pid, database)
                await verify_catalog(conn, FOUNDATION)
                if approval:
                    await approval.verify_recovery(conn)
                # Unqualified DDL in these immutable migrations creates objects in public.
                await conn.execute(text("SET LOCAL search_path=public"))
                await conn.run_sync(upgrade_shared, database)
                await _check_session(conn, pid, database)
                await verify_catalog(conn, DESTINATION)
                if approval:
                    approval.check_inputs()
                    approval.recovery.check(approval.recovery.foundation_digest)
            except BaseException:
                outcome = "unknown"
                await tx.rollback()
                outcome = "rolled_back"
                raise
            try:
                outcome = "unknown"
                await tx.commit()
                outcome = "committed"
            except BaseException as exc:
                raise CommitOutcomeUnknown("Commit unconfirmed; reconcile before retry") from exc
            await _check_session(conn, pid, database)
            if (
                await conn.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                is not True
            ):
                raise RuntimeError("Committed; lock release unconfirmed")
    finally:
        try:
            if approval:
                receipt = f"disposable/{database}/{pid}/{DESTINATION}/{outcome}"
                record_outcome(
                    approval.ledger,
                    approval.observed,
                    outcome,
                    hashlib.sha256(receipt.encode()).hexdigest(),
                )
        finally:
            await engine.dispose()
