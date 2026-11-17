-- Run as the migration owner using psql -v app_role=your_existing_login.
-- Provision the login separately with a secret outside source control.
\set ON_ERROR_STOP on
BEGIN;
SELECT set_config('dormiostay.grant_role', :'app_role', true);
DO $$
DECLARE target record;
BEGIN
    SELECT * INTO target FROM pg_roles
        WHERE rolname = current_setting('dormiostay.grant_role');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Application role must already exist';
    END IF;
    IF target.rolsuper OR target.rolbypassrls OR target.rolcreaterole OR target.rolreplication
       OR target.rolcreatedb OR target.oid = (SELECT oid FROM pg_roles WHERE rolname=current_user)
    THEN
        RAISE EXCEPTION 'Application role must be separate and unprivileged';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=target.oid)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner=target.oid)
       OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database()
                  AND datdba=target.oid)
       OR has_schema_privilege(target.rolname, 'public', 'CREATE') THEN
        RAISE EXCEPTION 'Application role has elevated memberships or ownership';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace
               AND relowner=target.oid) THEN
        RAISE EXCEPTION 'Application role must not own public tables';
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO :"app_role";
GRANT SELECT, INSERT, UPDATE ON users, user_profiles, user_sessions,
    email_verification_tokens, password_reset_tokens, locations, institutions,
    amenities, property_categories, platform_settings TO :"app_role";
GRANT DELETE ON user_sessions, email_verification_tokens, password_reset_tokens TO :"app_role";
REVOKE ALL ON audit_logs FROM :"app_role";
GRANT SELECT, INSERT ON audit_logs TO :"app_role";
COMMIT;
