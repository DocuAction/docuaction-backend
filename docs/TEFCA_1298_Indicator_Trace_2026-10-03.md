# The "1,298 Failed" indicator — trace, persisted fields, and the governed read-only query

Status: **UNRESOLVED, BLOCKED on a governed DEV read-only diagnostic.** Nothing below was run
against any DEV/PROD database. The SQL is written to be executed by an operator through the
governed channel; it selects aggregates only (no NPI, name, address or payload values).

Inputs re-read for this trace: `qa-evidence/2026-10-01-reporting-architecture/CHECKPOINT-1298-FAILED-INDICATOR-STATIC-ANALYSIS.md`
(2026-10-01), `app/tefca_registry/rce/verification_coverage.py` (`_COVERAGE_COUNTS_SQL`,
`_VERIFICATION_STATUS`, `_DIMENSION_DISPOSITION`), `app/tefca_registry/rce/verification_drilldown.py`
(`_OUTCOME_EXPOSURE_BLOCKED = "failed"`, `UnprovenOutcomeRefused`), and `app/tefca_registry/rce/arc_pipeline.py`
(`_DISPOSITION_TO_STATE`). Target population (from the checkpoint): job
`0930826c-970e-419d-ab8d-f05bb4f99116`, intake `4417b334-7440-4c30-9afa-05f4268f17c7`;
spec figures 24,589 total / 24,506 eligible / 24,502 attempted / 19,156 Verified / 5,346 Not Found /
displayed **1,298** Failed.

## 1. What "failed" means in code today — and what it does not

`verification_coverage.py` is the only producer of a `failed` outcome, and it is **per source**
(nppes / pecos / leie / sam), never a delivery-wide bucket:

| Evidence table | Column | Values mapped to `failed` |
|---|---|---|
| `tefca_verifications` | `verification_status` | `failed`, `error` |
| `tefca_dimension_evidence` | `disposition` | `FAIL`, `CONFLICT` |

Each source's counts are `count(DISTINCT entity_id) FILTER (WHERE outcome = …)` over the union of
both tables, restricted to the intake's promoted population (`rce_curated_records.canonical_entity_id`).
Within one source the outcomes are not mutually exclusive across rows: an entity can have a
`PASS` row and a `CONFLICT` row for the same source (two generations, or two dimensions), and is then
counted under **both** `verified` and `failed`. Across sources nothing is summed. No code path
produces a single cross-source "Failed" total; the dashboard shows one block per source.

Two facts that bound the explanation space without a live read:

- 19,156 + 5,346 = 24,502 = attempted. The spec's own Verified and Not Found already exhaust the
  attempted population, so 1,298 cannot be a disjoint third outcome on the same source and axis.
- `_DISPOSITION_TO_STATE["CONFLICT"] = "not_found"` on the classifier side while
  `_DIMENSION_DISPOSITION["CONFLICT"] = "failed"` on the dashboard side — the same disposition is
  read as "not found" by one consumer and "failed" by the other. `CONFLICT` is also the ADDRESS
  dimension's ordinary "addresses differ" result (`address_evidence.AddressComparison.CONFLICT`),
  which the classifier deliberately keeps out of source states (`_SOURCE_STATE_DIMENSIONS`
  excludes ADDRESS) but the coverage SQL does **not** exclude (it reads every dimension's rows for
  the source). An address disagreement therefore counts the entity as `failed` for NPPES on the
  dashboard while the classifier never saw a failure. Whether that is the 1,298 is exactly what the
  query below answers; it is **not** inferred from the label.

The `failed` outcome stays blocked from list/CSV export (`verification_drilldown._OUTCOME_EXPOSURE_BLOCKED`
raises `UnprovenOutcomeRefused` before any query runs) until the query has been run and the
definition reconciled. This document does not change that.

## 2. The persisted fields the governed query needs (and nothing else)

| Table | Fields read | Purpose |
|---|---|---|
| `rce_source_intakes` | `id` | anchor |
| `rce_source_records` | `source_intake_id` | total delivered records (#1) |
| `rce_curated_records` | `source_intake_id`, `canonical_entity_id` | eligible population (#2) |
| `tefca_verifications` | `entity_id`, `source`, `verification_status`, `detail` (LIKE '%deactivat%' only) | per-source outcomes, table A |
| `tefca_dimension_evidence` | `entity_id` (text), `source`, `evidence_dimension`, `disposition`, `generation_timestamp` | per-source outcomes, table B; dimension split |
| `rce_delivery_jobs` | `id`, `source_intake_id`, `state`, `stage` | confirms the job/intake pair (#0) |

No `original_values`, `normalized_values`, `field_conflicts`, names, identifiers or notes are selected.

## 3. The SQL — aggregate-only, read-only, bounded

Run as one session: `BEGIN; SET TRANSACTION READ ONLY; SET LOCAL statement_timeout = '20s';` …
`ROLLBACK;`. Replace `:intake` with `4417b334-7440-4c30-9afa-05f4268f17c7`.

```sql
-- #0 job/intake sanity
SELECT id AS job_id, source_intake_id, state, stage
FROM rce_delivery_jobs WHERE source_intake_id = CAST(:intake AS uuid);

WITH pop_uuid AS (
    SELECT DISTINCT canonical_entity_id AS eid
    FROM rce_curated_records
    WHERE source_intake_id = CAST(:intake AS uuid) AND canonical_entity_id IS NOT NULL),
pop_text AS (SELECT CAST(eid AS text) AS eid FROM pop_uuid),
src AS (  -- the same spelling map verification_coverage.py uses
    SELECT * FROM (VALUES ('nppes','nppes'),('npi_registry','nppes'),('cms_nppes','nppes'),
                          ('pecos','pecos'),
                          ('leie','leie'),('oig_leie','leie'),('oig','leie'),('oig-leie','leie'),
                          ('sam','sam'),('sam_gov','sam'),('sam.gov','sam'),('samgov','sam')) v(raw, key)),
rows_a AS (  -- tefca_verifications
    SELECT s.key, v.entity_id::text AS eid, 'tefca_verifications' AS tbl, NULL::text AS dim,
           CASE WHEN lower(coalesce(v.detail,'')) LIKE '%deactivat%' THEN 'deactivated'
                ELSE CASE lower(btrim(coalesce(v.verification_status,'')))
                       WHEN 'verified' THEN 'verified' WHEN 'match' THEN 'verified' WHEN 'matched' THEN 'verified'
                       WHEN 'not_found' THEN 'not_found' WHEN 'no_match' THEN 'not_found'
                       WHEN 'unavailable' THEN 'unavailable' WHEN 'source_unavailable' THEN 'unavailable'
                       WHEN 'failed' THEN 'failed' WHEN 'error' THEN 'failed'
                       WHEN 'deactivated' THEN 'deactivated' END END AS outcome,
           lower(btrim(coalesce(v.verification_status,''))) AS raw_status
    FROM tefca_verifications v JOIN src s ON s.raw = lower(btrim(v.source))
    WHERE v.entity_id IN (SELECT eid FROM pop_uuid)),
rows_b AS (  -- tefca_dimension_evidence
    SELECT s.key, d.entity_id AS eid, 'tefca_dimension_evidence' AS tbl, d.evidence_dimension AS dim,
           CASE upper(btrim(coalesce(d.disposition,'')))
                WHEN 'PASS' THEN 'verified' WHEN 'CORROBORATED' THEN 'verified'
                WHEN 'NOT_FOUND' THEN 'not_found' WHEN 'REVIEW' THEN 'not_found'
                WHEN 'UNAVAILABLE' THEN 'unavailable'
                WHEN 'FAIL' THEN 'failed' WHEN 'CONFLICT' THEN 'failed' END AS outcome,
           upper(btrim(coalesce(d.disposition,''))) AS raw_status
    FROM tefca_dimension_evidence d JOIN src s ON s.raw = lower(btrim(d.source))
    WHERE d.entity_id IN (SELECT eid FROM pop_text)),
allrows AS (SELECT * FROM rows_a UNION ALL SELECT * FROM rows_b),
per_entity AS (  -- one row per (source, entity): which outcomes it carries
    SELECT key, eid,
           bool_or(outcome='verified')    AS v,
           bool_or(outcome='not_found')   AS nf,
           bool_or(outcome='failed')      AS f,
           bool_or(outcome='unavailable') AS u,
           bool_or(outcome='deactivated') AS dx
    FROM allrows GROUP BY key, eid)
SELECT  -- #1..#11, per source AND cross-source
    (SELECT count(*) FROM rce_source_records WHERE source_intake_id = CAST(:intake AS uuid)) AS total_records,     -- #1
    (SELECT count(*) FROM pop_uuid)                                                             AS eligible,          -- #2
    (SELECT count(DISTINCT eid) FROM allrows)                                                   AS attempted_any_src, -- #3 / #11
    key                                                                                         AS source,
    count(*)                                                                                    AS attempted_src,
    count(*) FILTER (WHERE v)                                                                   AS verified,          -- #4
    count(*) FILTER (WHERE nf)                                                                  AS not_found,         -- #5
    count(*) FILTER (WHERE f)                                                                   AS failed,            -- #6
    count(*) FILTER (WHERE u)                                                                   AS unavailable,       -- #7
    count(*) FILTER (WHERE dx)                                                                  AS deactivated,
    count(*) FILTER (WHERE v  AND f)                                                            AS verified_and_failed,   -- #8
    count(*) FILTER (WHERE nf AND f)                                                            AS not_found_and_failed,  -- #9
    count(*) FILTER (WHERE u  AND f)                                                            AS unavailable_and_failed,-- #10
    count(*) FILTER (WHERE f AND NOT v AND NOT nf AND NOT u)                                    AS failed_only
FROM per_entity GROUP BY key ORDER BY key;

-- #12 raw breakdown, both tables, by source x status/disposition x dimension
SELECT key, tbl, dim, raw_status, outcome, count(*) AS rows, count(DISTINCT eid) AS entities
FROM (SELECT * FROM rows_a UNION ALL SELECT * FROM rows_b) r   -- re-declare the CTEs above in the same statement
GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4;

-- #12b the specific hypothesis in §1: does the NPPES 'failed' count come from ADDRESS CONFLICT rows?
SELECT evidence_dimension, upper(btrim(disposition)) AS disposition,
       count(*) AS rows, count(DISTINCT entity_id) AS entities
FROM tefca_dimension_evidence
WHERE lower(btrim(source)) IN ('nppes','npi_registry','cms_nppes')
  AND upper(btrim(disposition)) IN ('FAIL','CONFLICT')
  AND entity_id IN (SELECT CAST(canonical_entity_id AS text) FROM rce_curated_records
                    WHERE source_intake_id = CAST(:intake AS uuid) AND canonical_entity_id IS NOT NULL)
GROUP BY 1,2 ORDER BY 1,2;

-- #12c generations: how many verification generations does the population carry per source?
SELECT lower(btrim(source)) AS source, count(DISTINCT generation_timestamp) AS generations,
       count(*) AS rows, count(DISTINCT entity_id) AS entities
FROM tefca_dimension_evidence
WHERE entity_id IN (SELECT CAST(canonical_entity_id AS text) FROM rce_curated_records
                    WHERE source_intake_id = CAST(:intake AS uuid) AND canonical_entity_id IS NOT NULL)
GROUP BY 1 ORDER BY 1;
```

## 4. How the result resolves the question (decision table, written before the run)

| If the governed run shows… | Then 1,298 is… | Correct remediation |
|---|---|---|
| `failed` for NPPES = 1,298 and `verified_and_failed` ≈ 1,298 and #12b shows the rows are `ADDRESS`/`CONFLICT` | an **overlap** indicator: entities verified on identity whose address disagreed | relabel the UI/report figure ("entities with an address disagreement on at least one source, despite a successful identity result"); align `_DIMENSION_DISPOSITION` with the classifier by excluding the ADDRESS dimension from source-state counting, or report ADDRESS separately; the export block can then be lifted for the relabelled population |
| `failed` = 1,298 with `failed_only` = 1,298 and #12 shows `tefca_verifications.verification_status in ('failed','error')` | genuine connector errors on a source | keep `failed`; report it as a processing-availability figure per source, not as an entity finding; retry policy question |
| no source's `failed` ≈ 1,298 but #12c shows > 1 generation | a **stale/superseded** figure from an earlier cycle | the dashboard must count the latest generation per entity/source (or the cycle the report cites), not the union of all generations |
| `attempted_any_src` ≠ 24,502 or eligible ≠ 24,506 | a **different population** than the spec's | reconcile the population definition first; the outcome arithmetic is secondary |

Until one of these rows is confirmed by the run, the label is not asserted, totalled, renamed
or exported as a mutually exclusive status — the existing fail-closed behaviour stands.

## 5. Related, already-local fact (not a resolution)

`scripts/exception_inventory.py` (this pass) reports `REVIEW` separately from `NOT_FOUND` and
`FAIL`/`CONFLICT` per source on the local synthetic databases, so the same ambiguity cannot recur
in the local inventory. On the two local databases queried (`test_sam`, `test_report_full_scale`)
there are **zero** `FAIL`/`CONFLICT` dimension rows and zero `failed`/`error` verification rows —
the local data cannot reproduce the 1,298 and was not used to guess at it.
