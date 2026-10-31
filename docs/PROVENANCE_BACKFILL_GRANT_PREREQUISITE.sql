-- =====================================================================================
-- PROVENANCE_BACKFILL_GRANT_PREREQUISITE.sql   STATUS: DRAFT FOR DBA REVIEW - NOT APPLIED
--
-- Prerequisite for Alembic revision 20261030_default_routing_prov (PR #188, merged,
-- UNAPPLIED anywhere). NOT part of the Alembic chain. Never run by CI, by the governed
-- migration pipeline or by any application code. A human DBA runs it, by hand, after
-- reviewing the PRE-CHECK output. Companion: PROVENANCE_BACKFILL_GRANT_PREREQUISITE.md.
--
-- WHY
--   Governed release run 41170540825 failed: the migration connection (current_user
--   docuaction_owner, proven in the run log) was denied SELECT on public.review_records.
--   review_records is DELIBERATELY owned by the runtime role on converged databases
--   (docs/SOD_DB_OWNERSHIP_PREREQUISITE.md section 5: the managed chain temporarily
--   re-owns it for DDL and FINALIZE hands it back - "access by ownership", no explicit
--   grants). The migration role therefore has NO privilege on it, and the one-time
--   ownership-transfer repair tried for rce_issues (run 35158910794, 2026-09-16) FAILED
--   on exactly this shape: docuaction_owner cannot reassign an object it does not own,
--   and the pipeline identity cannot SET ROLE docuaction_app.
--
--   This runbook makes the SMALLEST change that lets the approved data-only migration
--   run: two narrow GRANTs to the migration role. It does NOT transfer ownership (the
--   documented ownership model for review_records is kept intact), does NOT touch the
--   runtime role's access, and does NOT edit any row.
--
-- WHAT THE MIGRATION NEEDS (and exactly what is granted)
--   upgrade():   SELECT + UPDATE on public.review_records (backfill selector + 1-row set)
--                INSERT          on public.tefca_reg_audit_log (its audit rows)
--   downgrade(): UPDATE on review_records + SELECT on tefca_reg_audit_log
--   => GRANT SELECT, UPDATE ON public.review_records     TO docuaction_owner;
--      GRANT SELECT, INSERT ON public.tefca_reg_audit_log TO docuaction_owner;
--   Deliberately NOT granted: DELETE, TRUNCATE, REFERENCES, TRIGGER on either table;
--   UPDATE or DELETE on tefca_reg_audit_log (the audit log stays append-only to everyone).
--
-- WHAT IS UNVERIFIED (no DEV/PROD access was used to write this)
--   * the actual owners of review_records and tefca_reg_audit_log on DEV (believed
--     docuaction_app per the convergence docs; APPLY asserts it and aborts otherwise)
--   * existing ACLs and any other grantees; RLS state (believed none - no migration in
--     this repository enables RLS); dependent views (none in this repository read
--     review_records; review_effective_determination reads review_decision_events only)
--   Run PRE-CHECKS first and record the output. If anything differs from what APPLY
--   asserts, stop and escalate; do not edit APPLY on the fly.
--
-- HOW TO USE (psql, as a role that can SET ROLE to the table owner - e.g. the server
-- admin; not docuaction_app itself and not the pipeline SP)
--   1. Run section 1 (PRE-CHECKS). Read-only. Save the output.
--   2. Run section 2 (APPLY) with `psql -v ON_ERROR_STOP=1`. ONE transaction; its
--      POST-CHECK assertions run before COMMIT, so any failure rolls back everything.
--   3. Run section 3 (POST-CHECKS report) afterwards and save the output.
--   4. Only then re-dispatch the governed release (apply_migrations=true,
--      expected_current=20261012_sod_db_enforcement,
--      target_revision=20261030_default_routing_prov, handshake_issue=191).
--   Section 4 (ROLLBACK) is reference text, NOT executed with the others.
--
-- IDEMPOTENT: APPLY may be run again; a repeated GRANT is a no-op and every assertion
-- still passes. Role names are literal on purpose: the reviewed text is the executed text.
-- =====================================================================================


-- @@SECTION: PRECHECKS  (read-only; run first; save the output)
-- 1.1 both tables: owner, ACL, RLS
SELECT c.relname, c.relowner::regrole AS owner, c.relacl, c.relrowsecurity
  FROM pg_class c
 WHERE c.oid IN ('public.review_records'::regclass, 'public.tefca_reg_audit_log'::regclass);
-- 1.2 what the migration role can do today (expected: all false)
SELECT t.tbl, p AS privilege, has_table_privilege('docuaction_owner', t.tbl, p) AS migration_role_has
  FROM (VALUES ('public.review_records'), ('public.tefca_reg_audit_log')) AS t(tbl),
       unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS p
 ORDER BY t.tbl, p;
-- 1.3 what the runtime role can do today (for the unchanged-afterwards comparison)
SELECT t.tbl, p AS privilege, has_table_privilege('docuaction_app', t.tbl, p) AS runtime_role_has
  FROM (VALUES ('public.review_records'), ('public.tefca_reg_audit_log')) AS t(tbl),
       unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) AS p
 ORDER BY t.tbl, p;
-- 1.4 alembic revision (must be 20261012_sod_db_enforcement)
SELECT version_num FROM alembic_version;
-- 1.5 the affected set the migration will touch (expected: exactly 1 row, REV-2026-001521).
--     Run as a role that may read the table (the table owner); counts only, no business values.
SELECT count(*) AS backfill_rows
  FROM public.review_records
 WHERE classification_bucket IS NOT NULL
   AND classification_rule IS NULL
   AND classification_rationale LIKE 'No active rule matched these results, so the review defaults to B3 for manual examination rather than being passed silently.%';


-- @@SECTION: APPLY  (one transaction; psql -v ON_ERROR_STOP=1; assertions abort = full rollback)
BEGIN;

DO $$
DECLARE rev text; own_rr regrole; own_al regrole;
BEGIN
  SELECT version_num INTO rev FROM alembic_version;
  IF rev <> '20261012_sod_db_enforcement' THEN
    RAISE EXCEPTION 'alembic_version is % - expected 20261012_sod_db_enforcement; nothing changed', rev;
  END IF;
  SELECT relowner::regrole INTO own_rr FROM pg_class WHERE oid = 'public.review_records'::regclass;
  SELECT relowner::regrole INTO own_al FROM pg_class WHERE oid = 'public.tefca_reg_audit_log'::regclass;
  IF own_rr::text <> 'docuaction_app' OR own_al::text <> 'docuaction_app' THEN
    RAISE EXCEPTION 'owners are % / % - APPLY assumes docuaction_app owns both; stop and escalate', own_rr, own_al;
  END IF;
END $$;

-- The GRANTs must be issued by the table owner; the DBA session assumes it for
-- exactly these two statements.
SET LOCAL ROLE docuaction_app;
GRANT SELECT, UPDATE ON public.review_records      TO docuaction_owner;
GRANT SELECT, INSERT ON public.tefca_reg_audit_log TO docuaction_owner;
RESET ROLE;

-- POST-CHECK assertions, inside the same transaction.
DO $$
BEGIN
  -- the migration role now holds exactly what the migration needs ...
  IF NOT has_table_privilege('docuaction_owner', 'public.review_records', 'SELECT')
     OR NOT has_table_privilege('docuaction_owner', 'public.review_records', 'UPDATE')
     OR NOT has_table_privilege('docuaction_owner', 'public.tefca_reg_audit_log', 'SELECT')
     OR NOT has_table_privilege('docuaction_owner', 'public.tefca_reg_audit_log', 'INSERT') THEN
    RAISE EXCEPTION 'grants did not take effect';
  END IF;
  -- ... and nothing more
  IF has_table_privilege('docuaction_owner', 'public.review_records', 'DELETE')
     OR has_table_privilege('docuaction_owner', 'public.review_records', 'TRUNCATE')
     OR has_table_privilege('docuaction_owner', 'public.tefca_reg_audit_log', 'UPDATE')
     OR has_table_privilege('docuaction_owner', 'public.tefca_reg_audit_log', 'DELETE')
     OR has_table_privilege('docuaction_owner', 'public.tefca_reg_audit_log', 'TRUNCATE') THEN
    RAISE EXCEPTION 'migration role holds more than the approved set - rolling back';
  END IF;
  -- the runtime role still owns both tables (full access by ownership, unchanged)
  IF (SELECT relowner::regrole::text FROM pg_class WHERE oid='public.review_records'::regclass) <> 'docuaction_app'
     OR (SELECT relowner::regrole::text FROM pg_class WHERE oid='public.tefca_reg_audit_log'::regclass) <> 'docuaction_app' THEN
    RAISE EXCEPTION 'ownership changed - this runbook must not do that; rolling back';
  END IF;
END $$;

COMMIT;


-- @@SECTION: POSTCHECKS  (read-only report; run after COMMIT and save the output)
SELECT c.relname, c.relowner::regrole AS owner, c.relacl
  FROM pg_class c
 WHERE c.oid IN ('public.review_records'::regclass, 'public.tefca_reg_audit_log'::regclass);
SELECT t.tbl, p AS privilege, has_table_privilege('docuaction_owner', t.tbl, p) AS migration_role_has
  FROM (VALUES ('public.review_records'), ('public.tefca_reg_audit_log')) AS t(tbl),
       unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS p
 ORDER BY t.tbl, p;


-- @@SECTION: ROLLBACK  (reference only - run by hand if the grants must be withdrawn)
-- BEGIN;
-- SET LOCAL ROLE docuaction_app;
-- REVOKE SELECT, UPDATE ON public.review_records      FROM docuaction_owner;
-- REVOKE SELECT, INSERT ON public.tefca_reg_audit_log FROM docuaction_owner;
-- RESET ROLE;
-- COMMIT;
