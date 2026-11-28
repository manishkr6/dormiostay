import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import asyncpg

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


class MockBatchResult:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[str, str]]:
        return list(self.rows)


class MockKeysetConnection:
    def __init__(
        self,
        table_data: dict[str, list[tuple[str, str]]] | None = None,
        error_on_query: BaseException | None = None,
    ) -> None:
        self.table_data = table_data or {}
        self.error_on_query = error_on_query
        self.executed_queries: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, stmt: Any) -> Any:
        sql = str(stmt)
        if "transaction_isolation" in sql:
            return "repeatable read"
        if "row_security" in sql:
            return "off"
        if "TimeZone" in sql:
            return "UTC"
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> MockBatchResult:
        if self.error_on_query:
            raise self.error_on_query
        sql = str(stmt)
        p = params or {}
        self.executed_queries.append((sql, dict(p)))

        for table, rows in self.table_data.items():
            if f'"{table}"' in sql:
                limit = p.get("limit", 500)
                last_id = p.get("last_id")
                if last_id is None:
                    matching = [r for r in rows][:limit]
                else:
                    matching = [r for r in rows if r[1] > last_id][:limit]
                return MockBatchResult(matching)

        return MockBatchResult([])


@pytest.mark.anyio
async def test_foundation_fingerprint_keyset_pagination_multiple_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    test_rows = [
        (
            f'{{"id": "00000000-0000-0000-0000-{i:012d}", "name": "r{i}"}}',
            f"00000000-0000-0000-0000-{i:012d}",
        )
        for i in range(1, 6)
    ]
    data = {table: list(test_rows) for table in APPLICATION_TABLES}
    conn = MockKeysetConnection(table_data=data)

    digest = await foundation_fingerprint(conn, batch_size=2)  # type: ignore[arg-type]
    assert len(digest) == 64

    # Each table with 5 rows and batch_size=2 should execute 3 queries (2 + 2 + 1)
    # Total queries = 11 tables * 3 = 33 queries
    assert len(conn.executed_queries) == 33

    # Inspect the 3 queries for the first table
    q1, p1 = conn.executed_queries[0]
    assert "ORDER BY t.id LIMIT :limit" in q1
    assert "WHERE" not in q1
    assert p1 == {"limit": 2}

    q2, p2 = conn.executed_queries[1]
    assert ":last_id::uuid" not in q2
    assert "WHERE t.id > CAST(:last_id AS uuid) ORDER BY t.id LIMIT :limit" in q2
    assert p2 == {"last_id": "00000000-0000-0000-0000-000000000002", "limit": 2}

    q3, p3 = conn.executed_queries[2]
    assert ":last_id::uuid" not in q3
    assert "WHERE t.id > CAST(:last_id AS uuid) ORDER BY t.id LIMIT :limit" in q3
    assert p3 == {"last_id": "00000000-0000-0000-0000-000000000004", "limit": 2}


@pytest.mark.anyio
async def test_foundation_fingerprint_matches_unbatched_linear_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    test_rows = [
        (
            f'{{"id": "00000000-0000-0000-0000-{i:012d}", "v": {i}}}',
            f"00000000-0000-0000-0000-{i:012d}",
        )
        for i in range(1, 8)
    ]
    data = {table: list(test_rows) for table in APPLICATION_TABLES}

    # Calculate expected digest linearly
    expected_digest = hashlib.sha256()
    for table in sorted(APPLICATION_TABLES):
        expected_digest.update(json.dumps(table).encode() + b"\n")
        for row_json, _ in test_rows:
            expected_digest.update(row_json.encode() + b"\n")

    conn_b2 = MockKeysetConnection(table_data=data)
    digest_b2 = await foundation_fingerprint(conn_b2, batch_size=2)  # type: ignore[arg-type]

    conn_b3 = MockKeysetConnection(table_data=data)
    digest_b3 = await foundation_fingerprint(conn_b3, batch_size=3)  # type: ignore[arg-type]

    conn_b500 = MockKeysetConnection(table_data=data)
    digest_b500 = await foundation_fingerprint(conn_b500, batch_size=500)  # type: ignore[arg-type]

    assert digest_b2 == expected_digest.hexdigest()
    assert digest_b3 == expected_digest.hexdigest()
    assert digest_b500 == expected_digest.hexdigest()


@pytest.mark.anyio
async def test_foundation_fingerprint_empty_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    conn = MockKeysetConnection(table_data={})

    digest = await foundation_fingerprint(conn)  # type: ignore[arg-type]
    assert len(digest) == 64
    # Empty tables execute exactly 1 query per table
    assert len(conn.executed_queries) == len(APPLICATION_TABLES)
    for q, p in conn.executed_queries:
        assert "WHERE" not in q
        assert p == {"limit": 500}


@pytest.mark.anyio
async def test_foundation_fingerprint_exact_batch_size_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    test_rows = [
        ('{"id": "00000000-0000-0000-0000-000000000001"}', "00000000-0000-0000-0000-000000000001"),
        ('{"id": "00000000-0000-0000-0000-000000000002"}', "00000000-0000-0000-0000-000000000002"),
        ('{"id": "00000000-0000-0000-0000-000000000003"}', "00000000-0000-0000-0000-000000000003"),
        ('{"id": "00000000-0000-0000-0000-000000000004"}', "00000000-0000-0000-0000-000000000004"),
    ]
    data = {sorted(APPLICATION_TABLES)[0]: list(test_rows)}
    conn = MockKeysetConnection(table_data=data)

    digest = await foundation_fingerprint(conn, batch_size=2)  # type: ignore[arg-type]
    assert len(digest) == 64

    first_table_queries = [
        q for q, _ in conn.executed_queries if f'"{sorted(APPLICATION_TABLES)[0]}"' in q
    ]
    # Exactly 4 rows with batch_size=2 executes 3 queries: (2 rows, 2 rows, 0 rows)
    assert len(first_table_queries) == 3


@pytest.mark.anyio
async def test_foundation_fingerprint_propagates_database_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    conn = MockKeysetConnection(error_on_query=RuntimeError("database read failed"))

    with pytest.raises(RuntimeError, match="database read failed"):
        await foundation_fingerprint(conn)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_foundation_fingerprint_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    conn = MockKeysetConnection(error_on_query=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await foundation_fingerprint(conn)  # type: ignore[arg-type]


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_batch_size", [0, -1, -500, 1.5, "10", True, False])
async def test_foundation_fingerprint_rejects_non_positive_or_non_integer_batch_size(
    invalid_batch_size: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    conn = MockKeysetConnection(table_data={})

    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        await foundation_fingerprint(conn, batch_size=invalid_batch_size)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_foundation_fingerprint_asyncpg_query_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify generated keyset pagination SQL compiles cleanly under asyncpg.

    Ensures no syntax errors, colon tokenizer collision, or parameter-binding defects.
    """
    monkeypatch.setattr("app.db.recovery.verify_catalog", AsyncMock())
    test_rows = [
        (
            f'{{"id": "00000000-0000-0000-0000-{i:012d}", "name": "r{i}"}}',
            f"00000000-0000-0000-0000-{i:012d}",
        )
        for i in range(1, 4)
    ]
    data = {sorted(APPLICATION_TABLES)[0]: list(test_rows)}
    conn = MockKeysetConnection(table_data=data)

    await foundation_fingerprint(conn, batch_size=2)  # type: ignore[arg-type]

    dialect = asyncpg.dialect()  # type: ignore[no-untyped-call]
    assert len(conn.executed_queries) >= 2

    for raw_sql, params in conn.executed_queries:
        assert ":last_id::uuid" not in raw_sql
        stmt = text(raw_sql)
        compiled = stmt.compile(dialect=dialect)
        assert ":last_id::uuid" not in compiled.string

        if "WHERE" in raw_sql:
            assert "WHERE t.id > CAST(:last_id AS uuid) ORDER BY t.id LIMIT :limit" in raw_sql
            assert "CAST($1 AS uuid)" in compiled.string
            assert "LIMIT $2" in compiled.string
            assert set(compiled.params.keys()) == {"last_id", "limit"}
            constructed = compiled.construct_params(params)
            assert constructed["last_id"] == params["last_id"]
            assert constructed["limit"] == params["limit"]
        else:
            assert "ORDER BY t.id LIMIT :limit" in raw_sql
            assert "LIMIT $1" in compiled.string
            assert set(compiled.params.keys()) == {"limit"}
            constructed = compiled.construct_params(params)
            assert constructed["limit"] == params["limit"]
