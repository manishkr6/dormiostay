-- REFERENCE SPECIFICATION ONLY — DO NOT EXECUTE DIRECTLY
--
-- Live Gate 4 execution must ONLY be performed via the authenticated Python runner:
-- python scripts/execute_gate4_grants.py
--
-- Direct psql or manual execution is strictly prohibited by security policy.
DO $$ BEGIN
    RAISE EXCEPTION 'Direct SQL execution of grant_application_role.sql is strictly prohibited. Use python scripts/execute_gate4_grants.py instead.' USING ERRCODE = '42501';
END $$;
