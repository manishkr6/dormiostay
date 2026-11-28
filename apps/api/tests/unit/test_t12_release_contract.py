import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from app.db.release_contract import (
    ReleaseBinding,
    ReleaseDecision,
    consume_at_custodian,
    record_outcome,
)


def test_custodian_binding_and_single_use() -> None:
    now = datetime.now(UTC)
    binding = ReleaseBinding.model_validate(
        dict(
            repository="manishkr6/dormiostay",
            environment="staging-gate3b",
            project="small-dew-81510029",
            branch="br-weathered-dust-bim5fibs",
            endpoint="ep-cold-smoke-bir1s74t",
            host="ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech",
            database="neondb",
            migration_role="neondb_owner",
            runtime_role="dormiostay_app",
            source="0001_foundation",
            destination="0003_row_level_security",
            commit="1" * 40,
            ref="refs/heads/test-release",
            artifact_digest="a" * 64,
            workflow_digest="1" * 64,
            dependency_digest="2" * 64,
            manifest_digest="3" * 64,
            recovery_digest="4" * 64,
            run_id=21,
            attempt=1,
            initiator_id=1,
        )
    )
    decision = ReleaseDecision(
        binding=binding, reviewer_id=2, issued=now, expires=now + timedelta(minutes=5)
    )
    with pytest.raises(ValueError):
        decision.check(binding.model_copy(update={"commit": "3" * 40}))
    with pytest.raises(ValueError):
        decision.check(binding, now + timedelta(hours=1))
    with pytest.raises(ValueError):
        decision.model_copy(update={"reviewer_id": 1}).check(binding)
    db = sqlite3.connect(":memory:")
    try:
        consume_at_custodian(db, decision, binding)
        record_outcome(db, binding, "unknown", "0" * 64)
        with pytest.raises(sqlite3.IntegrityError):
            record_outcome(db, binding, "committed", "1" * 64)
        with pytest.raises(sqlite3.IntegrityError):
            consume_at_custodian(db, decision, binding)
    finally:
        db.close()


def test_remote_entry_refuses_even_with_dispatch_arguments() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.gate3b", "--enable"], capture_output=True
    )
    assert result.returncode != 0
    assert b"GATE3B_DISABLED" in result.stderr


@pytest.mark.anyio
@pytest.mark.parametrize(
    "target",
    [
        "postgresql://neondb_owner@remote.invalid/dormiostay_t03_test_gate3b",
        "postgresql://neondb_owner@127.0.0.1/neondb",
        "postgresql://dormiostay_app@127.0.0.1/dormiostay_t03_test_gate3b",
        "postgresql://neondb_owner@127.0.0.1/dormiostay_t03_test_gate3b?host=other.invalid",
    ],
)
async def test_runner_refuses_non_disposable_identity(target: str) -> None:
    from app.db.gate3b import run_disposable

    with pytest.raises(ValueError):
        await run_disposable(target, "dormiostay_t03_test_gate3b")


@pytest.mark.anyio
async def test_matching_control_plane_and_tls_still_require_provider_binding() -> None:
    import ssl
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.db.gate3b_identity import verify_staging_session
    from app.db.migration_guard import (
        APPROVED_STAGING_BRANCH_ID,
        APPROVED_STAGING_ENDPOINT_ID,
        APPROVED_STAGING_HOST,
        APPROVED_STAGING_PROJECT_ID,
        ApprovedTargetIdentity,
    )

    metadata = ApprovedTargetIdentity(
        APPROVED_STAGING_HOST,
        "neondb",
        "neondb_owner",
        APPROVED_STAGING_PROJECT_ID,
        APPROVED_STAGING_BRANCH_ID,
        APPROVED_STAGING_ENDPOINT_ID,
        True,
    )
    conn = AsyncMock()
    raw = MagicMock()
    conn.get_raw_connection.return_value = raw
    peer = raw.driver_connection._transport.get_extra_info.return_value
    peer.context.verify_mode = ssl.CERT_REQUIRED
    peer.context.check_hostname = True
    peer.server_hostname = APPROVED_STAGING_HOST
    peer.getpeercert.return_value = {"subjectAltName": [("DNS", APPROVED_STAGING_HOST)]}
    conn.execute.return_value = MagicMock()
    conn.execute.return_value.one.return_value = ("neondb", "neondb_owner", "neondb_owner")
    with patch("app.db.gate3b_identity.verify_neon_control_plane", return_value=metadata) as lookup:
        with pytest.raises(RuntimeError, match="binding unavailable"):
            await verify_staging_session(conn)
        lookup.assert_called_once_with(require_read_write=True)
        peer.context.check_hostname = False
        with pytest.raises(RuntimeError, match="TLS peer"):
            await verify_staging_session(conn)
