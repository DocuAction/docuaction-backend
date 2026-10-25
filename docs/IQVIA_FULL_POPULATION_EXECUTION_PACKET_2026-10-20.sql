-- IQVIA full-population September comparison -- THE ONE EXECUTION PACKET.
-- Prepared 2026-10-20. NOT RUN by the agent. Operator decision + action only.
--
-- Scope: compares every one of the 24,589 real September delivery records
-- (intake 4417b334-7440-4c30-9afa-05f4268f17c7, job 0930826c -- see
-- qa-evidence/2026-10-13-delivery-count-clarity/75_SEPTEMBER_POPULATION_RECONCILIATION_AND_B1B4_METHODOLOGY_2026-10-20.md
-- for the file-hash evidence this count is reconciled against) against the
-- currently APPROVED IQVIA_HCO snapshot already in the database, using each
-- record's OWN submitted identifier (rce_source_records.npi / parsed->>'CCN'),
-- not a promoted registry entity -- so a record that was never promoted
-- (e.g. still HELD) is still accounted for, exactly as the brief requires.
--
-- WRITES NOTHING TO THE DATABASE. Zero rows inserted, updated or deleted
-- anywhere -- stronger than "append-only evidence rows": this version keeps
-- all evidence in the three exported CSV files below, not in a new table,
-- so no migration and no new shared-database write path is needed at all.
--
-- SAFETY
--   * SELECT-only. No INSERT/UPDATE/DELETE/CREATE anywhere in this file.
--     (An earlier draft used a `CREATE TEMP TABLE AS` to avoid repeating the
--     comparison query three times. That was tested against a disposable
--     Postgres before being sent anywhere and found to fail: Postgres
--     refuses even `CREATE TEMP TABLE AS` inside `SET TRANSACTION READ
--     ONLY` -- "cannot execute CREATE TABLE AS in a read-only transaction".
--     This version repeats the plain SELECT three times instead, so every
--     statement in this file is actually a SELECT, not just labelled one.)
--   * Wrapped in BEGIN; SET TRANSACTION READ ONLY; ... ROLLBACK;
--   * Three \copy ... TO exports write to LOCAL FILES on the machine running
--     psql, not to the database.
--   * Does not touch ENABLE_IQVIA_AFFILIATION_CONSUMPTION or any other flag.
--
-- VALIDATED END TO END before being sent anywhere: run against a disposable
-- ephemeral Postgres 18 instance seeded with a synthetic intake under this
-- exact real intake id, five synthetic records (one per outcome bucket:
-- MATCHED, MISSING_IDENTIFIER, NOT_FOUND, PROCESSING_ERROR, AMBIGUOUS) and a
-- real approved IQVIA_HCO snapshot built through the actual import/approve
-- pipeline (`iqvia_import.import_hco_csv` + `source_matching.approve_
-- snapshot`, not hand-inserted rows). Result: reconciliation row showed
-- total_records=5, matched=1, missing_identifier=1, not_found=1,
-- ambiguous=1, processing_error=1, and the last column
-- (bucket_total_minus_total_records) = 0. All three \copy exports produced
-- the expected row counts and content. Entirely synthetic data; this proves
-- the SQL runs correctly, not any fact about the real September population.
--
-- METHODOLOGY -- mirrors app/tefca_registry/rce/iqvia_batch_comparison.py's
-- already-tested 5-outcome taxonomy exactly (see that module and
-- tests/test_iqvia_batch_comparison_2026_10_19.py, 12/12 passing on a
-- disposable database), translated into SQL over the real per-record
-- population instead of the per-registry-entity population that module's
-- Python tests exercise:
--   MATCHED            -- record's NPI is well-formed and held by exactly
--                        one approved IQVIA_HCO observation.
--   MISSING_IDENTIFIER -- record has neither a usable NPI nor CCN.
--   NOT_FOUND          -- a well-formed identifier is present but zero
--                        approved IQVIA observations carry it.
--   AMBIGUOUS          -- NPI matches >1 approved observation, or the only
--                        evidence is a CCN at all (CCN is CANDIDATE-only,
--                        per evaluate_ccn_candidate -- never automatic).
--   PROCESSING_ERROR   -- NPI is present but not well-formed. NOTE: this SQL
--                        uses a BASIC FORMAT CHECK (10 digits), not the full
--                        Luhn check-digit algorithm `validate_npi()` applies
--                        in application code -- a known, deliberate
--                        simplification for a one-shot SQL benchmark/export,
--                        not a claim of full parity with the tested Python
--                        validator. ORG_ALL_NPI is not parsed because it does
--                        not exist in the real delivered files (doc 75 S2) --
--                        only the one submitted NPI per record is read.
--
-- HOW TO RUN (operator, Azure Cloud Shell or equivalent already-firewalled
-- host -- the agent has no DB access and did not request or add a firewall
-- rule):
--   psql "$DEV_DATABASE_URL" -f IQVIA_FULL_POPULATION_EXECUTION_PACKET_2026-10-20.sql
-- Before running, replace EVERY <ROOT_SNAPSHOT_ID> placeholder with the UUID
-- the step-1 query below prints -- there are FIVE occurrences (Step 2;
-- Export 1; Export 2 twice -- the obs CTE and the JOIN condition; Export 3).
-- Use a one-time find/replace on the whole file (grep -c "'<ROOT_SNAPSHOT_ID>'::uuid"
-- must print 0 afterwards); a hand replacement that stops early aborts the
-- single transaction at the first unreplaced placeholder cast.
-- Report back: the reconciliation row (must show total_records = 24589,
-- and the last column = 0), and the three CSV files' row counts.
--
-- RECOVERY / RESUME: trivially re-runnable at will. Because this writes
-- nothing to the database, there is no partial-run state to resume from --
-- a second run from scratch produces the identical result (barring a change
-- to the approved snapshot or the source records themselves, neither of
-- which this script can cause).

\timing on

BEGIN;
SET TRANSACTION READ ONLY;

-- ── Step 0: the exact September intake ──────────────────────────────────────
SELECT id, delivery_label, original_filename, sha256, created_at
FROM rce_source_intakes
WHERE id = '4417b334-7440-4c30-9afa-05f4268f17c7'::uuid;

-- ── Step 1: the approved IQVIA_HCO snapshot and its observation root ───────
WITH RECURSIVE approved AS (
    SELECT *
    FROM source_snapshot s
    WHERE s.source_system = 'IQVIA_HCO'
      AND s.status = 'APPROVED'
      AND s.id NOT IN (
          SELECT supersedes_snapshot_id FROM source_snapshot
          WHERE supersedes_snapshot_id IS NOT NULL)
    ORDER BY s.received_at DESC, s.created_at DESC
    LIMIT 1
),
root_walk AS (
    SELECT a.id AS current_id, a.supersedes_snapshot_id AS prev_id, a.id AS approved_id
    FROM approved a
    UNION ALL
    SELECT s.id, s.supersedes_snapshot_id, rw.approved_id
    FROM source_snapshot s
    JOIN root_walk rw ON s.id = rw.prev_id
)
SELECT approved_id AS approved_snapshot_id, current_id AS observation_root_snapshot_id
FROM root_walk WHERE prev_id IS NULL;
-- ^ Replace every <ROOT_SNAPSHOT_ID> below with observation_root_snapshot_id.

-- ── Step 2: the reconciliation check -- MUST show the last column = 0 ─────
WITH obs AS (
    SELECT npi, ccn FROM iqvia_hco_observation
    WHERE source_snapshot_id = '<ROOT_SNAPSHOT_ID>'::uuid
),
obs_npi_counts AS (
    SELECT npi, count(*) AS n FROM obs WHERE npi IS NOT NULL AND npi <> '' GROUP BY npi
),
obs_ccn_counts AS (
    SELECT ccn, count(*) AS n FROM obs WHERE ccn IS NOT NULL AND ccn <> '' GROUP BY ccn
),
records AS (
    SELECT r.id AS source_record_id, r.line_number, r.source_rce_id,
           r.tefcaid, r.hcid, r.canonical_entity_id,
           trim(r.npi)              AS npi,
           trim(r.parsed ->> 'CCN') AS ccn
    FROM rce_source_records r
    WHERE r.source_intake_id = '4417b334-7440-4c30-9afa-05f4268f17c7'::uuid
),
classified AS (
    SELECT
        rec.*,
        CASE
            WHEN (rec.npi IS NULL OR rec.npi = '') AND (rec.ccn IS NULL OR rec.ccn = '')
                THEN 'MISSING_IDENTIFIER'
            WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND rec.npi !~ '^[0-9]{10}$'
                THEN 'PROCESSING_ERROR'
            WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 1
                THEN 'MATCHED'
            WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) > 1
                THEN 'AMBIGUOUS'
            WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 0
                 AND (rec.ccn IS NULL OR rec.ccn = '')
                THEN 'NOT_FOUND'
            WHEN rec.ccn IS NOT NULL AND rec.ccn <> '' AND COALESCE(occ.n, 0) >= 1
                THEN 'AMBIGUOUS'
            ELSE 'NOT_FOUND'
        END AS outcome
    FROM records rec
    LEFT JOIN obs_npi_counts onc ON onc.npi = rec.npi
    LEFT JOIN obs_ccn_counts occ ON occ.ccn = rec.ccn
)
SELECT
    count(*)                                              AS total_records,
    count(*) FILTER (WHERE outcome = 'MATCHED')            AS matched,
    count(*) FILTER (WHERE outcome = 'MISSING_IDENTIFIER') AS missing_identifier,
    count(*) FILTER (WHERE outcome = 'NOT_FOUND')           AS not_found,
    count(*) FILTER (WHERE outcome = 'AMBIGUOUS')           AS ambiguous,
    count(*) FILTER (WHERE outcome = 'PROCESSING_ERROR')   AS processing_error,
    count(*) - (
        count(*) FILTER (WHERE outcome = 'MATCHED')
      + count(*) FILTER (WHERE outcome = 'MISSING_IDENTIFIER')
      + count(*) FILTER (WHERE outcome = 'NOT_FOUND')
      + count(*) FILTER (WHERE outcome = 'AMBIGUOUS')
      + count(*) FILTER (WHERE outcome = 'PROCESSING_ERROR')
    ) AS bucket_total_minus_total_records_MUST_BE_ZERO
FROM classified;

-- ── Export 1: full-population result CSV -- one row per September record ──
\copy (WITH obs AS (SELECT npi, ccn FROM iqvia_hco_observation WHERE source_snapshot_id = '<ROOT_SNAPSHOT_ID>'::uuid), obs_npi_counts AS (SELECT npi, count(*) AS n FROM obs WHERE npi IS NOT NULL AND npi <> '' GROUP BY npi), obs_ccn_counts AS (SELECT ccn, count(*) AS n FROM obs WHERE ccn IS NOT NULL AND ccn <> '' GROUP BY ccn), records AS (SELECT r.id AS source_record_id, r.line_number, r.source_rce_id, r.tefcaid, r.hcid, r.canonical_entity_id, trim(r.npi) AS npi, trim(r.parsed ->> 'CCN') AS ccn FROM rce_source_records r WHERE r.source_intake_id = '4417b334-7440-4c30-9afa-05f4268f17c7'::uuid) SELECT rec.source_record_id, rec.line_number, rec.source_rce_id, rec.tefcaid, rec.hcid, rec.canonical_entity_id, rec.npi, rec.ccn, CASE WHEN (rec.npi IS NULL OR rec.npi = '') AND (rec.ccn IS NULL OR rec.ccn = '') THEN 'MISSING_IDENTIFIER' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND rec.npi !~ '^[0-9]{10}$' THEN 'PROCESSING_ERROR' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 1 THEN 'MATCHED' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) > 1 THEN 'AMBIGUOUS' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 0 AND (rec.ccn IS NULL OR rec.ccn = '') THEN 'NOT_FOUND' WHEN rec.ccn IS NOT NULL AND rec.ccn <> '' AND COALESCE(occ.n, 0) >= 1 THEN 'AMBIGUOUS' ELSE 'NOT_FOUND' END AS outcome, COALESCE(onc.n, 0) AS npi_hit_count, COALESCE(occ.n, 0) AS ccn_hit_count FROM records rec LEFT JOIN obs_npi_counts onc ON onc.npi = rec.npi LEFT JOIN obs_ccn_counts occ ON occ.ccn = rec.ccn ORDER BY rec.line_number) TO 'september_iqvia_full_population_result_2026-10-20.csv' WITH CSV HEADER

-- ── Export 2: linked detail CSV -- one row per AMBIGUOUS record's actual IQVIA candidates ──
-- (one-to-many preserved here, not collapsed into the population-count CSV above)
\copy (WITH obs AS (SELECT npi, ccn FROM iqvia_hco_observation WHERE source_snapshot_id = '<ROOT_SNAPSHOT_ID>'::uuid), obs_npi_counts AS (SELECT npi, count(*) AS n FROM obs WHERE npi IS NOT NULL AND npi <> '' GROUP BY npi), obs_ccn_counts AS (SELECT ccn, count(*) AS n FROM obs WHERE ccn IS NOT NULL AND ccn <> '' GROUP BY ccn), records AS (SELECT r.id AS source_record_id, r.line_number, trim(r.npi) AS npi, trim(r.parsed ->> 'CCN') AS ccn FROM rce_source_records r WHERE r.source_intake_id = '4417b334-7440-4c30-9afa-05f4268f17c7'::uuid), classified AS (SELECT rec.*, CASE WHEN (rec.npi IS NULL OR rec.npi = '') AND (rec.ccn IS NULL OR rec.ccn = '') THEN 'MISSING_IDENTIFIER' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND rec.npi !~ '^[0-9]{10}$' THEN 'PROCESSING_ERROR' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 1 THEN 'MATCHED' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) > 1 THEN 'AMBIGUOUS' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 0 AND (rec.ccn IS NULL OR rec.ccn = '') THEN 'NOT_FOUND' WHEN rec.ccn IS NOT NULL AND rec.ccn <> '' AND COALESCE(occ.n, 0) >= 1 THEN 'AMBIGUOUS' ELSE 'NOT_FOUND' END AS outcome FROM records rec LEFT JOIN obs_npi_counts onc ON onc.npi = rec.npi LEFT JOIN obs_ccn_counts occ ON occ.ccn = rec.ccn) SELECT c.source_record_id, c.line_number, c.npi, c.ccn, o.npi AS iqvia_npi, o.ccn AS iqvia_ccn, o.source_record_key AS iqvia_source_record_key FROM classified c JOIN iqvia_hco_observation o ON (o.source_snapshot_id = '<ROOT_SNAPSHOT_ID>'::uuid AND ((c.npi IS NOT NULL AND c.npi <> '' AND o.npi = c.npi) OR (c.ccn IS NOT NULL AND c.ccn <> '' AND o.ccn = c.ccn))) WHERE c.outcome = 'AMBIGUOUS' ORDER BY c.line_number) TO 'september_iqvia_ambiguous_candidates_detail_2026-10-20.csv' WITH CSV HEADER

-- ── Export 3: Nabeel exception CSV -- the actionable subset only ──
\copy (SELECT * FROM (WITH obs AS (SELECT npi, ccn FROM iqvia_hco_observation WHERE source_snapshot_id = '<ROOT_SNAPSHOT_ID>'::uuid), obs_npi_counts AS (SELECT npi, count(*) AS n FROM obs WHERE npi IS NOT NULL AND npi <> '' GROUP BY npi), obs_ccn_counts AS (SELECT ccn, count(*) AS n FROM obs WHERE ccn IS NOT NULL AND ccn <> '' GROUP BY ccn), records AS (SELECT r.id AS source_record_id, r.line_number, r.source_rce_id, r.tefcaid, r.hcid, trim(r.npi) AS npi, trim(r.parsed ->> 'CCN') AS ccn FROM rce_source_records r WHERE r.source_intake_id = '4417b334-7440-4c30-9afa-05f4268f17c7'::uuid) SELECT rec.source_record_id, rec.line_number, rec.source_rce_id, rec.tefcaid, rec.hcid, rec.npi, rec.ccn, CASE WHEN (rec.npi IS NULL OR rec.npi = '') AND (rec.ccn IS NULL OR rec.ccn = '') THEN 'MISSING_IDENTIFIER' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND rec.npi !~ '^[0-9]{10}$' THEN 'PROCESSING_ERROR' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 1 THEN 'MATCHED' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) > 1 THEN 'AMBIGUOUS' WHEN rec.npi IS NOT NULL AND rec.npi <> '' AND COALESCE(onc.n, 0) = 0 AND (rec.ccn IS NULL OR rec.ccn = '') THEN 'NOT_FOUND' WHEN rec.ccn IS NOT NULL AND rec.ccn <> '' AND COALESCE(occ.n, 0) >= 1 THEN 'AMBIGUOUS' ELSE 'NOT_FOUND' END AS outcome, COALESCE(onc.n, 0) AS npi_hit_count, COALESCE(occ.n, 0) AS ccn_hit_count FROM records rec LEFT JOIN obs_npi_counts onc ON onc.npi = rec.npi LEFT JOIN obs_ccn_counts occ ON occ.ccn = rec.ccn) sub WHERE sub.outcome IN ('NOT_FOUND','AMBIGUOUS','PROCESSING_ERROR') ORDER BY sub.line_number) TO 'september_iqvia_exceptions_for_nabeel_2026-10-20.csv' WITH CSV HEADER

ROLLBACK;
