-- =====================================================================
-- PROD DATABASE READ-ONLY INVENTORY
--
-- Run this against the PROD database. It answers every item in the
-- readiness gate's inventory list and it CANNOT modify anything:
--
--   * the first statement puts the session in read-only mode, so the
--     server itself rejects any INSERT/UPDATE/DELETE/DDL that follows;
--   * every statement is a SELECT;
--   * per-table counts use query_to_xml(), which executes a read-only
--     SELECT per table, so no dynamic DDL is generated anywhere.
--
-- It emits COUNTS, SCHEMA METADATA and PROVENANCE FLAGS only. It never
-- selects a row's contents, so no PII, no Government record data and no
-- secret value can appear in the output.
--
-- Validated against the DEV database before being issued, so the syntax
-- and the read-only guarantee are proven rather than assumed.
--
-- USAGE
--   psql "<prod connection>" -f scripts/prod_readonly_inventory.sql -o prod_inventory.txt
-- or paste section by section into any SQL client.
-- =====================================================================

SET default_transaction_read_only = on;

\echo '=== 1. IDENTITY ==='
SELECT current_database()            AS database,
       current_user                  AS current_user,
       session_user                  AS session_user,
       version()                     AS postgres_version,
       current_setting('default_transaction_read_only') AS session_is_read_only;

\echo '=== 2. ALEMBIC REVISION (empty result = Alembic has never run) ==='
SELECT (xpath('/row/v/text()',
        query_to_xml('SELECT version_num AS v FROM public.alembic_version',
                     false, true, '')))[1]::text AS alembic_head
WHERE to_regclass('public.alembic_version') IS NOT NULL;

\echo '=== 3. EVERY TABLE WITH ITS EXACT ROW COUNT ==='
-- Exact counts, not reltuples estimates. Answers: table count, row count
-- per table, populated vs empty, total rows, and any unexpected table.
SELECT t.tablename,
       t.tableowner,
       (xpath('/row/cnt/text()', x))[1]::text::bigint AS row_count
FROM (
  SELECT tablename, tableowner,
         query_to_xml(format('SELECT count(*) AS cnt FROM public.%I', tablename),
                      false, true, '') AS x
  FROM pg_tables WHERE schemaname = 'public'
) t
ORDER BY row_count DESC, tablename;

\echo '=== 4. TOTALS ==='
SELECT count(*)                                    AS total_tables,
       count(*) FILTER (WHERE rc > 0)              AS populated_tables,
       count(*) FILTER (WHERE rc = 0)              AS empty_tables,
       sum(rc)                                     AS total_rows
FROM (
  SELECT (xpath('/row/cnt/text()',
           query_to_xml(format('SELECT count(*) AS cnt FROM public.%I', tablename),
                        false, true, '')))[1]::text::bigint AS rc
  FROM pg_tables WHERE schemaname = 'public'
) s;

\echo '=== 5. FOREIGN KEYS ==='
SELECT count(*) AS foreign_key_count
FROM pg_constraint c
JOIN pg_class cl ON cl.oid = c.conrelid
JOIN pg_namespace n ON n.oid = cl.relnamespace
WHERE c.contype = 'f' AND n.nspname = 'public';

\echo '=== 6. ORPHAN SCAN ACROSS EVERY FOREIGN KEY (rows only, no contents) ==='
SELECT child, childcol, parent, parentcol, orphans
FROM (
  SELECT cl.relname  AS child,
         a.attname   AS childcol,
         cl2.relname AS parent,
         a2.attname  AS parentcol,
         (xpath('/row/cnt/text()', query_to_xml(format(
             'SELECT count(*) AS cnt FROM public.%I ch LEFT JOIN public.%I p '
             || 'ON p.%I = ch.%I WHERE ch.%I IS NOT NULL AND p.%I IS NULL',
             cl.relname, cl2.relname, a2.attname, a.attname, a.attname, a2.attname),
             false, true, '')))[1]::text::bigint AS orphans
  FROM pg_constraint c
  JOIN pg_class cl  ON cl.oid  = c.conrelid
  JOIN pg_class cl2 ON cl2.oid = c.confrelid
  JOIN pg_attribute a  ON a.attrelid  = c.conrelid  AND a.attnum  = c.conkey[1]
  JOIN pg_attribute a2 ON a2.attrelid = c.confrelid AND a2.attnum = c.confkey[1]
  JOIN pg_namespace n  ON n.oid = cl.relnamespace
  WHERE c.contype = 'f' AND n.nspname = 'public'
) o
WHERE orphans > 0
ORDER BY orphans DESC;
-- No rows returned = zero orphans across all foreign keys.

\echo '=== 7. TABLE OWNERSHIP (Area-1 immutability depends on this) ==='
SELECT tableowner, count(*) AS tables, array_agg(tablename ORDER BY tablename) AS table_names
FROM pg_tables WHERE schemaname = 'public'
GROUP BY tableowner ORDER BY count(*) DESC;

\echo '=== 8. AREA-1 PRIVILEGES FOR THE CONNECTING ROLE ==='
-- HOW TO READ THIS. The rce_ prefix alone does NOT mean a table is Area-1.
-- Area-1 is defined by OWNERSHIP: on DEV exactly four tables are owned by the
-- dedicated NOLOGIN role docuaction_owner --
--   rce_ingestion_runs, rce_rule_execution_history,
--   rce_source_intakes, rce_source_records
-- and those four are append-only: SELECT and INSERT allowed, UPDATE, DELETE and
-- TRUNCATE denied. A true in upd/del/trunc IS a control failure *for a table
-- owned by docuaction_owner* and is entirely normal for any other rce_ table,
-- which are ordinary application tables the app legitimately writes.
--
-- If NO table here is owned by a dedicated owner role, Area-1 immutability is
-- INERT on this database regardless of how the grants look: the owner can
-- always UPDATE and DELETE its own tables. That was the DEV defect.
SELECT tablename,
       tableowner,
       has_table_privilege(current_user, 'public.'||quote_ident(tablename), 'SELECT')   AS sel,
       has_table_privilege(current_user, 'public.'||quote_ident(tablename), 'INSERT')   AS ins,
       has_table_privilege(current_user, 'public.'||quote_ident(tablename), 'UPDATE')   AS upd,
       has_table_privilege(current_user, 'public.'||quote_ident(tablename), 'DELETE')   AS del,
       has_table_privilege(current_user, 'public.'||quote_ident(tablename), 'TRUNCATE') AS trunc
FROM pg_tables
WHERE schemaname = 'public' AND tablename LIKE 'rce\_%'
ORDER BY tablename;

\echo '=== 9. IS THE CONNECTING ROLE PRIVILEGED? ==='
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
FROM pg_roles WHERE rolname = current_user;

\echo '=== 10. MOCK / TEST PROVENANCE FLAGS (counts only) ==='
-- Any table carrying an is_mock_data or is_deleted column, with how many
-- rows are flagged. Contents are never selected.
SELECT c.table_name, c.column_name,
       (xpath('/row/cnt/text()', query_to_xml(format(
           'SELECT count(*) AS cnt FROM public.%I WHERE %I', c.table_name, c.column_name),
           false, true, '')))[1]::text::bigint AS flagged_rows
FROM information_schema.columns c
JOIN pg_tables t ON t.tablename = c.table_name AND t.schemaname = 'public'
WHERE c.table_schema = 'public'
  AND c.column_name IN ('is_mock_data', 'is_deleted')
  AND c.data_type = 'boolean'
ORDER BY c.table_name, c.column_name;

\echo '=== 11. USER IDENTITY DOMAINS (domain + count only, no addresses) ==='
SELECT (xpath('/row/d/text()', x))[1]::text AS email_domain,
       (xpath('/row/n/text()', x))[1]::text::bigint AS users
FROM unnest((SELECT xpath('/table/row',
        query_to_xml(
          'SELECT split_part(email, ''@'', 2) AS d, count(*) AS n '
          || 'FROM public.users GROUP BY 1 ORDER BY 2 DESC', false, false, ''))
      WHERE to_regclass('public.users') IS NOT NULL)) AS x;

\echo '=== 12. PPEF SNAPSHOT PROVENANCE (metadata only, no records) ==='
SELECT (xpath('/row/c/text()', x))[1]::text        AS component,
       (xpath('/row/s/text()', x))[1]::text        AS ingest_status,
       (xpath('/row/t/text()', x))[1]::text        AS rows_truncated,
       (xpath('/row/n/text()', x))[1]::text::bigint AS snapshots,
       (xpath('/row/d/text()', x))[1]::text::bigint AS declared_rows
FROM unnest((SELECT xpath('/table/row',
        query_to_xml(
          'SELECT component AS c, ingest_status AS s, rows_truncated AS t, '
          || 'count(*) AS n, coalesce(sum(record_count),0) AS d '
          || 'FROM public.tefca_ppef_snapshots GROUP BY 1,2,3 ORDER BY 1,2',
          false, false, ''))
      WHERE to_regclass('public.tefca_ppef_snapshots') IS NOT NULL)) AS x;

\echo '=== INVENTORY COMPLETE — nothing was modified ==='
