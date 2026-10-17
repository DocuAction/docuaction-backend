-- =====================================================================================
-- SOD_DB_OWNERSHIP_PREREQUISITE.sql            STATUS: DRAFT FOR DBA REVIEW - NOT APPLIED
--
-- Prerequisite for Alembic revision 20261012_sod_db_enforcement (PR #151).
-- NOT part of the Alembic chain. Never run by CI, by the governed migration pipeline or by
-- any application code. A human DBA runs it, by hand, after reviewing the PRE-CHECK output.
-- Nothing in this file has been run against DEV or PROD. The only database it has been run
-- against is a disposable local fixture (tests/test_sod_ownership_convergence_2026_10_12.py).
--
-- WHY
--   The migration installs guards (an SoD trigger and append-only triggers) on
--   review_decision_events. Its preflight refuses when the runtime role (docuaction_app) owns
--   the table or the SoD function, because an owner can DISABLE/DROP triggers and REPLACE the
--   function: the guards would be removable by the very role they constrain. This file moves
--   the objects to the owner role (docuaction_owner) and gives the runtime role only what the
--   product uses (SELECT, INSERT).
--
-- WHAT IS UNVERIFIED (no DEV/PROD access was used to write this)
--   * who owns review_decision_events and review_event_enforce_sod() on DEV and PROD
--   * the runtime role's real ACL on the table; whether PUBLIC holds anything
--   * which views / functions / other objects depend on the table (the repo defines the view
--     review_effective_determination; a database may carry others)
--   * whether any sequence is owned by a column of the table (the model defines none; the
--     APPLY section handles them if present)
--   * the identity the DBA will run this as (see the guard at the top of APPLY)
--   Run the PRE-CHECKS section first and record the result. If anything differs from what
--   APPLY assumes, stop and escalate; do not edit APPLY on the fly.
--
-- HOW TO USE (psql, as a role that is a member of BOTH the current table owner and
-- docuaction_owner - e.g. the server admin; not docuaction_app and not the pipeline SP)
--   1. Run section 1 (PRE-CHECKS). Read-only. Save the output.
--   2. In a maintenance window (ALTER TABLE ... OWNER takes a short ACCESS EXCLUSIVE lock):
--      run section 2 (APPLY) with `psql -v ON_ERROR_STOP=1`. It is ONE transaction that also
--      runs the POST-CHECK assertions before COMMIT: any failed assertion aborts and rolls
--      back everything.
--   3. Run section 3 (POST-CHECKS report) again afterwards and save the output.
--   4. Only then may the governed pipeline run 20261012_sod_db_enforcement.
--   Section 4 (ROLLBACK) is reference text. It is NOT executed together with the others.
--
-- IDEMPOTENT: APPLY may be run again; the second run changes nothing and passes.
-- Role names are literal (docuaction_owner / docuaction_app). If a database uses other
-- names, replace them consistently before running; nothing here is parameterised on purpose
-- so the reviewed text is exactly the executed text.
-- =====================================================================================


-- @@SECTION: PRECHECKS  (read-only; run first; save the output)
-- 1.1 table: owner, ACL, triggers present, RLS
SELECT c.relname, c.relowner::regrole AS owner, c.relacl, c.relhastriggers, c.relrowsecurity
  FROM pg_class c WHERE c.oid = 'public.review_decision_events'::regclass;
-- 1.2 the SoD function(s): owner, SECURITY DEFINER?, body fingerprint
SELECT p.proname, p.proowner::regrole AS owner, p.prosecdef, p.proacl, md5(p.prosrc) AS body_md5
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'public' AND p.proname IN ('review_event_enforce_sod', 'review_event_append_only_guard');
-- 1.3 triggers on the table and whether each is enabled ('O' = enabled)
SELECT t.tgname, t.tgenabled::text AS enabled, pg_get_triggerdef(t.oid) AS definition
  FROM pg_trigger t WHERE t.tgrelid = 'public.review_decision_events'::regclass AND NOT t.tgisinternal;
-- 1.4 effective privileges of the runtime role (what it can do today)
SELECT p AS privilege, has_table_privilege('docuaction_app', 'public.review_decision_events', p) AS runtime_role_has
  FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS p;
-- 1.5 can the runtime role disable/drop triggers or replace the function? (true = it can)
SELECT pg_has_role('docuaction_app', c.relowner, 'USAGE') AS app_has_table_owner_privs,
       (SELECT pg_has_role('docuaction_app', p.proowner, 'USAGE') FROM pg_proc p
         WHERE p.proname = 'review_event_enforce_sod' LIMIT 1) AS app_has_function_owner_privs
  FROM pg_class c WHERE c.oid = 'public.review_decision_events'::regclass;
-- 1.6 role attributes and memberships (is the runtime role a member of the owner, or vice versa?)
SELECT rolname, rolsuper, rolcreaterole, rolbypassrls, rolcanlogin FROM pg_roles
 WHERE rolname IN ('docuaction_owner', 'docuaction_app');
SELECT r.rolname AS role, m.rolname AS member_of
  FROM pg_auth_members am JOIN pg_roles r ON r.oid = am.member JOIN pg_roles m ON m.oid = am.roleid
 WHERE r.rolname IN ('docuaction_owner', 'docuaction_app') OR m.rolname IN ('docuaction_owner', 'docuaction_app');
-- 1.7 the identity that will run APPLY: must be a member of the current table owner and of docuaction_owner
SELECT current_user, session_user,
       pg_has_role(current_user, 'docuaction_owner', 'USAGE') AS can_act_as_owner_role,
       pg_has_role(current_user, (SELECT relowner FROM pg_class WHERE oid = 'public.review_decision_events'::regclass), 'USAGE')
         AS can_act_as_current_table_owner;
-- 1.8 dependent objects: views/matviews that read the table (their owners need SELECT afterwards)
SELECT DISTINCT d.classid::regclass AS kind, c2.relname AS dependent, c2.relkind::text, c2.relowner::regrole AS dependent_owner
  FROM pg_depend d
  JOIN pg_rewrite rw ON rw.oid = d.objid AND d.classid = 'pg_rewrite'::regclass
  JOIN pg_class c2 ON c2.oid = rw.ev_class
 WHERE d.refobjid = 'public.review_decision_events'::regclass AND c2.oid <> d.refobjid;
-- 1.9 sequences owned by a column of the table (APPLY transfers + grants these)
SELECT s.relname AS sequence, s.relowner::regrole AS owner, s.relacl
  FROM pg_class s JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
 WHERE s.relkind = 'S' AND d.refobjid = 'public.review_decision_events'::regclass;
-- 1.10 tables that reference this table (foreign keys point AT it) and column-level grants on it
SELECT conrelid::regclass AS referencing_table, conname FROM pg_constraint
 WHERE contype = 'f' AND confrelid = 'public.review_decision_events'::regclass;
SELECT grantee::regrole AS grantee, column_name, privilege_type
  FROM information_schema.column_privileges
 WHERE table_schema = 'public' AND table_name = 'review_decision_events'
   AND grantee NOT IN ('docuaction_owner') ORDER BY 1, 2, 3;
-- 1.11 context: how many events exist, and how many carry exception fields (informational)
SELECT count(*) AS events,
       count(*) FILTER (WHERE sod_exception_granted_by IS NOT NULL) AS with_exception_fields
  FROM review_decision_events;
-- 1.11b schema privilege the move needs (false here is fine: APPLY step 2.0b grants it, as PREPARE would)
SELECT has_schema_privilege('docuaction_owner', 'public', 'CREATE') AS owner_role_can_create_in_public;
-- 1.12 REQUIRED: record 1.1 (relacl) and 1.4 verbatim. Section 4 (rollback) needs them.


-- @@SECTION: APPLY  (ONE transaction; includes the POST-CHECK assertions; rolls back on any failure)
BEGIN;
SET LOCAL lock_timeout = '10s';       -- do not queue behind long transactions; retry in the window
SET LOCAL statement_timeout = '60s';

-- 2.0 guards: wrong identity or missing objects abort before anything changes
DO $guard$
BEGIN
    IF to_regclass('public.review_decision_events') IS NULL THEN
        RAISE EXCEPTION 'prerequisite: public.review_decision_events does not exist; nothing to do';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docuaction_owner') THEN
        RAISE EXCEPTION 'prerequisite: role docuaction_owner does not exist';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docuaction_app') THEN
        RAISE EXCEPTION 'prerequisite: role docuaction_app does not exist';
    END IF;
    IF NOT pg_has_role(current_user, 'docuaction_owner', 'USAGE') THEN
        RAISE EXCEPTION 'prerequisite: % cannot act as docuaction_owner; run as an admin that is a member of it', current_user;
    END IF;
    IF NOT pg_has_role(current_user,
           (SELECT relowner FROM pg_class WHERE oid = 'public.review_decision_events'::regclass), 'USAGE') THEN
        RAISE EXCEPTION 'prerequisite: % cannot act as the current owner of review_decision_events', current_user;
    END IF;
    -- the owner role must not be the runtime role or a member of it (that would defeat the purpose)
    IF pg_has_role('docuaction_owner', 'docuaction_app', 'USAGE') THEN
        RAISE EXCEPTION 'prerequisite: docuaction_owner is a member of docuaction_app; fix role membership first';
    END IF;
    -- the runtime role must not be able to act as the new owner
    IF pg_has_role('docuaction_app', 'docuaction_owner', 'USAGE') THEN
        RAISE EXCEPTION 'prerequisite: docuaction_app is a member of docuaction_owner; fix role membership first';
    END IF;
END
$guard$;

-- 2.0b ALTER ... OWNER TO docuaction_owner needs docuaction_owner to hold CREATE on the schema. The managed
--      PREPARE step grants exactly this (and USAGE) anyway; it is granted here, only when missing, so the
--      runbook works whether it runs before or after PREPARE. Requires the executing identity to own the
--      schema or hold the privilege WITH GRANT OPTION; otherwise this statement fails and APPLY rolls back.
DO $schema$
BEGIN
    IF NOT has_schema_privilege('docuaction_owner', 'public', 'CREATE') THEN
        GRANT CREATE ON SCHEMA public TO docuaction_owner;
    END IF;
END
$schema$;

-- 2.1 the SoD function: move it (when it exists; a missing function is created later by the migration, as owner)
DO $fn$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig, p.proowner
               FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'public'
                AND p.proname IN ('review_event_enforce_sod', 'review_event_append_only_guard')
    LOOP
        IF r.proowner <> 'docuaction_owner'::regrole THEN
            EXECUTE format('ALTER FUNCTION %s OWNER TO docuaction_owner', r.sig);
        END IF;
        -- a trigger function is never called directly; do not leave it executable by everyone
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END
$fn$;

-- 2.2 the table. Triggers, indexes and constraints follow the table owner automatically.
DO $own$
BEGIN
    IF (SELECT relowner FROM pg_class WHERE oid = 'public.review_decision_events'::regclass) <> 'docuaction_owner'::regrole THEN
        ALTER TABLE public.review_decision_events OWNER TO docuaction_owner;
    END IF;
END
$own$;

-- 2.3 sequences owned by columns of the table (the model defines none; handled if a database has one)
DO $seq$
DECLARE r record;
BEGIN
    FOR r IN SELECT s.oid::regclass AS seq, s.relowner
               FROM pg_class s JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
              WHERE s.relkind = 'S' AND d.refobjid = 'public.review_decision_events'::regclass
    LOOP
        IF r.relowner <> 'docuaction_owner'::regrole THEN
            EXECUTE format('ALTER SEQUENCE %s OWNER TO docuaction_owner', r.seq);
        END IF;
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC', r.seq);
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM docuaction_app', r.seq);
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO docuaction_app', r.seq);
    END LOOP;
END
$seq$;

-- 2.4 privileges: clean slate for the two roles, then exactly SELECT + INSERT for the runtime role.
--     REVOKE at table level also removes any column-level grant of the same privilege.
--     Other grantees found by PRE-CHECK 1.10 (read-only reporting roles, etc.) are deliberately
--     NOT touched here; review them in the PRE-CHECK output and handle them separately.
REVOKE ALL ON TABLE public.review_decision_events FROM PUBLIC;
REVOKE ALL ON TABLE public.review_decision_events FROM docuaction_app;
GRANT SELECT, INSERT ON TABLE public.review_decision_events TO docuaction_app;

-- 2.5 POST-CHECK assertions (same transaction; any failure aborts and rolls back all of the above)
DO $post$
DECLARE
    p text;
    bad text;
BEGIN
    IF (SELECT relowner FROM pg_class WHERE oid = 'public.review_decision_events'::regclass) <> 'docuaction_owner'::regrole THEN
        RAISE EXCEPTION 'post-check: table is not owned by docuaction_owner';
    END IF;
    FOR bad IN SELECT p2.proname FROM pg_proc p2 JOIN pg_namespace n ON n.oid = p2.pronamespace
                WHERE n.nspname = 'public'
                  AND p2.proname IN ('review_event_enforce_sod', 'review_event_append_only_guard')
                  AND p2.proowner <> 'docuaction_owner'::regrole
    LOOP
        RAISE EXCEPTION 'post-check: function % is not owned by docuaction_owner', bad;
    END LOOP;
    FOREACH p IN ARRAY ARRAY['SELECT', 'INSERT'] LOOP
        IF NOT has_table_privilege('docuaction_app', 'public.review_decision_events', p) THEN
            RAISE EXCEPTION 'post-check: runtime role lacks %', p;
        END IF;
    END LOOP;
    FOREACH p IN ARRAY ARRAY['UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER', 'REFERENCES'] LOOP
        IF has_table_privilege('docuaction_app', 'public.review_decision_events', p) THEN
            RAISE EXCEPTION 'post-check: runtime role still holds %', p;
        END IF;
    END LOOP;
    IF pg_has_role('docuaction_app', 'docuaction_owner', 'USAGE') THEN
        RAISE EXCEPTION 'post-check: runtime role can act as the owner role';
    END IF;
    -- views that read the table must still be readable by their owners (a view runs with its owner's rights)
    FOR bad IN SELECT c2.relname FROM pg_depend d
                 JOIN pg_rewrite rw ON rw.oid = d.objid AND d.classid = 'pg_rewrite'::regclass
                 JOIN pg_class c2 ON c2.oid = rw.ev_class
                WHERE d.refobjid = 'public.review_decision_events'::regclass AND c2.oid <> d.refobjid
                  AND NOT has_table_privilege(c2.relowner, 'public.review_decision_events', 'SELECT')
    LOOP
        RAISE EXCEPTION 'post-check: owner of dependent view % cannot SELECT the table', bad;
    END LOOP;
END
$post$;

COMMIT;


-- @@SECTION: POSTCHECKS  (read-only report; run after APPLY and save; expected values in comments)
SELECT c.relowner::regrole AS table_owner,                               -- expect docuaction_owner
       c.relacl                                                           -- expect {docuaction_owner=arwdDxt.../docuaction_owner,docuaction_app=ar/docuaction_owner}
  FROM pg_class c WHERE c.oid = 'public.review_decision_events'::regclass;
SELECT p AS privilege, has_table_privilege('docuaction_app', 'public.review_decision_events', p) AS runtime_role_has
  FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS p;
                                                                          -- expect SELECT, INSERT true; the rest false
SELECT p.proname, p.proowner::regrole AS owner                            -- expect docuaction_owner for each
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'public' AND p.proname IN ('review_event_enforce_sod', 'review_event_append_only_guard');
SELECT pg_has_role('docuaction_app', 'docuaction_owner', 'USAGE') AS app_can_act_as_owner;   -- expect false
SELECT t.tgname, t.tgenabled::text FROM pg_trigger t
 WHERE t.tgrelid = 'public.review_decision_events'::regclass AND NOT t.tgisinternal;          -- unchanged vs PRE-CHECK 1.3
-- Functional proof (only in a session that can SET ROLE docuaction_app; inside a rolled-back transaction):
--   BEGIN; SET LOCAL ROLE docuaction_app;
--   UPDATE review_decision_events SET rationale = rationale WHERE false;   -- expect: permission denied for table
--   DELETE FROM review_decision_events WHERE false;                        -- expect: permission denied for table
--   ROLLBACK;
-- Then run the application smoke test through the release process (reads and the analyst/QA INSERT paths).


-- @@SECTION: ROLLBACK  (reference only; NOT run together with the other sections)
-- Use only if the move itself causes an outage. It restores ownership to the runtime role. NOTE:
--  * If migration 20261012_sod_db_enforcement has ALREADY been applied, roll the MIGRATION back
--    first (alembic downgrade 20261009_iqvia_affil_org_indexes, via the governed pipeline). After
--    returning ownership to docuaction_app the guards would be removable by the runtime role.
--  * The ORIGINAL ACL cannot be reconstructed from this file. Re-create it from the PRE-CHECK 1.1
--    (relacl) and 1.4 output you saved. The statements below are the shape; fill in the saved
--    values. Unknown original state means: do not guess, escalate.
-- BEGIN;
--   ALTER TABLE public.review_decision_events OWNER TO docuaction_app;     -- the pre-APPLY owner you recorded
--   ALTER FUNCTION public.review_event_enforce_sod() OWNER TO docuaction_app;   -- the pre-APPLY owner you recorded
--   -- (if the guard function exists:) ALTER FUNCTION public.review_event_append_only_guard() OWNER TO <recorded owner>;
--   -- ownership transfer to a role gives it full privileges implicitly; restore any extra grants from the saved relacl:
--   -- GRANT <privileges recorded in 1.1> ON TABLE public.review_decision_events TO <grantee>;
--   -- sequences (only if PRE-CHECK 1.9 listed any): ALTER SEQUENCE <name> OWNER TO <recorded owner>;
-- COMMIT;
