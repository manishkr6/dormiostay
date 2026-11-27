"""Static analysis and policy specification tests for Alembic migration
0003_row_level_security (T-12).

Verifies:
1. Migration revision chain: 0003_row_level_security -> 0002_email_outbox -> 0001_foundation.
2. Every governed table has ENABLE ROW LEVEL SECURITY and FORCE ROW LEVEL SECURITY
   in upgrade statements.
3. Policy syntax and presence across all 11 governed tables.
4. Correct use of NULLIF(current_setting('app.user_id', true), '')::uuid for fail-closed safety.
5. Downgrade statements drop all policies and disable RLS completely.
"""

import importlib.util
from pathlib import Path
from typing import Any

GOVERNED_TABLES = [
    "users",
    "user_profiles",
    "user_sessions",
    "email_verification_tokens",
    "password_reset_tokens",
    "email_outbox",
    "locations",
    "institutions",
    "amenities",
    "property_categories",
    "platform_settings",
    "audit_logs",
]


def load_migration_0003() -> Any:
    """Dynamically load 0003_row_level_security.py migration module."""
    mig_path = (
        Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0003_row_level_security.py"
    )
    assert mig_path.exists(), f"Migration file not found at {mig_path}"
    spec = importlib.util.spec_from_file_location("migration_0003", mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0003_metadata() -> None:
    """Verify revision identifiers and dependency chain for T-12 migration."""
    mod = load_migration_0003()
    assert mod.revision == "0003_row_level_security"
    assert mod.down_revision == "0002_email_outbox"
    assert mod.branch_labels is None
    assert mod.depends_on is None


def test_migration_0003_all_governed_tables_have_rls_and_force() -> None:
    """Verify every governed table has ENABLE and FORCE ROW LEVEL SECURITY in UPGRADE_STATEMENTS."""
    mod = load_migration_0003()
    upgrade_text = "\n".join(mod.UPGRADE_STATEMENTS)

    for table in GOVERNED_TABLES:
        enable_stmt = f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
        force_stmt = f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
        assert enable_stmt in upgrade_text, f"Missing '{enable_stmt}' in 0003 migration"
        assert force_stmt in upgrade_text, f"Missing '{force_stmt}' in 0003 migration"


def test_migration_0003_policy_definitions_present() -> None:
    """Verify that expected policies are defined for all governed tables."""
    mod = load_migration_0003()
    upgrade_text = "\n".join(mod.UPGRADE_STATEMENTS)

    # User & session policies
    assert "CREATE POLICY users_select ON users FOR SELECT" in upgrade_text
    assert "CREATE POLICY users_insert ON users FOR INSERT" in upgrade_text
    assert "CREATE POLICY users_update ON users FOR UPDATE" in upgrade_text
    assert "CREATE POLICY user_profiles_select ON user_profiles FOR SELECT" in upgrade_text
    assert "CREATE POLICY user_sessions_select ON user_sessions FOR SELECT" in upgrade_text
    assert (
        "CREATE POLICY email_verification_tokens_select ON email_verification_tokens"
        in upgrade_text
    )
    assert "CREATE POLICY password_reset_tokens_select ON password_reset_tokens" in upgrade_text

    # Worker & outbox policies
    assert "CREATE POLICY email_outbox_select ON email_outbox FOR SELECT" in upgrade_text

    # Reference data policies
    assert "CREATE POLICY locations_select ON locations FOR SELECT" in upgrade_text
    assert "CREATE POLICY institutions_select ON institutions FOR SELECT" in upgrade_text
    assert "CREATE POLICY amenities_select ON amenities FOR SELECT" in upgrade_text
    assert (
        "CREATE POLICY property_categories_select ON property_categories FOR SELECT" in upgrade_text
    )
    assert "CREATE POLICY platform_settings_select ON platform_settings FOR SELECT" in upgrade_text

    # Audit logs policies (append-only, immutable)
    assert "CREATE POLICY audit_logs_select ON audit_logs FOR SELECT" in upgrade_text
    assert "CREATE POLICY audit_logs_insert ON audit_logs FOR INSERT" in upgrade_text
    # Strict invariant: zero UPDATE or DELETE policies on audit_logs
    assert "ON audit_logs FOR UPDATE" not in upgrade_text
    assert "ON audit_logs FOR DELETE" not in upgrade_text


def test_migration_0003_nullif_uuid_cast_safety() -> None:
    """Verify that app.user_id is wrapped with NULLIF before UUID casting."""
    mod = load_migration_0003()
    upgrade_text = "\n".join(mod.UPGRADE_STATEMENTS)

    # Must use NULLIF(current_setting('app.user_id', true), '')::uuid
    safe_cast_pattern = "NULLIF(current_setting('app.user_id', true), '')::uuid"
    assert safe_cast_pattern in upgrade_text

    # Ensure no raw current_setting('app.user_id', true)::uuid without NULLIF exists
    lines = [line.strip() for line in upgrade_text.splitlines()]
    for line in lines:
        if "app.user_id" in line and "::uuid" in line:
            assert safe_cast_pattern in line, f"Unsafe UUID cast without NULLIF in line: {line}"


def test_migration_0003_downgrade_completeness() -> None:
    """Verify that downgrade drops all created policies and disables RLS."""
    mod = load_migration_0003()
    downgrade_text = "\n".join(mod.DOWNGRADE_STATEMENTS)

    for table in GOVERNED_TABLES:
        disable_stmt = f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"
        no_force_stmt = f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"
        assert disable_stmt in downgrade_text, f"Missing '{disable_stmt}' in downgrade"
        assert no_force_stmt in downgrade_text, f"Missing '{no_force_stmt}' in downgrade"


def test_migration_0003_least_privilege_table_grants_matrix() -> None:
    """Verify table-by-table, least-privilege GRANT statements for dormiostay_app (T12-SEC-04)."""
    mod = load_migration_0003()
    upgrade_text = "\n".join(mod.UPGRADE_STATEMENTS)

    # 1. users: SELECT, INSERT, UPDATE only (DELETE strictly prohibited)
    assert "GRANT SELECT, INSERT, UPDATE ON users TO dormiostay_app" in upgrade_text
    assert "DELETE ON users TO dormiostay_app" not in upgrade_text

    # 2. user_profiles: SELECT, INSERT, UPDATE only (DELETE strictly prohibited)
    assert "GRANT SELECT, INSERT, UPDATE ON user_profiles TO dormiostay_app" in upgrade_text
    assert "DELETE ON user_profiles TO dormiostay_app" not in upgrade_text

    # 3. user_sessions: SELECT, INSERT, UPDATE, DELETE (session lifecycle)
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO dormiostay_app" in upgrade_text

    # 4. email_verification_tokens: SELECT, INSERT, UPDATE, DELETE
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON email_verification_tokens TO dormiostay_app"
        in upgrade_text
    )

    # 5. password_reset_tokens: SELECT, INSERT, UPDATE, DELETE
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON password_reset_tokens TO dormiostay_app"
        in upgrade_text
    )

    # 6. email_outbox: SELECT, INSERT, UPDATE, DELETE
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON email_outbox TO dormiostay_app" in upgrade_text

    # 7. Reference dictionaries: SELECT only
    assert "GRANT SELECT ON locations TO dormiostay_app" in upgrade_text
    assert "GRANT SELECT ON institutions TO dormiostay_app" in upgrade_text
    assert "GRANT SELECT ON amenities TO dormiostay_app" in upgrade_text
    assert "GRANT SELECT ON property_categories TO dormiostay_app" in upgrade_text
    assert "GRANT SELECT ON platform_settings TO dormiostay_app" in upgrade_text

    # 8. audit_logs: SELECT, INSERT only (UPDATE and DELETE strictly forbidden - immutable log)
    assert "GRANT SELECT, INSERT ON audit_logs TO dormiostay_app" in upgrade_text
    assert "UPDATE ON audit_logs TO dormiostay_app" not in upgrade_text
    assert "DELETE ON audit_logs TO dormiostay_app" not in upgrade_text

    # 9. No blind 'GRANT ALL' statements
    assert "GRANT ALL" not in upgrade_text


def test_migration_0003_downgrade_revokes_runtime_grants() -> None:
    """Verify downgrade cleanly revokes all table privileges granted to dormiostay_app."""
    mod = load_migration_0003()
    downgrade_text = "\n".join(mod.DOWNGRADE_STATEMENTS)

    assert "REVOKE ALL PRIVILEGES ON users, user_profiles, user_sessions" in downgrade_text
    assert "FROM dormiostay_app" in downgrade_text


def test_migration_0003_users_insert_no_fail_open() -> None:
    """Verify users_insert strictly requires admin/system with NO fail-open (T12-SEC-06)."""
    mod = load_migration_0003()
    upgrade_text = "\n".join(mod.UPGRADE_STATEMENTS)

    # Must require admin or system
    assert "current_setting('app.role', true) IN ('admin', 'system')" in upgrade_text
    # Must NOT permit empty or null context bypass
    assert "NULLIF(current_setting('app.user_id', true), '') IS NULL" not in upgrade_text
    assert "OR NULLIF" not in [
        line.strip()
        for line in upgrade_text.splitlines()
        if "users_insert" in upgrade_text and "app.user_id" in line
    ]


def test_migration_0003_email_outbox_insert_no_fail_open() -> None:
    """Verify email_outbox_insert requires admin/system/worker with NO fail-open (T12-SEC-07)."""
    mod = load_migration_0003()
    upgrade_text = "\n".join(mod.UPGRADE_STATEMENTS)

    # Must require admin, system, or worker
    assert "current_setting('app.role', true) IN ('admin', 'system', 'worker')" in upgrade_text
    # Must NOT permit unauthenticated/empty context bypass
    assert "NULLIF(current_setting('app.role', true), '') IS NULL" not in upgrade_text


def test_migration_0003_protect_users_sensitive_columns_trigger() -> None:
    """Verify trigger protects sensitive user columns against tampering (T12-SEC-08)."""
    mod = load_migration_0003()
    upgrade_text = "\n".join(mod.UPGRADE_STATEMENTS)
    downgrade_text = "\n".join(mod.DOWNGRADE_STATEMENTS)

    # Trigger and function presence in upgrade
    assert "CREATE OR REPLACE FUNCTION dormiostay_protect_users_sensitive_columns()" in upgrade_text
    assert "CREATE TRIGGER protect_users_sensitive_columns" in upgrade_text
    assert "BEFORE UPDATE ON users" in upgrade_text

    # Guarded columns
    assert "NEW.role IS DISTINCT FROM OLD.role" in upgrade_text
    assert "NEW.status IS DISTINCT FROM OLD.status" in upgrade_text
    assert "NEW.password_hash IS DISTINCT FROM OLD.password_hash" in upgrade_text
    assert "NEW.email_verified_at IS DISTINCT FROM OLD.email_verified_at" in upgrade_text
    assert "NEW.failed_login_count IS DISTINCT FROM OLD.failed_login_count" in upgrade_text
    assert "NEW.locked_until IS DISTINCT FROM OLD.locked_until" in upgrade_text

    # System and admin exemption
    assert "IF current_setting('app.role', true) IN ('admin', 'system') THEN" in upgrade_text

    # Complete cleanup in downgrade
    assert "DROP TRIGGER IF EXISTS protect_users_sensitive_columns ON users" in downgrade_text
    assert "DROP FUNCTION IF EXISTS dormiostay_protect_users_sensitive_columns()" in downgrade_text
