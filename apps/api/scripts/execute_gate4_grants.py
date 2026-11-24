"""Execute Gate 4 application role grants on verified staging infrastructure.

Uses the single supported async SQLAlchemy execution architecture:
- Connects over verified TLS to authoritative Neon staging endpoint.
- Verifies control-plane metadata, administrator identity, and target role.
- Executes targeted revocations and in-transaction pre-commit security verification.
- Enforces the T-05 zero-table-grant security policy within a single atomic transaction.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add api package to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine

from app.db.grant_guard import (
    APPROVED_APPLICATION_ROLE,
    execute_gate4_grants_async,
    resolve_grant_target,
)


async def main() -> None:
    direct_url = os.environ.get("DATABASE_URL_DIRECT")
    if not direct_url:
        print("ERROR: DATABASE_URL_DIRECT is required")
        sys.exit(1)

    migration_env = os.environ.get("MIGRATION_ENVIRONMENT")
    migration_confirm = os.environ.get("MIGRATION_CONFIRMATION")
    target_role = os.environ.get("APPLICATION_ROLE", APPROVED_APPLICATION_ROLE)
    api_key = os.environ.get("NEON_API_KEY")

    # 1. Resolve and verify target environment and control-plane metadata
    try:
        target_url, approved_identity = resolve_grant_target(
            direct_url=direct_url,
            migration_env=migration_env,
            migration_confirmation=migration_confirm,
            target_role=target_role,
            neon_api_key=api_key,
        )
    except Exception as exc:
        print(f"GATE 4 ABORTED: Target verification failed: {exc}")
        sys.exit(1)

    # 2. Connect over verified TLS and execute single-transaction preflight & grant workflow
    print(f"Connecting to staging database as {approved_identity.migration_role}...")
    engine = create_async_engine(target_url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            print(f"Executing Gate 4 atomic workflow for {target_role}...")
            results = await execute_gate4_grants_async(
                connection=connection,
                expected_target=approved_identity,
                target_role=target_role,
            )
            print(
                f"Gate 4 grants executed and verified in-transaction before COMMIT: "
                f"{len(results['table_privileges'])} tables verified with zero privileges."
            )
    finally:
        await engine.dispose()

    print("GATE 4 SUCCESS — ZERO-GRANT APPLICATION ROLE POLICY VERIFIED")


if __name__ == "__main__":
    asyncio.run(main())
