-- IQVIA full-population matching -- bounded, read-only Cloud Shell benchmark.
-- Prepared 2026-10-19. NOT RUN by the agent. Operator action only.
--
-- Purpose: measure real elapsed time for the two queries the batch
-- comparison tool (app/tefca_registry/rce/iqvia_batch_comparison.py) issues,
-- against the real DEV database, at a bounded sample size -- without writing
-- anything and without requiring ENABLE_IQVIA_AFFILIATION_CONSUMPTION.
--
-- SAFETY
--   * SELECT-only. No INSERT/UPDATE/DELETE anywhere in this file.
--   * Wrapped in an explicit read-only transaction as a belt-and-suspenders
--     safeguard, even though SELECT-only SQL does not strictly need it.
--   * Ends with ROLLBACK, not COMMIT, so even a mistaken future edit that
--     added a write would not persist.
--   * Reads only: review_records, tefca_entity_identifiers, source_snapshot,
--     iqvia_hco_observation. Writes to none of them.
--
-- HOW TO RUN (operator, in Azure Cloud Shell, connected to the real DEV
-- Postgres -- this is the one remaining action; the agent does not have
-- DB access and did not request or add a firewall rule):
--   psql "$DEV_DATABASE_URL" -f iqvia_batch_comparison_benchmark_2026-10-19.sql
-- or paste the body below directly into an interactive psql session.
-- Report back: the two \timing lines ("Time: N.NNN ms") and the two
-- EXPLAIN ANALYZE blocks' "Execution Time" lines.

\timing on

BEGIN;
SET TRANSACTION READ ONLY;

-- ── Step 1: identify the approved IQVIA_HCO snapshot and its observation root ──
-- Mirrors source_matching.current_approved_snapshot() + iqvia_match.py's
-- _observation_root_snapshot_id() walk, in SQL. Read-only.
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
SELECT approved_id AS approved_snapshot_id,
       current_id  AS root_snapshot_id
FROM root_walk
WHERE prev_id IS NULL
\gset
-- ^ \gset stores the one-row result as psql variables, so steps 4/4b read
--   :'root_snapshot_id' directly and the file runs end to end without a
--   manual pause or placeholder edit. Run with -v ON_ERROR_STOP=1 so an
--   aborted step returns a non-zero exit status instead of a misleading
--   step-5 result. (Two-phase alternative: run step 1 alone, then
--   psql -v root_snapshot_id=<uuid> -f <this file>.)
\echo approved_snapshot_id = :'approved_snapshot_id'  root_snapshot_id = :'root_snapshot_id'

-- The September population, by the application's own linkage (curated record
-- -> canonical entity), so every timing below describes THE intended
-- population and not the earliest 1,000 review records of any delivery.
\set september_intake '4417b334-7440-4c30-9afa-05f4268f17c7'

-- ── Step 2: bounded, representative sample of September review records ──
-- LIMIT 1000, not all 24,589 -- this is a timing sample, not the real run.
-- Ordered by created_at so the sample is a contiguous, representative slice
-- rather than an arbitrary unordered LIMIT.
-- Scoped to the September delivery through rce_curated_records.canonical_entity_id
-- (the same linkage the application uses), not to review records of any delivery.
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT c.id, c.canonical_entity_id
FROM rce_curated_records c
WHERE c.source_intake_id = :'september_intake'::uuid
  AND c.canonical_entity_id IS NOT NULL
ORDER BY c.created_at, c.id
LIMIT 1000;

-- ── Step 3: side A -- our registry identifiers for that sample, ONE batch query ──
-- (the application code chunks this at 500-5,000 entity_ids per call; here,
-- for a single-shot benchmark, it is run as one query over the same 1,000
-- September entity ids captured by step 2's WHERE/LIMIT shape.)
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT tei.entity_id, tei.identifier_type, tei.identifier_value
FROM tefca_entity_identifiers tei
WHERE tei.identifier_type IN ('npi', 'ccn')
  AND tei.entity_id IN (
      SELECT c.canonical_entity_id
      FROM rce_curated_records c
      WHERE c.source_intake_id = :'september_intake'::uuid
        AND c.canonical_entity_id IS NOT NULL
      ORDER BY c.created_at, c.id
      LIMIT 1000
  );

-- ── Step 4: side B -- the full approved IQVIA_HCO observation set, ONE query ──
-- (a) the server-side plan only: EXPLAIN ANALYZE does not transfer rows to the client.
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT npi, ccn
FROM iqvia_hco_observation
WHERE source_snapshot_id = :'root_snapshot_id'::uuid;

-- ── Step 4b: side B -- the ACTUAL fetch the application performs (.all()) ──
-- Rows are transferred and discarded (\o /dev/null) so the \timing line below
-- measures plan + execution + row transfer + client materialisation, which is
-- what a full run pays. Report 4 (plan) and 4b (fetch) as two separate numbers.
\timing on
\o /dev/null
SELECT npi, ccn
FROM iqvia_hco_observation
WHERE source_snapshot_id = :'root_snapshot_id'::uuid;
\o
\timing off

-- ── Step 5: how many September records total, and how many lack any NPI/CCN ──
-- Read-only population characterization (answers doc 74's open question:
-- "how many of the 24,589 records lack a usable identifier"). Adjust the
-- WHERE clause to the real September delivery's actual scoping (date range
-- or intake id) before running -- the placeholder below assumes review
-- records are the right population; the operator should confirm against
-- the known September intake id from the delivery records rather than
-- trusting an unscoped count, which would include non-September reviews.
-- Scoped to the September delivery's curated records (one row per source
-- record); blank/whitespace identifier rows do not count as identifiers, which
-- is the comparison code's own definition (it trims and skips empties).
SELECT
    count(*)                                                        AS total_september_records,
    count(*) FILTER (WHERE canonical_entity_id IS NULL)             AS no_entity_at_all,
    count(*) FILTER (
        WHERE canonical_entity_id IS NOT NULL AND canonical_entity_id NOT IN (
            SELECT entity_id FROM tefca_entity_identifiers
            WHERE identifier_type IN ('npi', 'ccn')
              AND NULLIF(BTRIM(identifier_value), '') IS NOT NULL)
    )                                                                AS entity_present_no_npi_or_ccn
FROM rce_curated_records
WHERE source_intake_id = :'september_intake'::uuid;

ROLLBACK;
