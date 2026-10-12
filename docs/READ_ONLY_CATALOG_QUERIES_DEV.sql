-- READ_ONLY_CATALOG_QUERIES_DEV.sql
-- Read-only catalog/metadata queries for the DEV database. For an operator to run
-- in a read-only session; queries 1-9, 11 and 12 read pg_catalog /
-- information_schema only; query 10 runs
-- full-table min/max/count aggregates over application tables (review_decision_events,
-- audit_logs): read-only, but it reads application rows, so run it in a quiet window.
-- Nothing here has been run by the author.
--
-- Suggested session guard:
--   BEGIN READ ONLY;  -- run the queries below, then ROLLBACK;
-- Do not add privilege changes, ownership changes or DDL to this file.

-- 1. Object owners for the review / registry / report / Area-2 tables.
SELECT n.nspname AS schema, c.relname AS object, c.relkind AS kind,
       pg_get_userbyid(c.relowner) AS owner
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S')
  AND (c.relname IN ('review_decision_events','review_records','review_samples',
                     'review_cycles','review_reports','tefca_entity_relationships',
                     'tefca_identifier_decision_events','report_artifacts',
                     'report_export_jobs','source_snapshot','alembic_version')
       OR c.relname LIKE 'rce\_%' ESCAPE '\'        -- Area 1 / Area 2 (curation) tables
       OR c.relname LIKE 'tefca\_reg\_%' ESCAPE '\' -- canonical registry
       OR c.relname LIKE 'iqvia\_%' ESCAPE '\')
ORDER BY c.relname;

-- 2. ACLs (grants) on the same objects. NULL relacl = default (owner-only) privileges.
SELECT c.relname AS object, pg_get_userbyid(c.relowner) AS owner, c.relacl::text AS acl
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
  AND (c.relname IN ('review_decision_events','review_records','tefca_entity_relationships',
                     'tefca_identifier_decision_events','report_artifacts','report_export_jobs')
       OR c.relname LIKE 'rce\_%' ESCAPE '\')
ORDER BY c.relname;

-- 3. Column-level and table-level privileges as the information schema reports them.
SELECT table_name, grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name IN ('review_decision_events','review_records','tefca_entity_relationships',
                     'tefca_identifier_decision_events','report_artifacts')
ORDER BY table_name, grantee, privilege_type;

-- 3b. Column-level privileges (role_table_grants above omits column-only grants).
SELECT table_name, column_name, grantee, privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name IN ('review_decision_events','review_records','tefca_entity_relationships',
                     'tefca_identifier_decision_events','report_artifacts')
ORDER BY table_name, column_name, grantee, privilege_type;

-- 4. Triggers (non-internal) on public tables, with the function each calls.
SELECT c.relname AS table_name, t.tgname AS trigger_name, t.tgenabled AS enabled,
       p.proname AS function_name, pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_proc p ON p.oid = t.tgfoid
WHERE NOT t.tgisinternal AND n.nspname = 'public'
ORDER BY c.relname, t.tgname;

-- 5. Function owners (and whether SECURITY DEFINER) in public.
SELECT p.proname AS function_name, pg_get_userbyid(p.proowner) AS owner,
       p.prosecdef AS security_definer, l.lanname AS language
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE n.nspname = 'public'
ORDER BY p.proname;

-- 6. Role memberships (who is a member of which role).
SELECT r.rolname AS role, m.rolname AS member, g.rolname AS granted_by, am.admin_option
FROM pg_auth_members am
JOIN pg_roles r ON r.oid = am.roleid
JOIN pg_roles m ON m.oid = am.member
LEFT JOIN pg_roles g ON g.oid = am.grantor
ORDER BY r.rolname, m.rolname;

-- 7. Role attributes relevant to who could alter history (no password data is selected).
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin, rolbypassrls
FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname;

-- 8. Current Alembic revision.
SELECT version_num FROM alembic_version;

-- 9. Retention-related facts readable through SQL.
--    (Backup retention days and PITR window are Azure resource properties and are
--     NOT visible from SQL; they need `az postgres flexible-server show`.)
SELECT name, setting, unit, source
FROM pg_settings
WHERE name IN ('archive_mode','archive_timeout','wal_level','max_wal_size','min_wal_size',
               'wal_keep_size','log_statement','log_min_duration_statement',
               'idle_in_transaction_session_timeout','statement_timeout',
               'track_commit_timestamp','shared_preload_libraries')
ORDER BY name;

SELECT pg_postmaster_start_time() AS server_started_at, now() AS now, version() AS server_version;

-- 10. Oldest/newest timestamp per history-bearing table (retention as observed in data).
--     These aggregate over the whole table; run in a quiet window. Remove any line
--     whose table or column does not exist in the connected schema.
SELECT 'review_decision_events' AS tbl, min(occurred_at) AS oldest, max(occurred_at) AS newest, count(*) AS n
FROM review_decision_events;
SELECT 'audit_logs' AS tbl, min(created_at) AS oldest, max(created_at) AS newest, count(*) AS n
FROM audit_logs;

-- 11. Table sizes (capacity facts for backup/restore sizing).
SELECT c.relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 25;

-- 12. Append-only / immutability enforcement present? (rules and row-level security)
SELECT schemaname, tablename, rulename, definition
FROM pg_rules WHERE schemaname = 'public' ORDER BY tablename, rulename;

SELECT c.relname, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND c.relname IN ('review_decision_events','review_records','tefca_identifier_decision_events');
