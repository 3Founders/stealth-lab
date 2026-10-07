-- Create the `stealth_app` database role (docs/security_runbook.md, role plan; securityp1.md §5.1 item 5).
--
-- NOT a migration: roles are cluster-level objects and their passwords are secrets. Run this ONCE per database
-- cluster as the owner/admin, with psql, after the migrations, and only with the owner's go-ahead on a shared
-- database. Then point the services' DATABASE_URL at this role. It is idempotent.
--
--   psql "<owner DSN>" -v app_password="'<generated secret>'" -f scripts/sql/create_app_role.sql
--
-- What it guarantees, and why it matters: FORCE ROW LEVEL SECURITY (db/29, db/136) does NOTHING for a role that owns
-- the tables, is superuser, or has BYPASSRLS. A running service must therefore connect as a role that is none of
-- those. This role can read and write rows (DML) and use sequences and functions, and cannot create, alter or drop
-- anything (no DDL, no CREATE on the schema).

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stealth_app') THEN
        CREATE ROLE stealth_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
    END IF;
END $$;
ALTER ROLE stealth_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
\if :{?app_password}
ALTER ROLE stealth_app PASSWORD :app_password;
\endif

GRANT CONNECT ON DATABASE :"DBNAME" TO stealth_app;
GRANT USAGE ON SCHEMA public TO stealth_app;
REVOKE CREATE ON SCHEMA public FROM stealth_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO stealth_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO stealth_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO stealth_app;
-- tables a later migration creates: grant them too (run by the migration role, which owns them)
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO stealth_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO stealth_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO stealth_app;

-- Self-check: prints the role's flags. All must be false.
SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
FROM pg_roles WHERE rolname = 'stealth_app';
