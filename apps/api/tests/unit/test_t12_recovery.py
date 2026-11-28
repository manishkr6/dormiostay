import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.db.grant_guard import APPLICATION_TABLES
from app.db.recovery import RecoveryEvidence, foundation_fingerprint


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


class MockStreamResult:
    def __init__(
        self,
        rows: list[tuple[str]],
        fail_during_iteration: BaseException | None = None,
    ) -> None:
        self.rows = rows
        self.fail_during_iteration = fail_during_iteration
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[tuple[str]]:
        for row in self.rows:
            if self.fail_during_iteration:
                raise self.fail_during_iteration
            yield row

    async def close(self) -> None:
        self.closed = True


class MockStreamingConnection:
    def __init__(
        self,
        table_streams: dict[str, MockStreamResult] | None = None,
    ) -> None:
        self.table_streams = table_streams or {}
        self.created_streams: list[MockStreamResult] = []

    async def scalar(self, stmt: Any) -> Any:
        sql = str(stmt)
        if "transaction_isolation" in sql:
            return "repeatable read"
        if "row_security" in sql:
            return "off"
        if "TimeZone" in sql:
            return "UTC"
        return None

    async def stream(self, stmt: Any) -> MockStreamResult:
        sql = str(stmt)
        for table, stream in self.table_streams.items():
            if f'"{table}"' in sql:
                self.created_streams.append(stream)
                return stream
        stream = MockStreamResult([('{"id": 1}',)])
        self.created_streams.append(stream)
        return stream


@pytest.mark.anyio
async def test_foundation_fingerprint_closes_all_cursors_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    streams = {table: MockStreamResult([('{"id": 1}',)]) for table in APPLICATION_TABLES}
    conn = MockStreamingConnection(table_streams=streams)

    digest = await foundation_fingerprint(conn)  # type: ignore[arg-type]
    assert len(digest) == 64
    assert len(streams) == 11
    for table, stream in streams.items():
        assert stream.closed, f"Stream for table {table} was not closed"


@pytest.mark.anyio
async def test_foundation_fingerprint_closes_cursor_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    failing_stream = MockStreamResult(
        rows=[('{"id": 1}',)],
        fail_during_iteration=RuntimeError("Stream read failure"),
    )
    first_table = sorted(APPLICATION_TABLES)[0]
    conn = MockStreamingConnection(table_streams={first_table: failing_stream})

    with pytest.raises(RuntimeError, match="Stream read failure"):
        await foundation_fingerprint(conn)  # type: ignore[arg-type]

    assert failing_stream.closed, "Stream must be closed on exception"


@pytest.mark.anyio
async def test_foundation_fingerprint_closes_cursor_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    cancelling_stream = MockStreamResult(
        rows=[('{"id": 1}',)],
        fail_during_iteration=asyncio.CancelledError(),
    )
    last_table = sorted(APPLICATION_TABLES)[-1]
    conn = MockStreamingConnection(table_streams={last_table: cancelling_stream})

    with pytest.raises(asyncio.CancelledError):
        await foundation_fingerprint(conn)  # type: ignore[arg-type]

    assert cancelling_stream.closed, "Stream must be closed on cancellation"
