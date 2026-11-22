"""Guards for Alembic database migrations.

Enforces fail-closed validation:
1. Disposable test databases:
   Must match `dormiostay_t03_test_[a-z0-9_]+` on localhost / 127.0.0.1 / ::1.
   Permits both upgrade and downgrade operations for automated testing.
2. Remote database migrations:
   ALL remote database migrations (staging upgrades, staging downgrades,
   production upgrades, production downgrades, and remote offline SQL generation)
   are strictly DISABLED until independently verified staging infrastructure is available.
   Self-declared `APPROVED_STAGING_*` environment variables cannot authorize remote execution.
   Target validation fails closed before any DDL or migration output is generated.
"""

import os
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.test_target import validate_test_url, verify_test_connection

REMOTE_DISABLED_MSG = (
    "Remote database migrations are strictly disabled until independently "
    "verified staging infrastructure is available"
)
LOCAL_DISPOSABLE_REQUIRED_MSG = (
    "An explicitly approved disposable database name is required "
    "(TEST_DATABASE_NAME); remote migrations are disabled"
)


@dataclass(frozen=True)
class ApprovedTargetIdentity:
    """Authoritative target identity placeholder (remote execution disabled in T-05)."""

    host: str
    database: str
    migration_role: str
    project_id: str | None = None
    branch_id: str | None = None


def get_approved_staging_identity() -> ApprovedTargetIdentity | None:
    """Authoritative staging identity lookup (deferred: remote migrations disabled)."""
    return None


def detect_migration_action(
    config: object | None = None,
    context_obj: object | None = None,
) -> str:
    """Detect whether the current migration command is an upgrade or downgrade.

    Fail-safe: any signal indicating downgrade (CLI args, programmatic context_opts,
    config command) classifies the action as 'downgrade'. MIGRATION_ACTION=upgrade
    CANNOT override an actual downgrade.
    """
    # 1. Programmatic Alembic execution via EnvironmentContext (e.g. command.downgrade)
    if context_obj is not None and hasattr(context_obj, "context_opts"):
        opts = getattr(context_obj, "context_opts", None)
        if isinstance(opts, dict):
            fn = opts.get("fn")
            if fn is not None:
                fn_name = str(getattr(fn, "__name__", fn)).lower()
                if "downgrade" in fn_name:
                    return "downgrade"
            dest = str(opts.get("destination_rev", "") or "").lower()
            if (
                dest in {"base", "-1"}
                or (dest.startswith("-") and dest[1:].isdigit())
                or "head:base" in dest
                or ":base" in dest
            ):
                return "downgrade"

    # 2. Config command options (CLI or programmatic with cmd_opts set)
    if config is not None and hasattr(config, "cmd_opts") and config.cmd_opts is not None:
        cmd = getattr(config.cmd_opts, "cmd", None)
        if cmd and isinstance(cmd, tuple) and len(cmd) > 0:
            cmd_name = str(getattr(cmd[0], "__name__", cmd[0])).lower()
            if "downgrade" in cmd_name:
                return "downgrade"
        rev = getattr(config.cmd_opts, "revision", None)
        if rev is not None:
            rev_str = str(rev).lower()
            if (
                rev_str in {"base", "-1"}
                or (rev_str.startswith("-") and rev_str[1:].isdigit())
                or "head:base" in rev_str
                or ":base" in rev_str
            ):
                return "downgrade"

    # 3. CLI arguments inspection
    for arg in sys.argv[1:]:
        arg_lower = arg.lower()
        if (
            arg_lower in {"downgrade", "downgrading", "base"}
            or arg_lower == "-1"
            or (arg_lower.startswith("-") and arg_lower[1:].isdigit())
            or "head:base" in arg_lower
            or ":base" in arg_lower
        ):
            return "downgrade"

    # 4. Caller-declared action: only accepted if NO downgrade signal was detected
    env_action = os.environ.get("MIGRATION_ACTION")
    if env_action:
        env_action_lower = env_action.lower()
        if env_action_lower in {"downgrade", "downgrading"}:
            return "downgrade"
        if env_action_lower in {"upgrade", "upgrading"}:
            return "upgrade"

    return "upgrade"


def resolve_migration_target(
    direct_url: str | None,
    test_db_name: str | None = None,
    migration_env: str | None = None,
    migration_confirmation: str | None = None,
    action: str = "upgrade",
    approved_identity: ApprovedTargetIdentity | None = None,
) -> tuple[URL, ApprovedTargetIdentity | str]:
    """Validate and resolve migration target URL.

    Security Policy:
    - ALL remote database migrations are disabled until independently verified staging
      infrastructure is available.
    - Caller-controlled APPROVED_STAGING_* identity cannot authorize remote execution.
    - Local disposable test databases (dormiostay_t03_test_*) on localhost/127.0.0.1/::1
      are permitted for both upgrade and downgrade.
    - All other targets fail closed with ValueError.
    """
    if not direct_url:
        raise ValueError("DATABASE_URL_DIRECT is required for migrations")

    try:
        parsed = urlparse(direct_url)
    except Exception as exc:
        raise ValueError(f"Invalid DATABASE_URL_DIRECT: {exc}") from None

    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise ValueError("DATABASE_URL_DIRECT must be a PostgreSQL connection URL")

    host = parsed.hostname or ""

    # Mode 1: Reject ALL remote database targets regardless of caller parameters
    if not host or host not in {"127.0.0.1", "localhost", "::1"}:
        if migration_env == "production":
            raise ValueError("Production remote migrations are strictly disabled in T-05")
        if action == "downgrade":
            raise ValueError("Remote database downgrade operations are strictly rejected by policy")
        raise ValueError(REMOTE_DISABLED_MSG)

    # Mode 2: Local loopback targets MUST be approved disposable test databases
    if test_db_name:
        url = validate_test_url(direct_url, test_db_name)
        return url, test_db_name

    if migration_env in {"staging", "production"}:
        raise ValueError("Migration target for staging cannot be a local disposable host")

    raise ValueError(LOCAL_DISPOSABLE_REQUIRED_MSG)


async def verify_migration_connection(
    connection: AsyncConnection,
    expected_target: ApprovedTargetIdentity | str,
    test_db_name: str | None = None,
) -> None:
    """Verify runtime database connection before executing DDL."""
    if test_db_name:
        assert isinstance(expected_target, str)
        await verify_test_connection(connection, expected_target)
        return

    raise RuntimeError(REMOTE_DISABLED_MSG)
