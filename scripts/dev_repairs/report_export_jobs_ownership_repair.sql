-- DEV repair: public.report_export_jobs ownership (docuaction_app -> docuaction_owner)
-- while keeping the runtime role's required access.
--
-- WHY. Read-only diagnostic run 37337241352 (2026-10-05) found the table owned
-- by the RUNTIME role docuaction_app. Revision 20261001_report_generation_jobs
-- alters it as docuaction_owner and failed with "must be owner of table
-- report_export_jobs" (run 37272045742). A bare ownership transfer would
-- remove every privilege the application has on the table, so the transfer and
-- the runtime grant are ONE transaction.
--
-- WHO RUNS IT. The migration identity cannot: it is a member of
-- docuaction_owner only. The server admin role can: it owns the table through
-- its membership in docuaction_app, and holds ADMIN OPTION on docuaction_owner.
-- PostgreSQL also requires the transferring role to be able to SET ROLE to the
-- new owner; the admin grants itself that for this transaction only and
-- revokes it again before COMMIT.
--
-- EFFECT, exactly:
--   owner of public.report_export_jobs   docuaction_app -> docuaction_owner
--   docuaction_app on that table         owner (all)    -> SELECT, INSERT, UPDATE
--   role memberships                     unchanged (verified)
--   rows, columns, indexes, alembic      unchanged (verified)
-- SELECT/INSERT/UPDATE is what app/reports/data/export_jobs.py issues (create a
-- job, read it, claim it FOR UPDATE, set state/heartbeat) and is the shape the
-- chain already gives its sibling job tables (iqvia_import_job,
-- rce_recheck_job). The runtime role loses DELETE/TRUNCATE/REFERENCES/TRIGGER,
-- which it never uses.
--
-- SAFE BY DEFAULT. Without  -v apply=1  this is a DRY RUN: every step and every
-- check executes, then the transaction is ROLLED BACK. With apply=1 it COMMITs
-- only after all validation passes. Any error rolls everything back.
--
--   psql ... -v ON_ERROR_STOP=1 -f report_export_jobs_ownership_repair.sql            (dry run)
--   psql ... -v ON_ERROR_STOP=1 -v apply=1 -f report_export_jobs_ownership_repair.sql (apply)
\set ON_ERROR_STOP on
\if :{?apply} \else \set apply 0 \endif
\if :{?expected_rev} \else \set expected_rev 20260930_alembic_version_read \endif

BEGIN;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '120s';
SELECT set_config('repair.expected_rev', :'expected_rev', true) AS expected_revision,
       current_user AS executing_role, current_setting('server_version') AS server_version;

-- ── Preconditions (nothing has changed yet) ─────────────────────────────────
DO $$
DECLARE cur_owner text;
BEGIN
  IF (SELECT count(*) FROM pg_roles WHERE rolname IN ('docuaction_owner', 'docuaction_app')) <> 2 THEN
    RAISE EXCEPTION 'STOP: docuaction_owner / docuaction_app do not both exist on this server';
  END IF;
  SELECT r.rolname INTO cur_owner
  FROM pg_class k JOIN pg_roles r ON r.oid = k.relowner
  WHERE k.oid = to_regclass('public.report_export_jobs');
  IF cur_owner IS NULL THEN
    RAISE EXCEPTION 'STOP: public.report_export_jobs not found';
  ELSIF cur_owner = 'docuaction_owner' THEN
    RAISE EXCEPTION 'STOP: public.report_export_jobs is already owned by docuaction_owner - nothing to do';
  ELSIF cur_owner <> 'docuaction_app' THEN
    RAISE EXCEPTION 'STOP: owner is %, not docuaction_app - this repair was written for the diagnosed state only', cur_owner;
  END IF;
  IF current_user IN ('docuaction_owner', 'docuaction_app') THEN
    RAISE EXCEPTION 'STOP: run this as the server admin role, not as %', current_user;
  END IF;
  IF NOT pg_has_role(current_user, 'docuaction_app', 'USAGE') THEN
    RAISE EXCEPTION 'STOP: % does not hold docuaction_app''s privileges, so it does not own the table and cannot transfer it. No permission was changed.', current_user;
  END IF;
END $$;

-- Snapshots for the before/after comparison (dropped with the transaction).
CREATE TEMP TABLE repair_members ON COMMIT DROP AS
  SELECT m.roleid, m.member, m.grantor, to_jsonb(m) - 'oid' AS options
  FROM pg_auth_members m
  WHERE m.roleid IN (SELECT oid FROM pg_roles WHERE rolname IN ('docuaction_owner', 'docuaction_app'))
     OR m.member IN (SELECT oid FROM pg_roles WHERE rolname IN ('docuaction_owner', 'docuaction_app'));
CREATE TEMP TABLE repair_other_grants ON COMMIT DROP AS
  SELECT COALESCE(gee.rolname, 'PUBLIC') AS grantee, a.privilege_type, a.is_grantable, '' AS column_name
  FROM pg_class k CROSS JOIN LATERAL aclexplode(COALESCE(k.relacl, acldefault('r', k.relowner))) a
  LEFT JOIN pg_roles gee ON gee.oid = a.grantee
  WHERE k.oid = 'public.report_export_jobs'::regclass AND a.grantee <> k.relowner
  UNION ALL
  SELECT COALESCE(gee.rolname, 'PUBLIC'), a.privilege_type, a.is_grantable, att.attname
  FROM pg_attribute att CROSS JOIN LATERAL aclexplode(att.attacl) a
  LEFT JOIN pg_roles gee ON gee.oid = a.grantee
  WHERE att.attrelid = 'public.report_export_jobs'::regclass AND att.attnum > 0 AND att.attacl IS NOT NULL;
CREATE TEMP TABLE repair_shape ON COMMIT DROP AS
  SELECT (SELECT count(*) FROM public.report_export_jobs) AS row_count,
         (SELECT string_agg(attname || ':' || atttypid::regtype::text, ',' ORDER BY attnum)
          FROM pg_attribute WHERE attrelid = 'public.report_export_jobs'::regclass
            AND attnum > 0 AND NOT attisdropped) AS columns,
         (SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes
          WHERE schemaname = 'public' AND tablename = 'report_export_jobs') AS indexes;

\echo '--- BEFORE'
SELECT r.rolname AS owner_before, s.row_count, (SELECT count(*) FROM repair_other_grants) AS other_grant_entries
FROM pg_class k JOIN pg_roles r ON r.oid = k.relowner, repair_shape s
WHERE k.oid = 'public.report_export_jobs'::regclass;

-- ── Temporary authority, this transaction only ──────────────────────────────
SELECT NOT pg_has_role(current_user, 'docuaction_owner', 'SET') AS need_temp_set \gset
\if :need_temp_set
  \echo '--- granting this role SET on docuaction_owner for this transaction only'
  GRANT docuaction_owner TO CURRENT_USER WITH SET TRUE, INHERIT FALSE;
\endif

-- ── Hard stop: Alembic revision, before the table is touched ────────────────
SET LOCAL ROLE docuaction_owner;
DO $$
DECLARE revs text[];
BEGIN
  SELECT array_agg(version_num) INTO revs FROM public.alembic_version;
  IF revs IS NULL OR array_length(revs, 1) <> 1 OR revs[1] <> current_setting('repair.expected_rev') THEN
    RAISE EXCEPTION 'STOP: alembic_version is %, expected % - nothing changed', revs, current_setting('repair.expected_rev');
  END IF;
  RAISE NOTICE 'alembic_version = % (as expected)', revs[1];
END $$;
RESET ROLE;

-- ── The repair ──────────────────────────────────────────────────────────────
ALTER TABLE public.report_export_jobs OWNER TO docuaction_owner;
SET LOCAL ROLE docuaction_owner;
GRANT SELECT, INSERT, UPDATE ON public.report_export_jobs TO docuaction_app;
RESET ROLE;

\if :need_temp_set
  REVOKE docuaction_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;
\endif

-- ── Validation (any failure aborts and rolls back everything above) ─────────
DO $$
DECLARE owner_now text; app_privs text[]; n int; shape record;
BEGIN
  SELECT r.rolname INTO owner_now FROM pg_class k JOIN pg_roles r ON r.oid = k.relowner
  WHERE k.oid = 'public.report_export_jobs'::regclass;
  IF owner_now <> 'docuaction_owner' THEN
    RAISE EXCEPTION 'VALIDATION FAILED: owner is % after the transfer', owner_now;
  END IF;

  SELECT array_agg(p ORDER BY p) INTO app_privs
  FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p
  WHERE has_table_privilege('docuaction_app', 'public.report_export_jobs', p);
  IF app_privs IS DISTINCT FROM ARRAY['INSERT','SELECT','UPDATE'] THEN
    RAISE EXCEPTION 'VALIDATION FAILED: docuaction_app effective privileges are %, expected exactly {INSERT,SELECT,UPDATE}', app_privs;
  END IF;

  -- Non-owner ACL now = what was there before + the three runtime grants. Nothing else.
  SELECT count(*) INTO n FROM (
    (SELECT COALESCE(gee.rolname, 'PUBLIC'), a.privilege_type, a.is_grantable, ''
       FROM pg_class k CROSS JOIN LATERAL aclexplode(COALESCE(k.relacl, acldefault('r', k.relowner))) a
       LEFT JOIN pg_roles gee ON gee.oid = a.grantee
      WHERE k.oid = 'public.report_export_jobs'::regclass AND a.grantee <> k.relowner
     UNION ALL
     SELECT COALESCE(gee.rolname, 'PUBLIC'), a.privilege_type, a.is_grantable, att.attname
       FROM pg_attribute att CROSS JOIN LATERAL aclexplode(att.attacl) a
       LEFT JOIN pg_roles gee ON gee.oid = a.grantee
      WHERE att.attrelid = 'public.report_export_jobs'::regclass AND att.attnum > 0 AND att.attacl IS NOT NULL)
    EXCEPT
    (SELECT grantee, privilege_type, is_grantable, column_name FROM repair_other_grants
      WHERE grantee <> 'docuaction_owner'
     UNION
     SELECT 'docuaction_app', p, false, '' FROM unnest(ARRAY['SELECT','INSERT','UPDATE']) p)
  ) unexpected;
  IF n <> 0 THEN
    RAISE EXCEPTION 'VALIDATION FAILED: % unexpected grant entr(ies) on the table after the repair', n;
  END IF;
  SELECT count(*) INTO n FROM repair_other_grants b
  WHERE b.grantee NOT IN ('docuaction_owner', 'docuaction_app')
    AND NOT EXISTS (
      SELECT 1 FROM pg_class k CROSS JOIN LATERAL aclexplode(k.relacl) a LEFT JOIN pg_roles gee ON gee.oid = a.grantee
       WHERE k.oid = 'public.report_export_jobs'::regclass AND b.column_name = ''
         AND COALESCE(gee.rolname, 'PUBLIC') = b.grantee AND a.privilege_type = b.privilege_type
         AND a.is_grantable = b.is_grantable
      UNION ALL
      SELECT 1 FROM pg_attribute att CROSS JOIN LATERAL aclexplode(att.attacl) a LEFT JOIN pg_roles gee ON gee.oid = a.grantee
       WHERE att.attrelid = 'public.report_export_jobs'::regclass AND att.attname = b.column_name
         AND COALESCE(gee.rolname, 'PUBLIC') = b.grantee AND a.privilege_type = b.privilege_type
         AND a.is_grantable = b.is_grantable);
  IF n <> 0 THEN
    RAISE EXCEPTION 'VALIDATION FAILED: % pre-existing grant(s) to other roles were lost', n;
  END IF;

  -- Role memberships exactly as before (the temporary SET grant is gone).
  SELECT count(*) INTO n FROM (
    (SELECT m.roleid, m.member, m.grantor, to_jsonb(m) - 'oid' FROM pg_auth_members m
      WHERE m.roleid IN (SELECT oid FROM pg_roles WHERE rolname IN ('docuaction_owner', 'docuaction_app'))
         OR m.member IN (SELECT oid FROM pg_roles WHERE rolname IN ('docuaction_owner', 'docuaction_app'))
     EXCEPT SELECT roleid, member, grantor, options FROM repair_members)
    UNION ALL
    (SELECT roleid, member, grantor, options FROM repair_members
     EXCEPT SELECT m.roleid, m.member, m.grantor, to_jsonb(m) - 'oid' FROM pg_auth_members m
      WHERE m.roleid IN (SELECT oid FROM pg_roles WHERE rolname IN ('docuaction_owner', 'docuaction_app'))
         OR m.member IN (SELECT oid FROM pg_roles WHERE rolname IN ('docuaction_owner', 'docuaction_app')))
  ) d;
  IF n <> 0 THEN
    RAISE EXCEPTION 'VALIDATION FAILED: role memberships differ from before (% row(s))', n;
  END IF;

  -- Rows, columns and indexes untouched.
  SELECT * INTO shape FROM repair_shape;
  IF shape.row_count <> (SELECT count(*) FROM public.report_export_jobs)
     OR shape.columns IS DISTINCT FROM (SELECT string_agg(attname || ':' || atttypid::regtype::text, ',' ORDER BY attnum)
                                        FROM pg_attribute WHERE attrelid = 'public.report_export_jobs'::regclass
                                          AND attnum > 0 AND NOT attisdropped)
     OR shape.indexes IS DISTINCT FROM (SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes
                                        WHERE schemaname = 'public' AND tablename = 'report_export_jobs') THEN
    RAISE EXCEPTION 'VALIDATION FAILED: rows, columns or indexes changed';
  END IF;
  RAISE NOTICE 'validation passed: owner, runtime privileges, other grants, memberships, rows, columns, indexes';
END $$;

-- ── The runtime role, as itself: read and claim work; delete does not ───────
SAVEPOINT runtime_check;
SET LOCAL ROLE docuaction_app;
SELECT count(*) AS runtime_can_read_rows FROM public.report_export_jobs;
SELECT count(*) AS runtime_can_lock_for_update
FROM (SELECT 1 FROM public.report_export_jobs LIMIT 1 FOR UPDATE SKIP LOCKED) locked;
DO $$
BEGIN
  DELETE FROM public.report_export_jobs WHERE false;
  RAISE EXCEPTION 'VALIDATION FAILED: the runtime role can still DELETE';
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'runtime role: DELETE correctly refused';
END $$;
RESET ROLE;
ROLLBACK TO SAVEPOINT runtime_check;

\echo '--- AFTER (inside the transaction)'
SELECT r.rolname AS owner_after,
       (SELECT string_agg(p, ', ' ORDER BY p)
        FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p
        WHERE has_table_privilege('docuaction_app', 'public.report_export_jobs', p)) AS runtime_effective_privileges
FROM pg_class k JOIN pg_roles r ON r.oid = k.relowner
WHERE k.oid = 'public.report_export_jobs'::regclass;

\if :apply
  COMMIT;
  \echo '=== APPLIED AND COMMITTED: report_export_jobs is owned by docuaction_owner; docuaction_app has SELECT, INSERT, UPDATE ==='
\else
  ROLLBACK;
  \echo '=== DRY RUN COMPLETE: every step and check passed, then ROLLED BACK. Nothing was changed. Re-run with -v apply=1 to apply. ==='
\endif
