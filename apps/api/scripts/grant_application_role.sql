-- Historical Gate 4 mutation entry point retired.
DO $$ BEGIN
    RAISE EXCEPTION 'Direct SQL execution of grant_application_role.sql is strictly prohibited' USING ERRCODE = '42501';
END $$;
