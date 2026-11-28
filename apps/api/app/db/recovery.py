"""Validate recovery evidence and compute read-only, reproducible rehearsal fingerprints."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.grant_guard import APPLICATION_TABLES
from app.db.security_catalog import FOUNDATION, verify_catalog


class RecoveryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project: Literal["small-dew-81510029"]
    source_branch: Literal["br-weathered-dust-bim5fibs"]
    endpoint: Literal["ep-cold-smoke-bir1s74t"]
    database: Literal["neondb"]
    revision: Literal["0001_foundation"]
    artifact: str = Field(pattern=r"^snap-[a-z0-9-]+$")
    rehearsal_branch: str = Field(pattern=r"^br-[a-z0-9-]+$")
    captured_at: AwareDatetime
    rehearsed_at: AwareDatetime
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    retained_until: AwareDatetime
    recovery_owner: str = Field(min_length=1)
    independent_approver: str = Field(min_length=1)
    foundation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    write_freeze_record: str = Field(min_length=1)
    rehearsal_record: str = Field(min_length=1)

    def check(self, observed_digest: str, now: datetime | None = None) -> None:
        instant = now or datetime.now(UTC)
        if not (
            self.captured_at
            <= self.rehearsed_at
            <= self.approved_at
            <= instant
            < self.expires_at
            <= self.retained_until
        ):
            raise ValueError("Invalid recovery evidence timeline")
        if self.expires_at - self.captured_at > timedelta(hours=24):
            raise ValueError("Recovery evidence exceeds release window")
        if self.artifact == "snap-wandering-flower-bi9ojapb" or self.rehearsal_branch in {
            self.source_branch,
            "br-holy-lake-bi0hc1lu",
        }:
            raise ValueError("Ineligible recovery artifact or rehearsal destination")
        if self.recovery_owner.casefold() == self.independent_approver.casefold():
            raise ValueError("Independent recovery approval required")
        if observed_digest != self.foundation_digest:
            raise ValueError("Recovery baseline changed")


FINGERPRINT_BATCH_SIZE: int = 500


async def foundation_fingerprint(
    connection: AsyncConnection, *, batch_size: int = FINGERPRINT_BATCH_SIZE
) -> str:
    """Run in a stable snapshot, with owner visibility of FORCE RLS tables established."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    await verify_catalog(connection, FOUNDATION)
    isolation = await connection.scalar(text("SHOW transaction_isolation"))
    if isolation not in {"repeatable read", "serializable"}:
        raise RuntimeError("Stable snapshot required")
    # row_security=off makes PostgreSQL raise if a query would filter any rows.
    if await connection.scalar(text("SHOW row_security")) != "off":
        raise RuntimeError("Complete row visibility required")
    if await connection.scalar(text("SHOW TimeZone")) != "UTC":
        raise RuntimeError("UTC required for stable fingerprints")
    digest = hashlib.sha256()
    for table in sorted(APPLICATION_TABLES):
        digest.update(json.dumps(table).encode() + b"\n")
        last_id: str | None = None
        while True:
            if last_id is None:
                stmt = text(
                    f'SELECT to_jsonb(t)::text, t.id::text FROM public."{table}" t '
                    f"ORDER BY t.id LIMIT :limit"
                )
                params: dict[str, Any] = {"limit": batch_size}
            else:
                stmt = text(
                    f'SELECT to_jsonb(t)::text, t.id::text FROM public."{table}" t '
                    f"WHERE t.id > CAST(:last_id AS uuid) ORDER BY t.id LIMIT :limit"
                )
                params = {"last_id": last_id, "limit": batch_size}
            result = await connection.execute(stmt, params)
            rows = result.all()
            if not rows:
                break
            for row_json, _ in rows:
                digest.update(row_json.encode() + b"\n")
            if len(rows) < batch_size:
                break
            last_id = str(rows[-1][1])
    return digest.hexdigest()


async def verify_isolated_rehearsal(
    source: AsyncConnection, rehearsal: AsyncConnection, evidence: RecoveryEvidence
) -> None:
    """Compare separately authenticated source/restore sessions under stable read-only snapshots.

    Provider control-plane provenance and the freeze record must be checked by the custodian
    first. This comparison alone cannot certify the origin of a recovery artifact.
    """
    for connection in (source, rehearsal):
        if await connection.scalar(text("SHOW transaction_read_only")) != "on":
            raise RuntimeError("Recovery comparison requires read-only transactions")
    # Test fixtures use two distinctly named databases. For a same-named cloud restore
    # the independent provider identity adapter is still required before enabling this path.
    source_name = await source.scalar(text("SELECT current_database()"))
    restore_name = await rehearsal.scalar(text("SELECT current_database()"))
    if not source_name or not restore_name or source_name == restore_name:
        raise RuntimeError("Isolated rehearsal destination unproven")
    source_digest = await foundation_fingerprint(source)
    evidence.check(source_digest)
    if await foundation_fingerprint(rehearsal) != source_digest:
        raise RuntimeError("Rehearsal data differs from verified source")
