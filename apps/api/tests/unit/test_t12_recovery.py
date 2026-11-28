from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.db.recovery import RecoveryEvidence


def recovery_fields() -> dict[str, Any]:
    now = datetime.now(UTC)
    return dict(
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
        retained_until=now + timedelta(days=2),
        recovery_owner="owner",
        independent_approver="reviewer",
        foundation_digest="1" * 64,
        write_freeze_record="test",
        rehearsal_record="test",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"artifact": "snap-wandering-flower-bi9ojapb"},
        {"rehearsal_branch": "br-holy-lake-bi0hc1lu"},
        {"rehearsal_branch": "br-weathered-dust-bim5fibs"},
        {"independent_approver": "OWNER"},
        {"foundation_digest": "2" * 64},
        {"expires_at": datetime.now(UTC) - timedelta(hours=1)},
        {"rehearsed_at": datetime.now(UTC) + timedelta(days=2)},
    ],
)
def test_reject_recovery_evidence(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RecoveryEvidence.model_validate(recovery_fields() | overrides).check("1" * 64)


def test_fresh_recovery_contract() -> None:
    RecoveryEvidence.model_validate(recovery_fields()).check("1" * 64)
