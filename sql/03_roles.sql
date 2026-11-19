-- 03_roles.sql: read-only role for Power BI (SPEC.md section 10, "Roles"). Run as admin (postgres).
-- Never put the real password in this file. After running it, set the password once, by hand, in DBeaver:
--     ALTER ROLE powerbi_reader WITH PASSWORD '<strong password>';
-- Through the Session pooler, log in as: powerbi_reader.<project_ref>

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'powerbi_reader') THEN
        CREATE ROLE powerbi_reader LOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA plave TO powerbi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA plave TO powerbi_reader;

-- Tables created later by postgres (the admin that runs the DDL) are readable too.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA plave
    GRANT SELECT ON TABLES TO powerbi_reader;
