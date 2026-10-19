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
       current_id  AS observation_root_snapshot_id
FROM root_walk
WHERE prev_id IS NULL;
-- ^ Note this value (observation_root_snapshot_id) -- it is substituted as
--   :root_id below. psql does not carry a result into a later \set
--   automatically across a plain script; if running interactively, copy the
--   UUID printed above into the two placeholders marked <ROOT_SNAPSHOT_ID>
--   before running the rest of this file.

-- ── Step 2: bounded, representative sample of September review records ──
-- LIMIT 1000, not all 24,589 -- this is a timing sample, not the real run.
-- Ordered by created_at so the sample is a contiguous, representative slice
-- rather than an arbitrary unordered LIMIT.
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT rr.id, rr.entity_id
FROM review_records rr
WHERE rr.entity_id IS NOT NULL
ORDER BY rr.created_at
LIMIT 1000;

-- ── Step 3: side A -- our registry identifiers for that sample, ONE batch query ──
-- (the application code chunks this at 500-5,000 entity_ids per call; here,
-- for a single-shot benchmark, it is run as one query over the same 1,000
-- entity ids captured by step 2's WHERE/LIMIT shape.)
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT tei.entity_id, tei.identifier_type, tei.identifier_value
FROM tefca_entity_identifiers tei
WHERE tei.identifier_type IN ('npi', 'ccn')
  AND tei.entity_id IN (
      SELECT rr.entity_id
      FROM review_records rr
      WHERE rr.entity_id IS NOT NULL
      ORDER BY rr.created_at
      LIMIT 1000
  );

-- ── Step 4: side B -- the full approved IQVIA_HCO observation set, ONE query ──
-- Replace <ROOT_SNAPSHOT_ID> with the UUID step 1 printed before running.
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT npi, ccn
FROM iqvia_hco_observation
WHERE source_snapshot_id = '<ROOT_SNAPSHOT_ID>'::uuid;

-- ── Step 5: how many September records total, and how many lack any NPI/CCN ──
-- Read-only population characterization (answers doc 74's open question:
-- "how many of the 24,589 records lack a usable identifier"). Adjust the
-- WHERE clause to the real September delivery's actual scoping (date range
-- or intake id) before running -- the placeholder below assumes review
-- records are the right population; the operator should confirm against
-- the known September intake id from the delivery records rather than
-- trusting an unscoped count, which would include non-September reviews.
SELECT
    count(*)                                                        AS total_review_records,
    count(*) FILTER (WHERE entity_id IS NULL)                       AS no_entity_at_all,
    count(*) FILTER (
        WHERE entity_id IS NOT NULL AND entity_id NOT IN (
            SELECT entity_id FROM tefca_entity_identifiers
            WHERE identifier_type IN ('npi', 'ccn'))
    )                                                                AS entity_present_no_npi_or_ccn
FROM review_records;

ROLLBACK;
