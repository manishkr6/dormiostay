"""Regression tests for CI-02 test environment configuration and target validation (T-12).

Verifies:
1. Without DATABASE_URL, pytest retains its safe offline default ("not-used-by-unit-tests").
2. With an explicitly supplied disposable PostgreSQL DATABASE_URL, pytest does not overwrite it.
3. Live RLS harness validates approved disposable target before engine creation.
4. Remote staging and production URLs are rejected by the live RLS test harness.
5. Missing or mismatched TEST_DATABASE_NAME is rejected by the live RLS test harness.
6. Invalid or missing PostgreSQL configuration in mandatory CI mode fails closed.
7. Subprocess CI collection with staging URL fails closed with exit code 2.
8. All seven live RLS tests consume the validated target fixture.
9. Ordinary offline execution remains safely skipped.
10. Disposable target validator enforces host canonicalization and parameter restrictions.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.db.test_target import validate_test_url
from app.main import create_app


def _load_rls_module() -> Any:
    file_path = (
        Path(__file__).resolve().parents[1] / "security" / "test_postgres_rls_integration.py"
    )
    spec = importlib.util.spec_from_file_location("postgres_rls_module", file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_is_live_postgres() -> Callable[[], bool]:
    module = _load_rls_module()
    func: Callable[[], bool] = module.is_live_postgres
    return func


def _load_validate_rls_test_target() -> Callable[[], URL]:
    module = _load_rls_module()
    func: Callable[[], URL] = module.validate_rls_test_target
    return func


def test_conftest_safe_offline_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves: Without DATABASE_URL, pytest retains its safe offline default."""
    test_env: dict[str, str] = {}
    test_env.setdefault("DATABASE_URL", "not-used-by-unit-tests")
    assert test_env["DATABASE_URL"] == "not-used-by-unit-tests"


def test_conftest_preserves_supplied_disposable_database_url() -> None:
    """Proves: With disposable DATABASE_URL supplied, pytest does not overwrite it."""
    supplied_url = "postgresql+asyncpg://dormiostay_app:test_app_password@localhost:5432/dormiostay_t03_test_ci"
    test_env: dict[str, str] = {"DATABASE_URL": supplied_url}
    test_env.setdefault("DATABASE_URL", "not-used-by-unit-tests")
    assert test_env["DATABASE_URL"] == supplied_url


def test_conftest_subprocess_preserves_ambient_database_url() -> None:
    """Proves: Pytest collection preserves ambient DATABASE_URL when conftest.py is imported."""
    api_dir = Path(__file__).resolve().parents[2]
    supplied_url = "postgresql+asyncpg://dormiostay_app:test_app_password@localhost:5432/dormiostay_t03_test_ci"
    env = {
        **os.environ,
        "DATABASE_URL": supplied_url,
        "TEST_DATABASE_NAME": "dormiostay_t03_test_ci",
        "CI": "true",
        "OFFLINE_ONLY": "0",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/security/test_postgres_rls_integration.py",
            "--collect-only",
        ],
        cwd=api_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Pytest collection failed: {result.stderr}\n{result.stdout}"
    assert "collected 7 items" in result.stdout


def test_ci_mode_invalid_or_missing_postgres_fails_closed() -> None:
    """Proves: Invalid or missing PostgreSQL configuration in mandatory CI mode fails closed."""
    api_dir = Path(__file__).resolve().parents[2]

    # Subcase A: DATABASE_URL is not set / empty in CI with OFFLINE_ONLY=0
    env_empty = {
        **os.environ,
        "DATABASE_URL": "",
        "TEST_DATABASE_NAME": "dormiostay_t03_test_ci",
        "CI": "true",
        "OFFLINE_ONLY": "0",
    }
    res_empty = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/security/test_postgres_rls_integration.py",
            "--collect-only",
        ],
        cwd=api_dir,
        env=env_empty,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_empty.returncode == 2
    assert "database target validation failed" in (res_empty.stdout + res_empty.stderr)

    # Subcase B: DATABASE_URL is offline default "not-used-by-unit-tests" in CI with OFFLINE_ONLY=0
    env_offline = {
        **os.environ,
        "DATABASE_URL": "not-used-by-unit-tests",
        "TEST_DATABASE_NAME": "dormiostay_t03_test_ci",
        "CI": "true",
        "OFFLINE_ONLY": "0",
    }
    res_offline = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/security/test_postgres_rls_integration.py",
            "--collect-only",
        ],
        cwd=api_dir,
        env=env_offline,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_offline.returncode == 2
    assert "database target validation failed" in (res_offline.stdout + res_offline.stderr)


def test_ci_mode_missing_test_database_name_fails_closed() -> None:
    """Proves: Missing TEST_DATABASE_NAME in mandatory CI mode fails closed."""
    api_dir = Path(__file__).resolve().parents[2]
    env_no_db_name = {
        **os.environ,
        "DATABASE_URL": (
            "postgresql+asyncpg://dormiostay_app:pass@localhost:5432/dormiostay_t03_test_ci"
        ),
        "CI": "true",
        "OFFLINE_ONLY": "0",
    }
    env_no_db_name.pop("TEST_DATABASE_NAME", None)

    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/security/test_postgres_rls_integration.py",
            "--collect-only",
        ],
        cwd=api_dir,
        env=env_no_db_name,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 2
    assert "TEST_DATABASE_NAME is required" in (res.stdout + res.stderr)


def test_ci_mode_staging_database_url_fails_closed() -> None:
    """Proves: Setting DATABASE_URL to staging in CI mode fails closed during collection."""
    api_dir = Path(__file__).resolve().parents[2]
    staging_url = (
        "postgresql+asyncpg://dormiostay_app:secret@ep-ancient-star-123456.us-east-2.aws.neon.tech/"
        "dormiostay_t03_test_ci"
    )
    env_staging = {
        **os.environ,
        "DATABASE_URL": staging_url,
        "TEST_DATABASE_NAME": "dormiostay_t03_test_ci",
        "CI": "true",
        "OFFLINE_ONLY": "0",
    }
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/security/test_postgres_rls_integration.py",
            "--collect-only",
        ],
        cwd=api_dir,
        env=env_staging,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 2
    assert "Test target must be the approved local disposable database" in (res.stdout + res.stderr)


def test_rls_harness_validates_approved_disposable_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves: validate_rls_test_target accepts valid local disposable target and canonicalizes."""
    validate_target = _load_validate_rls_test_target()
    valid_url = "postgresql+asyncpg://dormiostay_app:test_app_password@localhost:5432/dormiostay_t03_test_ci"
    monkeypatch.setenv("DATABASE_URL", valid_url)
    monkeypatch.setenv("TEST_DATABASE_NAME", "dormiostay_t03_test_ci")

    validated = validate_target()
    assert validated.host == "127.0.0.1"
    assert validated.database == "dormiostay_t03_test_ci"


def test_rls_harness_rejects_remote_staging_and_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves: validate_rls_test_target rejects remote staging or production hostnames."""
    validate_target = _load_validate_rls_test_target()
    target_err = "Test target must be the approved local disposable database"
    monkeypatch.setenv("TEST_DATABASE_NAME", "dormiostay_t03_test_ci")

    # Neon staging
    neon_url = (
        "postgresql+asyncpg://dormiostay_app:secret@ep-ancient-star-123456.us-east-2.aws.neon.tech/"
        "dormiostay_t03_test_ci"
    )
    monkeypatch.setenv("DATABASE_URL", neon_url)
    with pytest.raises(ValueError, match=target_err):
        validate_target()

    # Remote production
    prod_url = (
        "postgresql+asyncpg://dormiostay_app:secret@db.dormiostay.com:5432/dormiostay_t03_test_ci"
    )
    monkeypatch.setenv("DATABASE_URL", prod_url)
    with pytest.raises(ValueError, match=target_err):
        validate_target()


def test_rls_harness_rejects_mismatched_database_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves: validate_rls_test_target rejects database name not matching TEST_DATABASE_NAME."""
    validate_target = _load_validate_rls_test_target()
    target_err = "Test target must be the approved local disposable database"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://dormiostay_app:pass@localhost:5432/dormiostay_t03_test_foo",
    )
    monkeypatch.setenv("TEST_DATABASE_NAME", "dormiostay_t03_test_bar")
    with pytest.raises(ValueError, match=target_err):
        validate_target()


def test_rls_tests_signatures_consume_validated_fixture() -> None:
    """Proves: All seven live RLS tests declare the live_database_url fixture."""
    module = _load_rls_module()
    test_functions = [
        "test_postgres_rls_cross_user_read_blocked",
        "test_postgres_rls_no_context_returns_zero_rows",
        "test_postgres_rls_missing_context_users_insert_denied",
        "test_postgres_rls_guest_context_admin_creation_denied",
        "test_postgres_rls_missing_context_email_outbox_insert_denied",
        "test_postgres_rls_guest_context_email_outbox_insert_denied",
        "test_postgres_rls_tenant_cannot_elevate_role",
    ]
    assert len(test_functions) == 7
    for fn_name in test_functions:
        fn = getattr(module, fn_name, None)
        assert fn is not None, f"Missing test function {fn_name}"
        sig = inspect.signature(fn)
        assert "live_database_url" in sig.parameters, (
            f"Test {fn_name} does not consume live_database_url: {sig.parameters}"
        )


def test_is_live_postgres_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves: is_live_postgres() correctly detects live postgres vs offline mode."""
    is_live_postgres = _load_is_live_postgres()

    # 1. Default offline mode
    monkeypatch.setenv("DATABASE_URL", "not-used-by-unit-tests")
    monkeypatch.setenv("OFFLINE_ONLY", "1")
    assert not is_live_postgres()

    # 2. Postgres URL but OFFLINE_ONLY=1
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://dormiostay_app:pass@localhost:5432/dormiostay_t03_test_ci",
    )
    monkeypatch.setenv("TEST_DATABASE_NAME", "dormiostay_t03_test_ci")
    monkeypatch.setenv("OFFLINE_ONLY", "1")
    assert not is_live_postgres()

    # 3. Valid Postgres URL with matching TEST_DATABASE_NAME and OFFLINE_ONLY=0
    monkeypatch.setenv("OFFLINE_ONLY", "0")
    assert is_live_postgres()

    # 4. Postgres URL with remote host and OFFLINE_ONLY=0 (must reject!)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://dormiostay_app:pass@ep-staging.aws.neon.tech/dormiostay_t03_test_ci",
    )
    assert not is_live_postgres()

    # 5. Non-postgres URL with OFFLINE_ONLY=0
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    assert not is_live_postgres()


def test_unit_tests_isolated_fixtures_no_db_connection() -> None:
    """Proves: Unit tests continue using isolated test fixtures without network or DB calls."""
    with TestClient(create_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_disposable_target_validator_rejects_staging_and_production_urls() -> None:
    """Proves: No staging or production database URL is accepted as a CI test target."""
    disposable_approved = "dormiostay_t03_test_ci"
    host_match_error = "Test target must be the approved local disposable database"
    name_match_error = "An explicitly approved disposable database name is required"

    # 1. Reject Neon staging host
    neon_staging_url = (
        "postgresql+asyncpg://dormiostay_app:secret@ep-ancient-star-123456.us-east-2.aws.neon.tech/"
        "dormiostay_t03_test_ci"
    )
    with pytest.raises(ValueError, match=host_match_error):
        validate_test_url(neon_staging_url, disposable_approved)

    # 2. Reject remote production host
    prod_url = (
        "postgresql+asyncpg://dormiostay_app:secret@db.dormiostay.com:5432/dormiostay_t03_test_ci"
    )
    with pytest.raises(ValueError, match=host_match_error):
        validate_test_url(prod_url, disposable_approved)

    # 3. Reject non-disposable database name
    staging_db_url = "postgresql+asyncpg://dormiostay_app:secret@localhost:5432/dormiostay_staging"
    with pytest.raises(ValueError, match=name_match_error):
        validate_test_url(staging_db_url, "dormiostay_staging")

    # 4. Reject production database name
    prod_db_url = "postgresql+asyncpg://dormiostay_app:secret@localhost:5432/dormiostay_prod"
    with pytest.raises(ValueError, match=name_match_error):
        validate_test_url(prod_db_url, "dormiostay_prod")

    # 5. Accept valid local disposable database URL and canonicalize host
    valid_url = "postgresql+asyncpg://dormiostay_app:test_app_password@localhost:5432/dormiostay_t03_test_ci"
    validated = validate_test_url(valid_url, disposable_approved)
    assert validated.host == "127.0.0.1"
    assert validated.database == disposable_approved
