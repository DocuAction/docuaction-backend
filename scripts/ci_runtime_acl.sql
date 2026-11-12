-- ci_runtime_acl.sql — apply the INTENDED production least-privilege runtime ACL
-- for the isolated integration harness, per docs/prod_database_baseline_design.md §4.
--
-- Run as a DB superuser AFTER `alembic upgrade head` (owner) and AFTER seeding
-- (owner), and BEFORE starting the application server as docuaction_app.
--
-- Model (NOT a blanket grant):
--   * docuaction_app is the runtime LOGIN role. It OWNS every ordinary table, so
--     startup create_all()/ALTER and all runtime DML work as the product expects.
--   * docuaction_owner KEEPS ownership of the immutable set — the four Area-1
--     tables plus the append-only / SoD tables — so those stay immutable to the
--     runtime role. On them docuaction_app gets only what the product uses:
--     SELECT, INSERT, and the one designed column-level UPDATE on the promotion
--     markers. No table-wide UPDATE / DELETE / TRUNCATE.
--   * There is deliberately no role membership between the two roles, so SET ROLE
--     cannot bypass the restriction.
-- This is the real intended ACL, not privileges widened to make the harness pass:
-- Area-1 UPDATE/DELETE/TRUNCATE remain denied to the runtime role.

\set ON_ERROR_STOP on

DO $acl$
DECLARE
  -- The immutable set kept under docuaction_owner. Area-1 (column-revoke model)
  -- plus the append-only / SoD-triggered tables (owner must own them so their
  -- triggers cannot be disabled by the role they constrain). Optional members
  -- are skipped when a build predates them.
  retained text[] := ARRAY[
    'rce_source_records','rce_source_intakes','rce_ingestion_runs',
    'rce_rule_execution_history','review_decision_events','iqvia_advisory_assessment',
    -- 20261108_iqvia_advisory_outcome (D-10): append-only trigger + app
    -- SELECT/INSERT only -- owner must own it or the constrained role could
    -- disable its own trigger (same contract as iqvia_advisory_assessment).
    'iqvia_advisory_comparison_outcome',
    -- 20261112_rce_record_comments: append-only trigger + app SELECT/INSERT only
    -- -- owner must own it or the runtime role could drop its own trigger.
    'rce_record_comments'];
  r record;
  owning_tbl text;
BEGIN
  -- 1. Every NON-retained table -> owned by the runtime role. Views/matviews go
  --    to the runtime role too (a view reads its base tables with the VIEW
  --    owner's rights, so the runtime role must own the views it queries; it
  --    still only has SELECT on any retained base table underneath).
  FOR r IN SELECT c.relname, c.relkind
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m')
              AND NOT (c.relkind IN ('r','p') AND c.relname = ANY(retained)) LOOP
    IF r.relkind IN ('r','p') THEN
      EXECUTE format('ALTER TABLE public.%I OWNER TO docuaction_app', r.relname);
    ELSIF r.relkind = 'v' THEN
      EXECUTE format('ALTER VIEW public.%I OWNER TO docuaction_app', r.relname);
    ELSIF r.relkind = 'm' THEN
      EXECUTE format('ALTER MATERIALIZED VIEW public.%I OWNER TO docuaction_app', r.relname);
    END IF;
  END LOOP;

  -- 2. Sequences tied to a non-retained table (or to none) -> owned by the
  --    runtime role; sequences of retained tables stay with the owner, with
  --    USAGE,SELECT to the runtime role for INSERTs.
  FOR r IN SELECT c.oid, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind='S' AND n.nspname='public' LOOP
    SELECT regexp_replace(dep.refobjid::regclass::text, '^public\.', '')
      INTO owning_tbl
      FROM pg_depend dep
     WHERE dep.objid=r.oid AND dep.classid='pg_class'::regclass AND dep.deptype IN ('a','i')
     LIMIT 1;
    IF owning_tbl IS NOT NULL AND owning_tbl = ANY(retained) THEN
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE public.%I TO docuaction_app', r.relname);
    ELSE
      EXECUTE format('ALTER SEQUENCE public.%I OWNER TO docuaction_app', r.relname);
    END IF;
  END LOOP;

  -- 3. Retained (immutable) tables: owner keeps ownership; the runtime role gets
  --    exactly SELECT, INSERT (+ the designed promotion-column UPDATE).
  FOR r IN SELECT unnest(retained) AS t LOOP
    IF to_regclass('public.'||r.t) IS NOT NULL THEN
      EXECUTE format('GRANT SELECT, INSERT ON public.%I TO docuaction_app', r.t);
    END IF;
  END LOOP;
  IF to_regclass('public.rce_source_records') IS NOT NULL THEN
    EXECUTE 'GRANT UPDATE (promotion_status, canonical_entity_id) ON public.rce_source_records TO docuaction_app';
  END IF;
END
$acl$;

-- 4. Schema: the runtime role needs USAGE + CREATE so startup create_all() can
--    materialise any non-migration table (e.g. audio_files) and own it (§4).
GRANT USAGE, CREATE ON SCHEMA public TO docuaction_app;
GRANT USAGE ON SCHEMA public TO docuaction_owner;
