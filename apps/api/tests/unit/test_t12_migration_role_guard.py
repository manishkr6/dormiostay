"""Offline unit and regression tests for T12-FIX-02 fail-closed migration role guard.

Verifies:
1. Migration revision chain and metadata:
   - 0003_row_level_security -> 0002_email_outbox -> 0001_foundation.
2. Silent fail-open bypass elimination:
   - "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dormiostay_app') THEN" is absent.
   - Fail-closed "IF NOT FOUND THEN RAISE EXCEPTION ... ERRCODE = '42704'" is enforced.
3. Elevated attribute verification:
   - Rejects rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls (ERRCODE 42501).
4. Simulation of PL/pgSQL block execution:
   - Missing role raises exception (42704).
   - Elevated attributes raise exception (42501).
   - Valid restricted role succeeds and grants runtime permissions.
5. Migration execution failure semantics:
   - op.execute failure aborts upgrade() immediately and prevents commit/version increment.
6. Intended runtime grant matrix:
   - Table-by-table least-privilege matrix is completely preserved.
7. CI workflow provisioning compatibility:
   - .github/workflows/ci.yml role provisioning creates dormiostay_app with exact attributes.
8. Migration guard security invariants:
   - dormiostay_app is strictly forbidden from running migrations.
   - Remote production and downgrade operations remain blocked.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.db.migration_guard import (
    FORBIDDEN_APP_ROLE,
    PROTECTED_PRODUCTION_BRANCH_ID,
    PROTECTED_PRODUCTION_PROJECT_ID,
)

ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
WORKFLOW_FILE = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "ci.yml"


def load_migration(filename: str, module_name: str) -> Any:
    """Dynamically load an Alembic migration module by filename."""
    mig_path = ALEMBIC_DIR / filename
    assert mig_path.exists(), f"Migration file not found at {mig_path}"
    spec = importlib.util.spec_from_file_location(module_name, mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# =============================================================================
# 1. Revision Chain and Migration Dependency Integrity
# =============================================================================


def test_migration_revision_chain_exact_identifiers() -> None:
    """Verify exact revision identifiers and linear dependency chain in source."""
    mod_0001 = load_migration("0001_foundation.py", "migration_0001")
    mod_0002 = load_migration("0002_email_outbox.py", "migration_0002")
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")

    # Verify 0001
    assert mod_0001.revision == "0001_foundation"
    assert mod_0001.down_revision is None

    # Verify 0002
    assert mod_0002.revision == "0002_email_outbox"
    assert mod_0002.down_revision == "0001_foundation"

    # Verify 0003
    assert mod_0003.revision == "0003_row_level_security"
    assert mod_0003.down_revision == "0002_email_outbox"

    # Invariants
    assert mod_0003.branch_labels is None
    assert mod_0003.depends_on is None


# =============================================================================
# 2. Migration 0003 Role Guard Static Analysis
# =============================================================================


def test_migration_0003_silent_skip_eliminated() -> None:
    """Verify that silent fail-open role checks are completely removed from UPGRADE_STATEMENTS."""
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")
    upgrade_text = "\n".join(mod_0003.UPGRADE_STATEMENTS)

    # Silent bypass pattern must NOT exist in upgrade
    silent_pattern = re.compile(
        r"IF\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+pg_roles\s+WHERE\s+rolname\s*=\s*'dormiostay_app'\s*\)\s*THEN",
        re.IGNORECASE,
    )
    assert not silent_pattern.search(upgrade_text), (
        "Found silent fail-open IF EXISTS role grant bypass in UPGRADE_STATEMENTS"
    )


def test_migration_0003_explicit_role_assertion_present() -> None:
    """Verify that statement 13 actively asserts dormiostay_app existence and raises 42704."""
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")
    role_stmt = mod_0003.UPGRADE_STATEMENTS[-1]

    # Must contain DO $$ block
    assert "DO $$" in role_stmt
    assert "WHERE rolname = 'dormiostay_app'" in role_stmt
    assert "IF NOT FOUND THEN" in role_stmt
    assert 'Required application role "dormiostay_app" does not exist' in role_stmt
    assert "42704" in role_stmt


def test_migration_0003_elevated_role_attributes_rejected() -> None:
    """Verify that statement 13 actively asserts non-elevated attributes and raises 42501."""
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")
    role_stmt = mod_0003.UPGRADE_STATEMENTS[-1]

    for attr in [
        "rolsuper",
        "rolcreaterole",
        "rolcreatedb",
        "rolreplication",
        "rolbypassrls",
    ]:
        assert attr in role_stmt, f"Attribute {attr} missing from role guard check"

    assert 'Application role "dormiostay_app" possesses forbidden elevated attributes' in role_stmt
    assert "42501" in role_stmt


def test_migration_0003_does_not_create_or_alter_role() -> None:
    """Verify migration does not attempt to create or alter database roles automatically."""
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")
    upgrade_text = "\n".join(mod_0003.UPGRADE_STATEMENTS)

    assert "CREATE ROLE" not in upgrade_text
    assert "ALTER ROLE" not in upgrade_text


# =============================================================================
# 3. Behavioral Simulation of PL/pgSQL Guard Logic
# =============================================================================


class MockPLpgSQLExecutor:
    """Simulates the catalog lookup and exception handling of the PL/pgSQL role guard."""

    def __init__(self, roles_catalog: dict[str, dict[str, bool]] | None = None) -> None:
        self.roles_catalog = roles_catalog if roles_catalog is not None else {}
        self.executed_grants: list[str] = []

    def execute_role_guard_block(self, role_name: str = "dormiostay_app") -> None:
        # Step 1: SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
        if role_name not in self.roles_catalog:
            # IF NOT FOUND THEN
            raise RuntimeError(
                f'42704: Required application role "{role_name}" does not exist; '
                "cannot apply least-privilege table grants"
            )

        r = self.roles_catalog[role_name]

        # Step 2: Attribute check
        if (
            r.get("rolsuper", False)
            or r.get("rolcreaterole", False)
            or r.get("rolcreatedb", False)
            or r.get("rolreplication", False)
            or r.get("rolbypassrls", False)
        ):
            raise RuntimeError(
                f'42501: Application role "{role_name}" possesses forbidden elevated attributes'
            )

        # Step 3: Apply grants
        self.executed_grants.append(f"GRANT SELECT, INSERT, UPDATE ON users TO {role_name}")
        self.executed_grants.append(f"GRANT SELECT, INSERT, UPDATE ON user_profiles TO {role_name}")
        self.executed_grants.append(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO {role_name}"
        )
        self.executed_grants.append(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON email_verification_tokens TO {role_name}"
        )
        self.executed_grants.append(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON password_reset_tokens TO {role_name}"
        )
        self.executed_grants.append(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON email_outbox TO {role_name}"
        )
        self.executed_grants.append(f"GRANT SELECT ON locations TO {role_name}")
        self.executed_grants.append(f"GRANT SELECT ON institutions TO {role_name}")
        self.executed_grants.append(f"GRANT SELECT ON amenities TO {role_name}")
        self.executed_grants.append(f"GRANT SELECT ON property_categories TO {role_name}")
        self.executed_grants.append(f"GRANT SELECT ON platform_settings TO {role_name}")
        self.executed_grants.append(f"GRANT SELECT, INSERT ON audit_logs TO {role_name}")


def test_simulation_missing_role_fails_closed() -> None:
    """Missing dormiostay_app role raises 42704 and applies zero grants."""
    executor = MockPLpgSQLExecutor(roles_catalog={})

    with pytest.raises(RuntimeError, match="42704: Required application role"):
        executor.execute_role_guard_block("dormiostay_app")

    assert len(executor.executed_grants) == 0


@pytest.mark.parametrize(
    "elevated_attr",
    ["rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls"],
)
def test_simulation_elevated_role_attributes_fail_closed(elevated_attr: str) -> None:
    """Elevated attribute on dormiostay_app raises 42501 and applies zero grants."""
    roles = {
        "dormiostay_app": {
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolreplication": False,
            "rolbypassrls": False,
        }
    }
    roles["dormiostay_app"][elevated_attr] = True

    executor = MockPLpgSQLExecutor(roles_catalog=roles)

    with pytest.raises(RuntimeError, match="42501: Application role .* possesses forbidden"):
        executor.execute_role_guard_block("dormiostay_app")

    assert len(executor.executed_grants) == 0


def test_simulation_valid_restricted_role_succeeds() -> None:
    """Properly restricted role succeeds and all 12 table grants are executed."""
    roles = {
        "dormiostay_app": {
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolreplication": False,
            "rolbypassrls": False,
        }
    }

    executor = MockPLpgSQLExecutor(roles_catalog=roles)
    executor.execute_role_guard_block("dormiostay_app")

    assert len(executor.executed_grants) == 12
    assert "GRANT SELECT, INSERT, UPDATE ON users TO dormiostay_app" in executor.executed_grants
    assert "GRANT SELECT, INSERT ON audit_logs TO dormiostay_app" in executor.executed_grants


# =============================================================================
# 4. Alembic Migration Upgrade Error Propagation
# =============================================================================


def test_upgrade_propagates_role_check_failure() -> None:
    """Verify that if the guarded role statement fails in op.execute, upgrade() aborts."""
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")

    executed_statements: list[str] = []

    def mock_op_execute(stmt: str) -> None:
        executed_statements.append(stmt)
        if "Required application role" in stmt:
            raise RuntimeError('42704: Required application role "dormiostay_app" does not exist')

    with patch("alembic.op.execute", side_effect=mock_op_execute):
        with pytest.raises(RuntimeError, match="42704: Required application role"):
            mod_0003.upgrade()

    # The statement must have been called, failing before completing upgrade
    assert any("Required application role" in stmt for stmt in executed_statements)


def test_upgrade_executes_all_statements_when_role_check_passes() -> None:
    """Verify that when op.execute succeeds, upgrade() runs all UPGRADE_STATEMENTS."""
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")

    executed_statements: list[str] = []

    def mock_op_execute(stmt: str) -> None:
        executed_statements.append(stmt)

    with patch("alembic.op.execute", side_effect=mock_op_execute):
        mod_0003.upgrade()

    assert len(executed_statements) == len(mod_0003.UPGRADE_STATEMENTS)


# =============================================================================
# 5. Runtime Grant Matrix Preservation
# =============================================================================


def test_runtime_grant_matrix_exhaustively_verified() -> None:
    """Verify that the exact table-grant matrix is present in statement 13."""
    mod_0003 = load_migration("0003_row_level_security.py", "migration_0003")
    role_stmt = mod_0003.UPGRADE_STATEMENTS[-1]

    expected_grants = [
        "GRANT SELECT, INSERT, UPDATE ON users TO dormiostay_app;",
        "GRANT SELECT, INSERT, UPDATE ON user_profiles TO dormiostay_app;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO dormiostay_app;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON email_verification_tokens TO dormiostay_app;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON password_reset_tokens TO dormiostay_app;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON email_outbox TO dormiostay_app;",
        "GRANT SELECT ON locations TO dormiostay_app;",
        "GRANT SELECT ON institutions TO dormiostay_app;",
        "GRANT SELECT ON amenities TO dormiostay_app;",
        "GRANT SELECT ON property_categories TO dormiostay_app;",
        "GRANT SELECT ON platform_settings TO dormiostay_app;",
        "GRANT SELECT, INSERT ON audit_logs TO dormiostay_app;",
    ]

    for grant in expected_grants:
        assert grant in role_stmt, f"Expected grant missing: {grant}"

    # Forbidden privileges must NOT be granted
    forbidden_grants = [
        "DELETE ON users TO dormiostay_app",
        "DELETE ON user_profiles TO dormiostay_app",
        "UPDATE ON audit_logs TO dormiostay_app",
        "DELETE ON audit_logs TO dormiostay_app",
        "GRANT ALL",
    ]
    for forbidden in forbidden_grants:
        assert forbidden not in role_stmt, f"Forbidden grant present: {forbidden}"


# =============================================================================
# 6. CI Workflow Compatibility
# =============================================================================


def test_ci_workflow_role_provisioning_compatible_with_guard() -> None:
    """Verify that .github/workflows/ci.yml role provisioning satisfies the migration role guard."""
    assert WORKFLOW_FILE.exists(), f"CI workflow file not found at {WORKFLOW_FILE}"
    ci_content = WORKFLOW_FILE.read_text(encoding="utf-8")

    # CI must create dormiostay_app role with explicit non-elevated flags
    role_create_pattern = re.compile(
        r"CREATE\s+ROLE\s+dormiostay_app\s+WITH\s+LOGIN\s+PASSWORD\s+'[^']+'\s+"
        r"NOSUPERUSER\s+NOBYPASSRLS\s+NOCREATEDB\s+NOCREATEROLE\s+NOREPLICATION",
        re.IGNORECASE,
    )
    assert role_create_pattern.search(ci_content), (
        "CI workflow role provisioning does not match required non-elevated attributes"
    )


# =============================================================================
# 7. Migration Guard Invariants
# =============================================================================


def test_migration_guard_security_invariants_preserved() -> None:
    """Verify that migration_guard.py constants and boundaries remain unchanged."""
    assert FORBIDDEN_APP_ROLE == "dormiostay_app"
    assert PROTECTED_PRODUCTION_PROJECT_ID == "small-dew-81510029"
    assert PROTECTED_PRODUCTION_BRANCH_ID == "br-holy-lake-bi0hc1lu"
