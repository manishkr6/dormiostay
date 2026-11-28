"""Offline release binding validation. A successful comparison never authorizes execution."""

import hashlib
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ReleaseBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    repository: Literal["manishkr6/dormiostay"]
    environment: Literal["staging-gate3b"]
    project: Literal["small-dew-81510029"]
    branch: Literal["br-weathered-dust-bim5fibs"]
    endpoint: Literal["ep-cold-smoke-bir1s74t"]
    host: Literal["ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech"]
    database: Literal["neondb"]
    migration_role: Literal["neondb_owner"]
    runtime_role: Literal["dormiostay_app"]
    source: Literal["0001_foundation"]
    destination: Literal["0003_row_level_security"]
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    ref: str = Field(pattern=r"^refs/heads/.+$")
    workflow_digest: Digest
    artifact_digest: Digest
    dependency_digest: Digest
    manifest_digest: Digest
    recovery_digest: Digest
    run_id: int = Field(gt=0, strict=True)
    attempt: int = Field(ge=1, le=1, strict=True)
    initiator_id: int = Field(gt=0, strict=True)


class ReleaseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding: ReleaseBinding
    reviewer_id: int = Field(gt=0, strict=True)
    issued: AwareDatetime
    expires: AwareDatetime

    def check(self, observed: ReleaseBinding, now: datetime | None = None) -> None:
        if observed != self.binding or self.reviewer_id == observed.initiator_id:
            raise ValueError("Release binding or independent reviewer mismatch")
        if not self.issued <= (now or datetime.now(UTC)) < self.expires:
            raise ValueError("Release approval outside validity window")
        if self.expires - self.issued > timedelta(hours=1):
            raise ValueError("Release approval exceeds one hour")


def consume_at_custodian(
    ledger: sqlite3.Connection, decision: ReleaseDecision, observed: ReleaseBinding
) -> None:
    """Local replay semantics for a custodian-owned durable ledger, after authentication."""
    decision.check(observed)
    if ledger.in_transaction or ledger.isolation_level is None:
        raise ValueError("Dedicated transactional ledger required")
    with ledger:
        ledger.execute(
            "CREATE TABLE IF NOT EXISTS consumed (repository TEXT NOT NULL, run_id INTEGER,"
            " decision TEXT NOT NULL, PRIMARY KEY(repository,run_id))"
        )
        ledger.execute(
            "INSERT INTO consumed VALUES (?,?,?)",
            (
                observed.repository,
                observed.run_id,
                decision.model_dump_json(),
            ),
        )


def record_outcome(
    ledger: sqlite3.Connection,
    binding: ReleaseBinding,
    status: Literal["committed", "rolled_back", "unknown", "aborted"],
    evidence_digest: str,
) -> None:
    """Record one immutable-by-interface terminal outcome against a consumed approval."""
    if status not in {"committed", "rolled_back", "unknown", "aborted"}:
        raise ValueError("Unknown outcome")
    if not re.fullmatch("[0-9a-f]{64}", evidence_digest):
        raise ValueError("Invalid release evidence digest")
    if ledger.in_transaction or ledger.isolation_level is None:
        raise ValueError("Dedicated transactional ledger required")
    with ledger:
        ledger.execute(
            "CREATE TABLE IF NOT EXISTS outcomes (repository TEXT, run_id INTEGER,"
            " status TEXT NOT NULL, recorded TEXT NOT NULL, evidence_digest TEXT NOT NULL,"
            " PRIMARY KEY(repository,run_id))"
        )
        row = ledger.execute(
            "SELECT decision FROM consumed WHERE repository=? AND run_id=?",
            (binding.repository, binding.run_id),
        ).fetchone()
        if row is None or ReleaseDecision.model_validate_json(row[0]).binding != binding:
            raise ValueError("No matching consumed release decision")
        ledger.execute(
            "INSERT INTO outcomes VALUES (?,?,?,?,?)",
            (
                binding.repository,
                binding.run_id,
                status,
                datetime.now(UTC).isoformat(),
                evidence_digest,
            ),
        )


def digest_files(paths: tuple[Path, ...]) -> str:
    """Bind names, lengths and bytes of an ordered set of repository release inputs."""
    digest = hashlib.sha256()
    for path in paths:
        name = path.name.encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def local_input_digests() -> tuple[str, str]:
    from app.db.security_catalog import MANIFEST_DIR

    api = Path(__file__).resolve().parents[2]
    return (
        digest_files((api / "pyproject.toml", api / "requirements.txt")),
        digest_files((MANIFEST_DIR / "foundation.jsonl", MANIFEST_DIR / "rls.delta")),
    )
