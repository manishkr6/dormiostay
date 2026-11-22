"""Unit tests for T-05 deployment configuration, environment readiness, and migration security."""

import asyncio
import contextlib
import io
import re
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import alembic.command
import pytest
from alembic.config import CommandLine, Config
from fastapi.testclient import TestClient
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.migration_guard import (
    ApprovedTargetIdentity,
    detect_migration_action,
    resolve_migration_target,
)
from app.db.test_target import validate_test_url
from app.main import app

API_DIR = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI_PATH = str(API_DIR / "alembic.ini")


def test_requirements_file_matches_pyproject() -> None:
    """Verify apps/api/requirements.txt contains production dependencies."""
    req_file = API_DIR / "requirements.txt"
    pyproject_file = API_DIR / "pyproject.toml"

    assert req_file.exists(), "apps/api/requirements.txt must exist for Render deployment"
    req_text = req_file.read_text(encoding="utf-8")
    pyproject_text = pyproject_file.read_text(encoding="utf-8")

    expected_packages = [
        "sqlalchemy",
        "alembic",
        "fastapi",
        "asyncpg",
        "pydantic",
        "pydantic-settings",
        "sentry-sdk",
        "uvicorn",
    ]
    for pkg in expected_packages:
        assert pkg in req_text, f"{pkg} missing from requirements.txt"
        assert pkg in pyproject_text, f"{pkg} missing from pyproject.toml"


def test_render_blueprint_specification() -> None:
    """Verify render.yaml exists at root and adheres to security constraints."""
    root_dir = API_DIR.parent.parent
    blueprint_file = root_dir / "render.yaml"

    assert blueprint_file.exists(), "render.yaml must exist at repository root"
    content = blueprint_file.read_text(encoding="utf-8")

    # Web static site configuration
    assert "name: dormiostay-web" in content
    assert "runtime: static" in content
    assert "branch: main" in content
    assert "buildCommand: npm ci && npm run build" in content
    assert "staticPublishPath: ./apps/web/dist" in content

    # Routing rules: SPA fallback must be active
    spa_match = re.search(r"source:\s*/\*\s*\n\s*destination:\s*/index\.html", content)
    assert spa_match is not None, "Missing /* SPA fallback route"

    # Active /api/* proxy rewrite must NOT be active targeting unverified destinations
    active_proxy_match = re.search(
        r"^(\s*)-\s+type:\s*rewrite\s*\n\s+source:\s*/api/\*",
        content,
        re.MULTILINE,
    )
    assert active_proxy_match is None, "Active proxy rewrite must remain disabled until verified"

    # Documented verification instructions must be present
    assert "SECURITY NOTICE: Disabled until the staging API service is provisioned" in content
    assert "DO NOT activate with unverified placeholders" in content

    # API web service configuration: explicit plan: free, branch: main, and APP_ENV: staging
    assert "name: dormiostay-api" in content
    assert "plan: free" in content
    assert "branch: main" in content
    assert "runtime: python" in content
    assert "rootDir: apps/api" in content
    assert "healthCheckPath: /healthz" in content
    assert "uvicorn app.main:app --host 0.0.0.0 --port $PORT" in content

    # Environment variables
    assert re.search(r"key:\s*APP_ENV\s+value:\s*staging", content), (
        "APP_ENV must be staging in blueprint"
    )
    assert re.search(r"key:\s*DATABASE_URL\s+sync:\s*false", content), (
        "DATABASE_URL must be sync: false"
    )
    assert re.search(r"key:\s*FRONTEND_ORIGIN\s+sync:\s*false", content), (
        "FRONTEND_ORIGIN must be sync: false"
    )
    assert re.search(r"key:\s*SENTRY_DSN\s+sync:\s*false", content), (
        "SENTRY_DSN must be sync: false"
    )

    # Worker cron must not be active prior to T-09
    assert not re.search(r"^-\s+type:\s*cron", content, re.MULTILINE), (
        "Worker cron must not be active before T-09"
    )


def test_health_and_readiness_routes_registered() -> None:
    """Verify liveness routes exist at root and under /api/v1 for reverse-proxy routing."""
    with TestClient(app) as client:
        # Root health check for Render platform probe
        health_res = client.get("/healthz")
        assert health_res.status_code == 200
        assert health_res.json() == {"status": "ok"}

        # Versioned health check for Web client startup probe through /api/* reverse proxy
        api_health_res = client.get("/api/v1/healthz")
        assert api_health_res.status_code == 200
        assert api_health_res.json() == {"status": "ok"}


def test_settings_environment_validation() -> None:
    """Verify settings validation across development, staging, and production."""
    for env in ["development", "staging", "production"]:
        settings = Settings(
            app_env=env,  # type: ignore[arg-type]
            database_url="postgresql+asyncpg://user:pass@ep-test.neon.tech/neondb",
            frontend_origin="https://dormiostay.com",  # type: ignore[arg-type]
        )
        assert settings.app_env == env
        assert settings.is_production is (env == "production")


# =============================================================================
# Migration Guard Unit Tests (Remote Disabled Policy & Local Disposable Safety)
# =============================================================================

APPROVED_STAGING = ApprovedTargetIdentity(
    host="ep-staging-approved-123.aws.neon.tech",
    database="neondb",
    migration_role="neondb_owner",
    project_id="dormiostay-staging-proj",
    branch_id="br-staging-main",
)


def test_disposable_test_migration_target_accepted() -> None:
    """Verify local disposable test database targets remain protected and accepted."""
    test_db = "dormiostay_t03_test_suite123"
    target, approved = resolve_migration_target(
        direct_url=f"postgresql://app@127.0.0.1:5433/{test_db}",
        test_db_name=test_db,
        action="upgrade",
    )
    assert approved == test_db
    assert target.database == test_db


def test_disposable_test_migration_target_allows_downgrade() -> None:
    """Verify local disposable test database permits downgrades for integration testing."""
    test_db = "dormiostay_t03_test_suite123"
    target, approved = resolve_migration_target(
        direct_url=f"postgresql://app@127.0.0.1:5433/{test_db}",
        test_db_name=test_db,
        action="downgrade",
    )
    assert approved == test_db
    assert target.database == test_db


def test_disposable_test_migration_target_rejected_on_non_test_name() -> None:
    """Verify non-disposable test targets are rejected during test runs."""
    with pytest.raises(ValueError, match="An explicitly approved disposable database name"):
        resolve_migration_target(
            direct_url="postgresql://app@127.0.0.1:5433/dormiostay_dev",
            test_db_name="dormiostay_dev",
        )


def test_remote_staging_upgrade_rejected() -> None:
    """Verify all remote staging upgrades are strictly rejected until verified infrastructure."""
    staging_url = (
        f"postgresql://{APPROVED_STAGING.migration_role}:pass"
        f"@{APPROVED_STAGING.host}/{APPROVED_STAGING.database}?sslmode=require"
    )
    with pytest.raises(
        ValueError, match="Remote database migrations are strictly disabled until independently"
    ):
        resolve_migration_target(
            direct_url=staging_url,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            approved_identity=APPROVED_STAGING,
        )


def test_remote_staging_downgrade_rejected() -> None:
    """Verify remote staging downgrades are strictly rejected by policy."""
    staging_url = (
        f"postgresql://{APPROVED_STAGING.migration_role}:pass"
        f"@{APPROVED_STAGING.host}/{APPROVED_STAGING.database}?sslmode=require"
    )
    with pytest.raises(
        ValueError, match="Remote database downgrade operations are strictly rejected"
    ):
        resolve_migration_target(
            direct_url=staging_url,
            migration_env="staging",
            migration_confirmation="staging",
            action="downgrade",
            approved_identity=APPROVED_STAGING,
        )


def test_remote_production_upgrade_rejected() -> None:
    """Verify all production remote upgrades are strictly rejected in T-05."""
    prod_url = "postgresql://neondb_owner:pass@ep-prod-123.aws.neon.tech/neondb?sslmode=require"
    with pytest.raises(ValueError, match="Production remote migrations are strictly disabled"):
        resolve_migration_target(
            direct_url=prod_url,
            migration_env="production",
            migration_confirmation="production",
            action="upgrade",
            approved_identity=None,
        )


def test_remote_production_downgrade_rejected() -> None:
    """Verify all production remote downgrades are strictly rejected in T-05."""
    prod_url = "postgresql://neondb_owner:pass@ep-prod-123.aws.neon.tech/neondb?sslmode=require"
    with pytest.raises(ValueError, match="Production remote migrations are strictly disabled"):
        resolve_migration_target(
            direct_url=prod_url,
            migration_env="production",
            migration_confirmation="production",
            action="downgrade",
            approved_identity=None,
        )


def test_remote_downgrade_to_base_rejected() -> None:
    """Verify destructive production downgrade to base is strictly rejected in T-05."""
    prod_url = "postgresql://neondb_owner:pass@ep-prod-123.aws.neon.tech/neondb?sslmode=require"
    with pytest.raises(ValueError, match="Production remote migrations are strictly disabled"):
        resolve_migration_target(
            direct_url=prod_url,
            migration_env="production",
            migration_confirmation="production",
            action="downgrade",
            approved_identity=None,
        )


def test_caller_controlled_approved_staging_cannot_authorize_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify self-declared APPROVED_STAGING_* values cannot authorize remote execution."""
    staging_host = "ep-attacker-controlled.aws.neon.tech"
    monkeypatch.setenv("APPROVED_STAGING_HOST", staging_host)
    monkeypatch.setenv("APPROVED_STAGING_DATABASE", "neondb")
    monkeypatch.setenv("APPROVED_STAGING_MIGRATION_ROLE", "neondb_owner")

    staging_url = f"postgresql://neondb_owner:pass@{staging_host}/neondb?sslmode=require"
    with pytest.raises(
        ValueError, match="Remote database migrations are strictly disabled until independently"
    ):
        resolve_migration_target(
            direct_url=staging_url,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
            approved_identity=ApprovedTargetIdentity(
                host=staging_host,
                database="neondb",
                migration_role="neondb_owner",
            ),
        )


def test_neutral_hostname_rejected() -> None:
    """Verify neutral remote hostnames cannot bypass the remote block."""
    neutral_url = "postgresql://neondb_owner:pass@ep-random-butterfly-999.aws.neon.tech/neondb?sslmode=require"
    with pytest.raises(
        ValueError, match="Remote database migrations are strictly disabled until independently"
    ):
        resolve_migration_target(
            direct_url=neutral_url,
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
        )


def test_arbitrary_hostname_rejected() -> None:
    """Verify arbitrary third-party hostnames are rejected."""
    arbitrary_url = "postgresql://user:pass@db.internal.example.com/mydb"
    with pytest.raises(
        ValueError, match="Remote database migrations are strictly disabled until independently"
    ):
        resolve_migration_target(
            direct_url=arbitrary_url,
        )


def test_local_host_rejected_without_approved_test_name() -> None:
    """Verify local loopback database fails closed without approved TEST_DATABASE_NAME."""
    with pytest.raises(
        ValueError, match="An explicitly approved disposable database name is required"
    ):
        resolve_migration_target(
            direct_url="postgresql://app@127.0.0.1:5433/dormiostay_dev",
            test_db_name=None,
        )


def test_staging_migration_rejected_on_local_disposable_host() -> None:
    """Verify staging migration fails closed if pointed at a local/loopback database."""
    with pytest.raises(ValueError, match="cannot be a local disposable host"):
        resolve_migration_target(
            direct_url="postgresql://user:pass@127.0.0.1/neondb?sslmode=require",
            migration_env="staging",
            migration_confirmation="staging",
            action="upgrade",
        )


def test_migration_fails_closed_when_unspecified() -> None:
    """Verify migration fails closed if target is unspecified or missing."""
    with pytest.raises(ValueError, match="DATABASE_URL_DIRECT is required for migrations"):
        resolve_migration_target(
            direct_url=None,
        )


def test_migration_fails_closed_on_invalid_scheme() -> None:
    """Verify non-postgres connection URLs fail closed."""
    with pytest.raises(ValueError, match="must be a PostgreSQL connection URL"):
        resolve_migration_target(
            direct_url="mysql://user:pass@127.0.0.1/mydb",
        )


# =============================================================================
# Action Detection Unit Tests (Downgrade Bypass Protections)
# =============================================================================


def test_detect_migration_action_cli_downgrade() -> None:
    """Verify CLI downgrade arguments are accurately detected."""
    for arg in ["downgrade", "base", "-1", "-5", "head:base"]:
        action = detect_migration_action(config=None, context_obj=None)
        # Without sys.argv it defaults to upgrade
        assert action == "upgrade"

        # With mock sys.argv containing downgrade arg
        orig_argv = sys.argv
        try:
            sys.argv = ["alembic", arg]
            assert detect_migration_action() == "downgrade"
        finally:
            sys.argv = orig_argv


def test_detect_migration_action_cmd_opts() -> None:
    """Verify Config.cmd_opts command name and revision detect downgrade."""

    def dummy_downgrade() -> None:
        pass

    mock_config = SimpleNamespace(cmd_opts=SimpleNamespace(cmd=(dummy_downgrade,), revision=None))
    assert detect_migration_action(config=mock_config) == "downgrade"

    mock_config_rev = SimpleNamespace(cmd_opts=SimpleNamespace(cmd=None, revision="base"))
    assert detect_migration_action(config=mock_config_rev) == "downgrade"


def test_detect_migration_action_programmatic_context() -> None:
    """Verify EnvironmentContext context_opts detect programmatic downgrade."""

    def dummy_downgrade(rev: str, ctx: object) -> None:
        pass

    ctx_with_fn = SimpleNamespace(context_opts={"fn": dummy_downgrade})
    assert detect_migration_action(context_obj=ctx_with_fn) == "downgrade"

    ctx_with_dest = SimpleNamespace(context_opts={"destination_rev": "base"})
    assert detect_migration_action(context_obj=ctx_with_dest) == "downgrade"

    ctx_with_range = SimpleNamespace(context_opts={"destination_rev": "head:base"})
    assert detect_migration_action(context_obj=ctx_with_range) == "downgrade"


def test_migration_action_env_cannot_override_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify MIGRATION_ACTION=upgrade cannot override a detected downgrade."""
    monkeypatch.setenv("MIGRATION_ACTION", "upgrade")

    # When CLI has downgrade
    orig_argv = sys.argv
    try:
        sys.argv = ["alembic", "downgrade", "base"]
        assert detect_migration_action() == "downgrade"
    finally:
        sys.argv = orig_argv

    # When context has downgrade function
    def dummy_downgrade(rev: str, ctx: object) -> None:
        pass

    ctx_obj = SimpleNamespace(context_opts={"fn": dummy_downgrade})
    assert detect_migration_action(context_obj=ctx_obj) == "downgrade"


# =============================================================================
# Programmatic Alembic Command Regression Tests (Astra Findings 1 & 2 Reproduction)
# =============================================================================


def test_programmatic_remote_staging_upgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify programmatic upgrade against remote staging is rejected with zero SQL output."""
    staging_url = (
        "postgresql://neondb_owner:pass@ep-staging-123.aws.neon.tech/neondb?sslmode=require"
    )
    monkeypatch.setenv("DATABASE_URL_DIRECT", staging_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "staging")
    monkeypatch.setenv("MIGRATION_CONFIRMATION", "staging")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    cfg = Config(ALEMBIC_INI_PATH)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        alembic.command.upgrade(cfg, "head", sql=True)

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "CREATE TABLE" not in combined_output
    assert "DROP TABLE" not in combined_output
    assert combined_output.strip() == ""


def test_programmatic_remote_staging_downgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify programmatic downgrade against remote staging is rejected with zero SQL output."""
    staging_url = (
        "postgresql://neondb_owner:pass@ep-staging-123.aws.neon.tech/neondb?sslmode=require"
    )
    monkeypatch.setenv("DATABASE_URL_DIRECT", staging_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "staging")
    monkeypatch.setenv("MIGRATION_CONFIRMATION", "staging")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    cfg = Config(ALEMBIC_INI_PATH)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        alembic.command.downgrade(cfg, "head:base", sql=True)

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "DROP TABLE" not in combined_output
    assert combined_output.strip() == ""


def test_programmatic_remote_production_upgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify programmatic upgrade against remote production is rejected with zero SQL output."""
    prod_url = "postgresql://neondb_owner:pass@ep-prod-999.aws.neon.tech/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL_DIRECT", prod_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "production")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    cfg = Config(ALEMBIC_INI_PATH)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        alembic.command.upgrade(cfg, "head", sql=True)

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "CREATE TABLE" not in combined_output
    assert combined_output.strip() == ""


def test_programmatic_remote_production_downgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify programmatic downgrade against remote production is rejected with zero SQL output."""
    prod_url = "postgresql://neondb_owner:pass@ep-prod-999.aws.neon.tech/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL_DIRECT", prod_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "production")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    cfg = Config(ALEMBIC_INI_PATH)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        alembic.command.downgrade(cfg, "head:base", sql=True)

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "DROP TABLE" not in combined_output
    assert combined_output.strip() == ""


def test_programmatic_downgrade_with_migration_action_upgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Astra reproduction: MIGRATION_ACTION=upgrade cannot bypass downgrade protection."""
    staging_host = "ep-staging-123.aws.neon.tech"
    staging_url = f"postgresql://neondb_owner:pass@{staging_host}/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL_DIRECT", staging_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "staging")
    monkeypatch.setenv("MIGRATION_CONFIRMATION", "staging")
    monkeypatch.setenv("APPROVED_STAGING_HOST", staging_host)
    monkeypatch.setenv("APPROVED_STAGING_DATABASE", "neondb")
    monkeypatch.setenv("APPROVED_STAGING_MIGRATION_ROLE", "neondb_owner")
    monkeypatch.setenv("MIGRATION_ACTION", "upgrade")  # Attempted override!
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    cfg = Config(ALEMBIC_INI_PATH)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        alembic.command.downgrade(cfg, "head:base", sql=True)

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "DROP TABLE" not in combined_output
    assert "audit_logs" not in combined_output
    assert combined_output.strip() == ""


def test_programmatic_caller_controlled_approved_staging_cannot_authorize_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Astra finding 1: Caller-controlled APPROVED_STAGING_* cannot authorize remote."""
    staging_host = "ep-staging-attacker-target.aws.neon.tech"
    staging_url = f"postgresql://neondb_owner:pass@{staging_host}/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL_DIRECT", staging_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "staging")
    monkeypatch.setenv("MIGRATION_CONFIRMATION", "staging")
    monkeypatch.setenv("APPROVED_STAGING_HOST", staging_host)
    monkeypatch.setenv("APPROVED_STAGING_DATABASE", "neondb")
    monkeypatch.setenv("APPROVED_STAGING_MIGRATION_ROLE", "neondb_owner")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    cfg = Config(ALEMBIC_INI_PATH)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        alembic.command.upgrade(cfg, "head", sql=True)

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "CREATE TABLE" not in combined_output
    assert combined_output.strip() == ""


# =============================================================================
# Alembic CLI Runner Regression Tests (CommandLine().main)
# =============================================================================


def test_cli_remote_staging_upgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Alembic CLI upgrade against remote staging is rejected with zero SQL output."""
    staging_url = (
        "postgresql://neondb_owner:pass@ep-staging-123.aws.neon.tech/neondb?sslmode=require"
    )
    monkeypatch.setenv("DATABASE_URL_DIRECT", staging_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "staging")
    monkeypatch.setenv("MIGRATION_CONFIRMATION", "staging")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    cmd = CommandLine()
    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        cmd.main(argv=["-c", ALEMBIC_INI_PATH, "upgrade", "head", "--sql"])

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "CREATE TABLE" not in combined_output
    assert combined_output.strip() == ""


def test_cli_remote_downgrade_with_migration_action_upgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Alembic CLI downgrade with MIGRATION_ACTION=upgrade is rejected with zero SQL."""
    staging_url = (
        "postgresql://neondb_owner:pass@ep-staging-123.aws.neon.tech/neondb?sslmode=require"
    )
    monkeypatch.setenv("DATABASE_URL_DIRECT", staging_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "staging")
    monkeypatch.setenv("MIGRATION_CONFIRMATION", "staging")
    monkeypatch.setenv("MIGRATION_ACTION", "upgrade")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    cmd = CommandLine()
    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        cmd.main(argv=["-c", ALEMBIC_INI_PATH, "downgrade", "head:base", "--sql"])

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "DROP TABLE" not in combined_output
    assert combined_output.strip() == ""


def test_cli_remote_production_downgrade_rejected_no_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Alembic CLI downgrade against production is rejected with zero SQL output."""
    prod_url = "postgresql://neondb_owner:pass@ep-prod-999.aws.neon.tech/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL_DIRECT", prod_url)
    monkeypatch.setenv("MIGRATION_ENVIRONMENT", "production")
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    cmd = CommandLine()
    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        cmd.main(argv=["-c", ALEMBIC_INI_PATH, "downgrade", "head:base", "--sql"])

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"
    combined_output = stdout_buf.getvalue() + stderr_buf.getvalue()
    assert "DROP TABLE" not in combined_output
    assert combined_output.strip() == ""


def test_cli_missing_database_url_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Alembic CLI fails closed when DATABASE_URL_DIRECT is missing."""
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.delenv("TEST_DATABASE_NAME", raising=False)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    cmd = CommandLine()
    with (
        contextlib.redirect_stdout(stdout_buf),
        contextlib.redirect_stderr(stderr_buf),
        pytest.raises(SystemExit) as exc_info,
    ):
        cmd.main(argv=["-c", ALEMBIC_INI_PATH, "upgrade", "head"])

    assert exc_info.value.code == "MIGRATION_TARGET_REJECTED"


# =============================================================================
# Local Disposable Test Database Compatibility Tests (T-03 Preservation)
# =============================================================================


def test_local_disposable_offline_upgrade_and_downgrade_functional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify authorized local disposable databases retain full upgrade and downgrade."""
    test_db = "dormiostay_t03_test_regression123"
    local_url = f"postgresql://app@127.0.0.1:5433/{test_db}"
    monkeypatch.setenv("DATABASE_URL_DIRECT", local_url)
    monkeypatch.setenv("TEST_DATABASE_NAME", test_db)

    cfg = Config(ALEMBIC_INI_PATH)

    # 1. Test offline upgrade SQL generation
    upgrade_buf = io.StringIO()
    with contextlib.redirect_stdout(upgrade_buf):
        alembic.command.upgrade(cfg, "head", sql=True)

    upgrade_sql = upgrade_buf.getvalue()
    assert "CREATE TABLE" in upgrade_sql
    assert "users" in upgrade_sql
    assert "audit_logs" in upgrade_sql

    # 2. Test offline downgrade SQL generation
    downgrade_buf = io.StringIO()
    with contextlib.redirect_stdout(downgrade_buf):
        alembic.command.downgrade(cfg, "head:base", sql=True)

    downgrade_sql = downgrade_buf.getvalue()
    assert "DROP TABLE" in downgrade_sql
    assert "audit_logs" in downgrade_sql
    assert "users" in downgrade_sql


# =============================================================================
# Localhost DNS Resolution & Loopback Canonicalization Regression Tests
# =============================================================================


def test_localhost_canonicalized_to_literal_loopback_ip() -> None:
    """Verify approved localhost database targets are canonicalized to literal 127.0.0.1."""
    test_db = "dormiostay_t03_test_canon123"
    localhost_url = f"postgresql://app@localhost:5433/{test_db}"

    # validate_test_url canonicalizes host
    validated_url = validate_test_url(localhost_url, test_db)
    assert validated_url.host == "127.0.0.1"
    assert validated_url.port == 5433
    assert validated_url.database == test_db

    # resolve_migration_target produces canonical target
    resolved_target, approved = resolve_migration_target(localhost_url, test_db)
    assert resolved_target.host == "127.0.0.1"
    assert resolved_target.port == 5433
    assert approved == test_db


def test_approved_localhost_with_mocked_dns_uses_literal_loopback_no_remote_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Astra bypass reproduction: DNS mapping localhost to remote IP does not redirect."""
    test_db = "dormiostay_t03_test_mockdns"
    localhost_url = f"postgresql://app@localhost:5433/{test_db}"
    remote_ip = "203.0.113.17"

    # Mock DNS resolver: maps localhost to remote IP
    orig_getaddrinfo = socket.getaddrinfo

    def mocked_getaddrinfo(
        host: bytes | str | None,
        port: bytes | str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[object]:
        if host == "localhost":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (remote_ip, int(str(port or 0))))]
        return list(orig_getaddrinfo(host, port, family, type, proto, flags))

    monkeypatch.setattr(socket, "getaddrinfo", mocked_getaddrinfo)

    # Resolve target: must canonicalize to literal 127.0.0.1
    target, approved = resolve_migration_target(localhost_url, test_db)
    assert target.host == "127.0.0.1"

    # Intercept socket connection creation
    connection_attempts: list[tuple[str | None, int | None]] = []

    async def mock_create_connection(
        self: asyncio.BaseEventLoop,
        protocol_factory: object,
        host: str | None = None,
        port: int | None = None,
        *args: object,
        **kwargs: object,
    ) -> tuple[asyncio.Transport, asyncio.Protocol]:
        connection_attempts.append((host, port))
        raise ConnectionRefusedError(f"Mock intercepted connection to {host}:{port}")

    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", mock_create_connection)

    # Attempt connection with canonical target
    engine = create_async_engine(target, poolclass=pool.NullPool)

    async def try_connect() -> None:
        async with engine.connect():
            pass

    with pytest.raises(ConnectionRefusedError):
        asyncio.run(try_connect())

    # Assert connection was made ONLY to literal loopback 127.0.0.1
    assert connection_attempts == [("127.0.0.1", 5433)]
    assert (remote_ip, 5433) not in connection_attempts
    assert ("localhost", 5433) not in connection_attempts


def test_remote_ip_targets_rejected() -> None:
    """Verify explicit remote IP targets are rejected unconditionally."""
    test_db = "dormiostay_t03_test_remoteip"
    remote_ip_url = f"postgresql://app@203.0.113.17:5433/{test_db}"

    with pytest.raises(
        ValueError, match="Remote database migrations are strictly disabled until independently"
    ):
        resolve_migration_target(remote_ip_url, test_db)

    with pytest.raises(
        ValueError, match="Test target must be the approved local disposable database"
    ):
        validate_test_url(remote_ip_url, test_db)


def test_ipv4_and_ipv6_loopback_supported() -> None:
    """Verify both 127.0.0.1 and ::1 loopback addresses are supported."""
    test_db = "dormiostay_t03_test_dualstack"

    # IPv4 loopback
    v4_url = validate_test_url(f"postgresql://app@127.0.0.1:5433/{test_db}", test_db)
    assert v4_url.host == "127.0.0.1"

    # IPv6 loopback
    v6_url = validate_test_url(f"postgresql://app@[::1]:5433/{test_db}", test_db)
    assert v6_url.host == "::1"

    # resolve_migration_target handles both
    t_v4, _ = resolve_migration_target(f"postgresql://app@127.0.0.1:5433/{test_db}", test_db)
    assert t_v4.host == "127.0.0.1"

    t_v6, _ = resolve_migration_target(f"postgresql://app@[::1]:5433/{test_db}", test_db)
    assert t_v6.host == "::1"


def test_host_and_connection_override_parameters_rejected() -> None:
    """Verify connection parameters and overrides cannot redirect the target."""
    test_db = "dormiostay_t03_test_overrides"

    # Multi-host comma list
    with pytest.raises(
        ValueError, match="Test target must be the approved local disposable database"
    ):
        validate_test_url(f"postgresql://app@127.0.0.1,203.0.113.17:5433/{test_db}", test_db)

    # Query param host override
    with pytest.raises(ValueError, match="Unsupported database connection options"):
        validate_test_url(f"postgresql://app@127.0.0.1:5433/{test_db}?host=203.0.113.17", test_db)

    # Query param port override
    with pytest.raises(ValueError, match="Unsupported database connection options"):
        validate_test_url(f"postgresql://app@127.0.0.1:5433/{test_db}?port=5432", test_db)

    # Query param service override
    with pytest.raises(ValueError, match="Unsupported database connection options"):
        validate_test_url(f"postgresql://app@127.0.0.1:5433/{test_db}?service=evil", test_db)

    # Unix domain socket path (fails closed due to missing literal host)
    with pytest.raises(ValueError, match="Database host, name and user are required"):
        validate_test_url(f"postgresql://app@/var/run/postgresql/{test_db}", test_db)


def test_safe_sslmode_option_preserved_on_canonical_target() -> None:
    """Verify safe sslmode=require option is preserved on the canonical target."""
    test_db = "dormiostay_t03_test_tls"
    target = validate_test_url(
        f"postgresql://app@localhost:5433/{test_db}?sslmode=require",
        test_db,
    )
    assert target.host == "127.0.0.1"
    assert target.query == {"ssl": "require"}


def test_cli_localhost_uses_canonical_loopback_offline_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Alembic CLI execution with localhost target generates SQL using canonical target."""
    test_db = "dormiostay_t03_test_clicanon"
    monkeypatch.setenv("DATABASE_URL_DIRECT", f"postgresql://app@localhost:5433/{test_db}")
    monkeypatch.setenv("TEST_DATABASE_NAME", test_db)

    cmd = CommandLine()

    # Offline upgrade generates valid DDL
    stdout_upgrade = io.StringIO()
    with contextlib.redirect_stdout(stdout_upgrade):
        cmd.main(argv=["-c", ALEMBIC_INI_PATH, "upgrade", "head", "--sql"])
    upgrade_out = stdout_upgrade.getvalue()
    assert "CREATE TABLE" in upgrade_out
    assert "users" in upgrade_out

    # Offline downgrade generates valid DDL
    stdout_downgrade = io.StringIO()
    with contextlib.redirect_stdout(stdout_downgrade):
        cmd.main(argv=["-c", ALEMBIC_INI_PATH, "downgrade", "head:base", "--sql"])
    downgrade_out = stdout_downgrade.getvalue()
    assert "DROP TABLE" in downgrade_out
    assert "audit_logs" in downgrade_out
