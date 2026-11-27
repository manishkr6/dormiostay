"""Enable and force Row-Level Security (RLS) on all foundation tables (T-12).

Authoritative Specifications:
- Feature Ticket List.md — T-12 (Role permissions and row-level security).
- Security and Access Document — Section 4 (RLS) & Section 4.3 (Rules table by table).
- Technical Architecture Document — Section 5.

Invariants:
- ENABLE and FORCE ROW LEVEL SECURITY on all application tables.
- Restricts access using transaction-local current_setting('app.user_id', true)
  and current_setting('app.role', true).
- Uses NULLIF(current_setting('app.user_id', true), '')::uuid to ensure missing
  or empty context fails closed without UUID cast syntax errors.
- Prohibits cross-tenant and cross-user data access.
- Ensures no-context queries return zero rows for protected user data.
"""

from alembic import op

revision = "0003_row_level_security"
down_revision = "0002_email_outbox"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = [
    # -------------------------------------------------------------------------
    # 1. users
    # -------------------------------------------------------------------------
    "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE users FORCE ROW LEVEL SECURITY",
    """CREATE POLICY users_select ON users FOR SELECT
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY users_insert ON users FOR INSERT
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
    )""",
    """CREATE POLICY users_update ON users FOR UPDATE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE OR REPLACE FUNCTION dormiostay_protect_users_sensitive_columns()
    RETURNS trigger AS $$
    BEGIN
        -- System and Admin sessions can update all permitted columns
        IF current_setting('app.role', true) IN ('admin', 'system') THEN
            RETURN NEW;
        END IF;

        -- Non-privileged callers (tenant, owner, guest, or uninitialized context)
        -- are strictly forbidden from modifying sensitive account and security fields
        IF NEW.role IS DISTINCT FROM OLD.role THEN
            RAISE EXCEPTION 'Unauthorized modification of role' USING ERRCODE = '42501';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'Unauthorized modification of status' USING ERRCODE = '42501';
        END IF;
        IF NEW.password_hash IS DISTINCT FROM OLD.password_hash THEN
            RAISE EXCEPTION 'Unauthorized modification of password_hash' USING ERRCODE = '42501';
        END IF;
        IF NEW.email_verified_at IS DISTINCT FROM OLD.email_verified_at THEN
            RAISE EXCEPTION 'Unauthorized modification of email_verified_at'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.failed_login_count IS DISTINCT FROM OLD.failed_login_count THEN
            RAISE EXCEPTION 'Unauthorized modification of failed_login_count'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.locked_until IS DISTINCT FROM OLD.locked_until THEN
            RAISE EXCEPTION 'Unauthorized modification of locked_until' USING ERRCODE = '42501';
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;""",
    """CREATE TRIGGER protect_users_sensitive_columns
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION dormiostay_protect_users_sensitive_columns();""",
    # -------------------------------------------------------------------------
    # 2. user_profiles
    # -------------------------------------------------------------------------
    "ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE user_profiles FORCE ROW LEVEL SECURITY",
    """CREATE POLICY user_profiles_select ON user_profiles FOR SELECT
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY user_profiles_insert ON user_profiles FOR INSERT
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY user_profiles_update ON user_profiles FOR UPDATE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    # -------------------------------------------------------------------------
    # 3. user_sessions
    # -------------------------------------------------------------------------
    "ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE user_sessions FORCE ROW LEVEL SECURITY",
    """CREATE POLICY user_sessions_select ON user_sessions FOR SELECT
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY user_sessions_insert ON user_sessions FOR INSERT
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY user_sessions_update ON user_sessions FOR UPDATE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY user_sessions_delete ON user_sessions FOR DELETE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    # -------------------------------------------------------------------------
    # 4. email_verification_tokens
    # -------------------------------------------------------------------------
    "ALTER TABLE email_verification_tokens ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE email_verification_tokens FORCE ROW LEVEL SECURITY",
    """CREATE POLICY email_verification_tokens_select ON email_verification_tokens FOR SELECT
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY email_verification_tokens_insert ON email_verification_tokens FOR INSERT
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY email_verification_tokens_update ON email_verification_tokens FOR UPDATE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY email_verification_tokens_delete ON email_verification_tokens FOR DELETE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    # -------------------------------------------------------------------------
    # 5. password_reset_tokens
    # -------------------------------------------------------------------------
    "ALTER TABLE password_reset_tokens ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE password_reset_tokens FORCE ROW LEVEL SECURITY",
    """CREATE POLICY password_reset_tokens_select ON password_reset_tokens FOR SELECT
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY password_reset_tokens_insert ON password_reset_tokens FOR INSERT
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY password_reset_tokens_update ON password_reset_tokens FOR UPDATE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    """CREATE POLICY password_reset_tokens_delete ON password_reset_tokens FOR DELETE
    USING (
        current_setting('app.role', true) IN ('admin', 'system')
        OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    )""",
    # -------------------------------------------------------------------------
    # 6. email_outbox
    # -------------------------------------------------------------------------
    "ALTER TABLE email_outbox ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE email_outbox FORCE ROW LEVEL SECURITY",
    """CREATE POLICY email_outbox_select ON email_outbox FOR SELECT
    USING (current_setting('app.role', true) IN ('admin', 'system', 'worker'))""",
    """CREATE POLICY email_outbox_insert ON email_outbox FOR INSERT
    WITH CHECK (
        current_setting('app.role', true) IN ('admin', 'system', 'worker')
    )""",
    """CREATE POLICY email_outbox_update ON email_outbox FOR UPDATE
    USING (current_setting('app.role', true) IN ('admin', 'system', 'worker'))
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system', 'worker'))""",
    """CREATE POLICY email_outbox_delete ON email_outbox FOR DELETE
    USING (current_setting('app.role', true) IN ('admin', 'system', 'worker'))""",
    # -------------------------------------------------------------------------
    # 7. locations
    # -------------------------------------------------------------------------
    "ALTER TABLE locations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE locations FORCE ROW LEVEL SECURITY",
    """CREATE POLICY locations_select ON locations FOR SELECT
    USING (is_active = true OR current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY locations_insert ON locations FOR INSERT
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY locations_update ON locations FOR UPDATE
    USING (current_setting('app.role', true) IN ('admin', 'system'))
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY locations_delete ON locations FOR DELETE
    USING (current_setting('app.role', true) IN ('admin', 'system'))""",
    # -------------------------------------------------------------------------
    # 8. institutions
    # -------------------------------------------------------------------------
    "ALTER TABLE institutions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE institutions FORCE ROW LEVEL SECURITY",
    """CREATE POLICY institutions_select ON institutions FOR SELECT
    USING (is_active = true OR current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY institutions_insert ON institutions FOR INSERT
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY institutions_update ON institutions FOR UPDATE
    USING (current_setting('app.role', true) IN ('admin', 'system'))
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY institutions_delete ON institutions FOR DELETE
    USING (current_setting('app.role', true) IN ('admin', 'system'))""",
    # -------------------------------------------------------------------------
    # 9. amenities
    # -------------------------------------------------------------------------
    "ALTER TABLE amenities ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE amenities FORCE ROW LEVEL SECURITY",
    """CREATE POLICY amenities_select ON amenities FOR SELECT
    USING (is_active = true OR current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY amenities_insert ON amenities FOR INSERT
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY amenities_update ON amenities FOR UPDATE
    USING (current_setting('app.role', true) IN ('admin', 'system'))
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY amenities_delete ON amenities FOR DELETE
    USING (current_setting('app.role', true) IN ('admin', 'system'))""",
    # -------------------------------------------------------------------------
    # 10. property_categories
    # -------------------------------------------------------------------------
    "ALTER TABLE property_categories ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE property_categories FORCE ROW LEVEL SECURITY",
    """CREATE POLICY property_categories_select ON property_categories FOR SELECT
    USING (is_active = true OR current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY property_categories_insert ON property_categories FOR INSERT
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY property_categories_update ON property_categories FOR UPDATE
    USING (current_setting('app.role', true) IN ('admin', 'system'))
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY property_categories_delete ON property_categories FOR DELETE
    USING (current_setting('app.role', true) IN ('admin', 'system'))""",
    # -------------------------------------------------------------------------
    # 11. platform_settings
    # -------------------------------------------------------------------------
    "ALTER TABLE platform_settings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE platform_settings FORCE ROW LEVEL SECURITY",
    """CREATE POLICY platform_settings_select ON platform_settings FOR SELECT
    USING (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY platform_settings_insert ON platform_settings FOR INSERT
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    """CREATE POLICY platform_settings_update ON platform_settings FOR UPDATE
    USING (current_setting('app.role', true) IN ('admin', 'system'))
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    # -------------------------------------------------------------------------
    # 12. audit_logs (append-only, immutable security audit trail)
    # -------------------------------------------------------------------------
    "ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY",
    """CREATE POLICY audit_logs_select ON audit_logs FOR SELECT
    USING (current_setting('app.role', true) = 'admin')""",
    """CREATE POLICY audit_logs_insert ON audit_logs FOR INSERT
    WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))""",
    # -------------------------------------------------------------------------
    # 13. Least-privilege runtime grants for dormiostay_app (T-12 / T-05)
    # -------------------------------------------------------------------------
    """DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dormiostay_app') THEN
        -- users: SELECT, INSERT, UPDATE (DELETE denied: soft status only)
        GRANT SELECT, INSERT, UPDATE ON users TO dormiostay_app;

        -- user_profiles: SELECT, INSERT, UPDATE (DELETE denied)
        GRANT SELECT, INSERT, UPDATE ON user_profiles TO dormiostay_app;

        -- user_sessions: SELECT, INSERT, UPDATE, DELETE (session lifecycle & cleanup)
        GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO dormiostay_app;

        -- email_verification_tokens: SELECT, INSERT, UPDATE, DELETE
        GRANT SELECT, INSERT, UPDATE, DELETE ON email_verification_tokens TO dormiostay_app;

        -- password_reset_tokens: SELECT, INSERT, UPDATE, DELETE
        GRANT SELECT, INSERT, UPDATE, DELETE ON password_reset_tokens TO dormiostay_app;

        -- email_outbox: SELECT, INSERT, UPDATE, DELETE
        GRANT SELECT, INSERT, UPDATE, DELETE ON email_outbox TO dormiostay_app;

        -- Public dictionary & catalog tables: SELECT only (read-only for application)
        GRANT SELECT ON locations TO dormiostay_app;
        GRANT SELECT ON institutions TO dormiostay_app;
        GRANT SELECT ON amenities TO dormiostay_app;
        GRANT SELECT ON property_categories TO dormiostay_app;
        GRANT SELECT ON platform_settings TO dormiostay_app;

        -- audit_logs: SELECT, INSERT only (UPDATE and DELETE strictly forbidden - immutable log)
        GRANT SELECT, INSERT ON audit_logs TO dormiostay_app;
    END IF;
END
$$;""",
]

DOWNGRADE_STATEMENTS = [
    # 1. users
    "DROP TRIGGER IF EXISTS protect_users_sensitive_columns ON users",
    "DROP FUNCTION IF EXISTS dormiostay_protect_users_sensitive_columns()",
    "DROP POLICY IF EXISTS users_select ON users",
    "DROP POLICY IF EXISTS users_insert ON users",
    "DROP POLICY IF EXISTS users_update ON users",
    "ALTER TABLE users NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE users DISABLE ROW LEVEL SECURITY",
    # 2. user_profiles
    "DROP POLICY IF EXISTS user_profiles_select ON user_profiles",
    "DROP POLICY IF EXISTS user_profiles_insert ON user_profiles",
    "DROP POLICY IF EXISTS user_profiles_update ON user_profiles",
    "ALTER TABLE user_profiles NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE user_profiles DISABLE ROW LEVEL SECURITY",
    # 3. user_sessions
    "DROP POLICY IF EXISTS user_sessions_select ON user_sessions",
    "DROP POLICY IF EXISTS user_sessions_insert ON user_sessions",
    "DROP POLICY IF EXISTS user_sessions_update ON user_sessions",
    "DROP POLICY IF EXISTS user_sessions_delete ON user_sessions",
    "ALTER TABLE user_sessions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE user_sessions DISABLE ROW LEVEL SECURITY",
    # 4. email_verification_tokens
    "DROP POLICY IF EXISTS email_verification_tokens_select ON email_verification_tokens",
    "DROP POLICY IF EXISTS email_verification_tokens_insert ON email_verification_tokens",
    "DROP POLICY IF EXISTS email_verification_tokens_update ON email_verification_tokens",
    "DROP POLICY IF EXISTS email_verification_tokens_delete ON email_verification_tokens",
    "ALTER TABLE email_verification_tokens NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE email_verification_tokens DISABLE ROW LEVEL SECURITY",
    # 5. password_reset_tokens
    "DROP POLICY IF EXISTS password_reset_tokens_select ON password_reset_tokens",
    "DROP POLICY IF EXISTS password_reset_tokens_insert ON password_reset_tokens",
    "DROP POLICY IF EXISTS password_reset_tokens_update ON password_reset_tokens",
    "DROP POLICY IF EXISTS password_reset_tokens_delete ON password_reset_tokens",
    "ALTER TABLE password_reset_tokens NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE password_reset_tokens DISABLE ROW LEVEL SECURITY",
    # 6. email_outbox
    "DROP POLICY IF EXISTS email_outbox_select ON email_outbox",
    "DROP POLICY IF EXISTS email_outbox_insert ON email_outbox",
    "DROP POLICY IF EXISTS email_outbox_update ON email_outbox",
    "DROP POLICY IF EXISTS email_outbox_delete ON email_outbox",
    "ALTER TABLE email_outbox NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE email_outbox DISABLE ROW LEVEL SECURITY",
    # 7. locations
    "DROP POLICY IF EXISTS locations_select ON locations",
    "DROP POLICY IF EXISTS locations_insert ON locations",
    "DROP POLICY IF EXISTS locations_update ON locations",
    "DROP POLICY IF EXISTS locations_delete ON locations",
    "ALTER TABLE locations NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE locations DISABLE ROW LEVEL SECURITY",
    # 8. institutions
    "DROP POLICY IF EXISTS institutions_select ON institutions",
    "DROP POLICY IF EXISTS institutions_insert ON institutions",
    "DROP POLICY IF EXISTS institutions_update ON institutions",
    "DROP POLICY IF EXISTS institutions_delete ON institutions",
    "ALTER TABLE institutions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE institutions DISABLE ROW LEVEL SECURITY",
    # 9. amenities
    "DROP POLICY IF EXISTS amenities_select ON amenities",
    "DROP POLICY IF EXISTS amenities_insert ON amenities",
    "DROP POLICY IF EXISTS amenities_update ON amenities",
    "DROP POLICY IF EXISTS amenities_delete ON amenities",
    "ALTER TABLE amenities NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE amenities DISABLE ROW LEVEL SECURITY",
    # 10. property_categories
    "DROP POLICY IF EXISTS property_categories_select ON property_categories",
    "DROP POLICY IF EXISTS property_categories_insert ON property_categories",
    "DROP POLICY IF EXISTS property_categories_update ON property_categories",
    "DROP POLICY IF EXISTS property_categories_delete ON property_categories",
    "ALTER TABLE property_categories NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE property_categories DISABLE ROW LEVEL SECURITY",
    # 11. platform_settings
    "DROP POLICY IF EXISTS platform_settings_select ON platform_settings",
    "DROP POLICY IF EXISTS platform_settings_insert ON platform_settings",
    "DROP POLICY IF EXISTS platform_settings_update ON platform_settings",
    "ALTER TABLE platform_settings NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE platform_settings DISABLE ROW LEVEL SECURITY",
    # 12. audit_logs
    "DROP POLICY IF EXISTS audit_logs_select ON audit_logs",
    "DROP POLICY IF EXISTS audit_logs_insert ON audit_logs",
    "ALTER TABLE audit_logs NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE audit_logs DISABLE ROW LEVEL SECURITY",
    # 13. Revoke runtime grants
    """DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dormiostay_app') THEN
        REVOKE ALL PRIVILEGES ON users, user_profiles, user_sessions,
            email_verification_tokens, password_reset_tokens, email_outbox,
            locations, institutions, amenities, property_categories,
            platform_settings, audit_logs FROM dormiostay_app;
    END IF;
END
$$;""",
]


def upgrade() -> None:
    for stmt in UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_STATEMENTS:
        op.execute(stmt)
