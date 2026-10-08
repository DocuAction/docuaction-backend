# IQVIA organisation-first lookup: design for review (no migration, nothing deployed)

Status: DRAFT proposal, default-off code in this PR. No migration is included or applied; no snapshot approved; no policy activated. IQVIA remains a PROPOSED supplemental source (S5) pending COR acceptance; staging is not verified coverage.

## Why organisation-first
The immediate use is RCE organisation verification, so the entry point is an organisation identifier (ORG_NPI / ORG_CCN_ID, or the HCO key), not an HCP key. HCP-key lookups stay available but are not sufficient.

## Measured facts (DEV, snapshot afb55a68, read-only, exact unless marked)
* 7,551,269 relationship rows; 620,733 organisations (HCO keys); 4,419,663 distinct HCP keys.
* Rows per organisation: 144,589 have 1; 7,566 have more than 100; 899 more than 1,000; the largest has 28,065. A page cap and explicit truncation are therefore required.
* Existing indexes on the table (1,220 MB total): primary key 312 MB, `uq_iqvia_affiliation_snapshot_key` (snapshot, HCP, HCO, type) 591 MB, `idx_iqvia_affiliation_hcp` 219 MB, **`idx_iqvia_affiliation_hco` (HCO key) 99 MB**. An earlier note in this PR said organisation-side lookups lacked an index; that was wrong for the HCO KEY. By HCO key, the page query uses the existing index (0.4 s for a 1,542-row organisation).
* The gap is organisation lookup BY IDENTIFIER: ORG_NPI and ORG_CCN_ID live inside the JSONB payload with no index. Measured on DEV: 28.2 s for one ORG_NPI (parallel heap scan of a 7.76 GB table). Blank identifiers are stored as empty strings, so any expression must use `nullif(btrim(...),'')`.
* Identifier structure (exact, whole snapshot): 341,920 organisations carry an ORG_NPI and 55,655 distinct ORG_CCN values exist; no HCO key carries two different ORG_NPI or ORG_CCN values; no ORG_NPI and no ORG_CCN is shared by two HCO keys; 278,813 organisations have no ORG_NPI on any row; 51,653 have both an ORG_NPI and an ORG_CCN. So today's data has zero identifier conflicts; the conflict logic matters for future snapshots and is proven with fixtures.
* Registry resolution by NPI (exact): 11,473 of 341,920 organisations (3.4%) have an ORG_NPI held by a registry entity; each maps to exactly one entity; 469,832 relationship rows (6.2%) belong to them. The DEV registry holds no CCN identifiers. These are CANDIDATES; confirmed matches: 0.

## Options compared (local synthetic benchmark; see method below)
| | A. Expression indexes on the existing table | B. Separate organisation projection table | (C. Extra ordered-page index) |
|---|---|---|---|
| New objects | 2 partial btree indexes: (snapshot, nullif(btrim(ORG_NPI))) and the same for ORG_CCN_ID | table `iqvia_affiliation_org` (one row per snapshot + HCO key: ORG_NPI, ORG_CCN, row and HCP counts, distinct-identifier counts, kind counts) with PK and 2 partial indexes, filled by one INSERT..SELECT | (snapshot, HCO, HCP, type) |
| Extra storage (local, 8.2M rows) | 69 MB + 41 MB = 110 MB | 76 MB table + 62 MB indexes = 138 MB | 483 MB |
| DEV estimate | about 130-155 MB (DEV existing indexes measured 1.2-1.4x larger than local equivalents) | about 165-195 MB | about 600 MB |
| Build time (local) | 15 s per index | 59 s | 26 s |
| Lookup by ORG_NPI (local, warm) | 1-10 ms | 0.4-1.1 ms | n/a |
| Ordered page of relationships (local) | 1-83 ms (two-step query, largest NPI-bearing org 12,746 rows) | 0.7-17 ms through the existing HCO index | 0.8-3.5 ms; existing index already gives 1-15 ms, so not needed |
| Identifier conflict on demand | per-organisation count(distinct) over that organisation's rows, indexed | precomputed columns | n/a |
| Whole-snapshot conflict scan | 17-37 s (full scan, already run once on DEV: 0 conflicts) | 0.2-0.5 s | n/a |
| Moving parts | migration with 2 indexes; no population job; indexes follow every insert | migration with table + grants + population/refresh job per snapshot; can go stale | migration |
| Unindexed baseline (local) | about 12 s per lookup (DEV measured 28 s) | | |

Important planner finding (applies to A and the baseline): the obvious page query `... WHERE expr = :npi ORDER BY hcp, type LIMIT 101` makes PostgreSQL walk the unique index in HCP order and filter, which ran over two minutes locally and was cancelled. The implemented query is two-step (rows of the matched organisations first, ordering second); with it the page takes 1-83 ms. Any index proposal must be paired with this query shape.

Caveats: the benchmark is local PostgreSQL 18 on synthetic rows shaped from DEV aggregates (8.19M rows, 620,733 organisations, same rows-per-organisation histogram, payload about 854 bytes versus about 632 on DEV), everything cached; DEV is a D2ds_v5 with a 120-IOPS disk, so cold lookups on DEV will be slower and must be re-measured after any index exists. Index sizes are the most reliable output.

## Recommendation (smallest sufficient)
Option A: two partial expression indexes, no new table and no population job, plus the two-step query already implemented. It is the smaller and simpler change, it answers organisation lookup by NPI or CCN, and conflict detection per organisation is cheap because it runs over that organisation's own rows through the existing HCO index. Choose B only if reviewers need organisation LIST views or precomputed whole-snapshot conflict reports. Do not add C. Index creation on DEV (about 150 MB; DB at 9.25 GB of 32 GiB) should be a governed migration using CREATE INDEX CONCURRENTLY, in a quiet window, after owner approval. The migration is now written (`20261009_iqvia_affil_org_indexes`) and verified locally (see 'Verified indexed path (local)'); it has not been applied to DEV.

## What the code in this PR does (default off, read-only)
`organisation_relationships(...)` and `POST .../organisation-relationships`: find organisations by ORG_NPI / ORG_CCN_ID / HCO key; list up to 25 organisations with per-organisation counts and distinct-identifier counts (the rest are counted as `organisations_omitted`); return one page of relationships (default 100, hard cap 500, keyset cursor), with `total_relationships`, `returned`, `truncated`, `next_cursor` and a `LIMIT_CAPPED_AT_500` note when the caller asked for more. Conflicts reported with codes: NPI and CCN identify different organisations; an HCO key carrying more than one ORG_NPI or ORG_CCN; an identifier shared by several HCO keys. Every relationship row is kept (the same HCP under two affiliation types is two rows). Returned person-level data: HCP key and relationship kind only. Registry candidates are separate and labelled `AUTOMATED_CANDIDATE_NOT_CONFIRMED`. A PENDING snapshot is refused except through the diagnostic switch, which is never routed.
Without the proposed indexes the identifier lookup works but is slow (about 28 s on DEV), so the flag must stay off until indexes are approved or the lookup is limited to HCO-key requests.

## Test evidence
`tests/test_iqvia_affiliation_org_lookup.py` (10, real database, synthetic rows): refusal of PENDING; positive lookup preserving all relationships including one HCP under two types; CCN leading-zero restoration; absent identifier = not found in this extract; invalid identifier reported; NPI/CCN conflict; shared identifier and two ORG_NPI on one HCO key; complete duplicate-free pagination; page cap; no status change. Plus the 31 DB-free tests from the earlier commit (41 total pass locally).

## Verified indexed path (local)

STATUS: LOCAL SYNTHETIC verification of Option A. Migration `alembic/versions/20261009_iqvia_affil_org_indexes.py` (revision
`20261009_iqvia_affil_org_indexes`, parent `20261008_record_check_results`) creates `ix_iqvia_affil_snapshot_org_npi` and
`ix_iqvia_affil_snapshot_org_ccn` with CREATE INDEX CONCURRENTLY inside `autocommit_block()`. It is NOT applied by CI to any shared database and has
not been applied to DEV; **DEV must be re-timed after the governed migration**. Full raw EXPLAIN output:
`qa-evidence/2026-10-08-iqvia-index-verification/EXPLAIN-LOCAL-2026-10-08.md`. Test: `tests/test_iqvia_affil_org_indexes_2026_10_08.py`
(builds a throwaway database, checks both indexes valid, partial and owned by the table owner, round-trips the downgrade, and EXPLAINs the
statements the lookup emits; it sets `enable_seqscan = off` because a 60k-row table can legitimately prefer a seq scan on cost, and says so).

Dataset (generate_series, random organisation assignment): 2,000,000 rows in `iqvia_affiliation_observation` under one registered
snapshot; 149,839 organisations; heavy-tailed rows per organisation (largest 17,013); 1,505,647 rows with ORG_NPI (~75%),
1,007,218 with ORG_CCN_ID (~50%); blanks stored as empty strings. Heap 605 MB; pre-existing indexes 424 MB (pkey 78, uq 220, hcp 103,
hco 23). Targets: "medium" organisation (598 rows, ORG_NPI Luhn-valid synthetic 1234567893, ORG_CCN_ID 100058) and "heavy"
organisation (17,013 rows, ORG_NPI 9876543213). Statements are the ACTUAL SQL emitted by `organisation_relationships`, captured through
a SQLAlchemy `before_cursor_execute` hook and run with the same bind parameters under EXPLAIN (ANALYZE, BUFFERS), second (warm)
execution. Local cluster has max_parallel_workers_per_gather=2 (the seq scans are parallel).

| Statement | BEFORE (no new indexes) | AFTER |
|---|---|---|
| hco-key lookup by ORG_NPI (medium org, 598 rows) | Parallel Seq Scan, 354.5 ms | Index Scan ix_iqvia_affil_snapshot_org_npi, 0.26 ms |
| hco-key lookup by ORG_NPI (heavy org, 17,013 rows) | Parallel Seq Scan, 302.8 ms | Index Scan ix_iqvia_affil_snapshot_org_npi, 2.8 ms |
| hco-key lookup by ORG_CCN_ID (single value) | Parallel Seq Scan, 311.5 ms | Index Scan ix_iqvia_affil_snapshot_org_ccn, 0.25 ms |
| ORG_CCN_ID IN (two leading-zero variants) | Parallel Seq Scan, 294.1 ms | Index Scan ix_iqvia_affil_snapshot_org_ccn (= ANY array), 0.39 ms |
| per-organisation aggregate (medium / heavy) | Bitmap scan idx_iqvia_affiliation_hco, 0.85 / 23.6 ms | unchanged, 0.62 / 22.8 ms |
| materialized-CTE keyset page (medium / heavy) | Bitmap scan idx_iqvia_affiliation_hco, 0.40 / 12.2 ms | unchanged, 0.34 / 10.7 ms |
| whole `organisation_relationships` call, first call (medium) | 824 ms | ~100 ms |

Index sizes (local synthetic): ix_iqvia_affil_snapshot_org_npi 15 MB, ix_iqvia_affil_snapshot_org_ccn 9.9 MB (25 MB total; btree
deduplication makes this smaller than the earlier ~69 + ~41 MB sizing because this dataset has only ~150k distinct identifier
values; DEV's real distinct-value count decides its size). Total table indexes 424 MB -> 449 MB. Build via the migration: ~9 s for
both (CREATE INDEX CONCURRENTLY inside the Alembic upgrade, process start included). Downgrade (DROP INDEX CONCURRENTLY) and
re-upgrade round trip verified; both indexes valid and owned by docuaction_owner.

The page query and the aggregate used the existing `idx_iqvia_affiliation_hco` (source_snapshot_id, hco_record_key) both before and
after; the new indexes change only the two identifier lookups.

#### Finding: the consumer expression was changed (bind-parameter key), not the index

The consumer originally emitted `payload ->> $2::TEXT` (SQLAlchemy `payload["ORG_NPI"].astext` binds the key) and `$3::VARCHAR` for
the empty string. A custom plan folds the parameters and uses the index, but a cached GENERIC plan cannot:

```
prepare pb(uuid,text,text,text) as select distinct hco_record_key from iqvia_affiliation_observation
  where source_snapshot_id=$1 and nullif(btrim(payload ->> $2),$3) in ($4);
set plan_cache_mode=force_generic_plan;  explain execute pb(...)
-> Parallel Seq Scan ... Filter: ((source_snapshot_id = $1) AND (NULLIF(btrim((payload ->> $2)), $3) = $4))

prepare pl(uuid,text) as ... nullif(btrim(payload ->> 'ORG_NPI'),'') in ($2)      -- literals inlined
-> Index Scan using ix_iqvia_affil_snapshot_org_npi ... Index Cond: (... NULLIF(btrim((payload ->> 'ORG_NPI'::text)), ''::text) = $2)
```

`_org_identifier_expr` now inlines the key and `''` as SQL literals so the emitted text is exactly the index expression
(`nullif(btrim(payload ->> 'ORG_NPI'), '')`). All AFTER numbers are with the fixed consumer. (The raw output's
"force_generic_plan" EXPLAIN blocks are a weak proxy because EXPLAIN folds parameters; the PREPARE experiment above is the real check.)

#### Index definitions (pg_get_indexdef)

```
CREATE INDEX ix_iqvia_affil_snapshot_org_npi ON public.iqvia_affiliation_observation USING btree (source_snapshot_id, NULLIF(btrim((payload ->> 'ORG_NPI'::text)), ''::text)) WHERE (NULLIF(btrim((payload ->> 'ORG_NPI'::text)), ''::text) IS NOT NULL)
CREATE INDEX ix_iqvia_affil_snapshot_org_ccn ON public.iqvia_affiliation_observation USING btree (source_snapshot_id, NULLIF(btrim((payload ->> 'ORG_CCN_ID'::text)), ''::text)) WHERE (NULLIF(btrim((payload ->> 'ORG_CCN_ID'::text)), ''::text) IS NOT NULL)
```

#### Read-only DEV verification procedure (run AFTER the governed migration; nothing here writes)

1. Confirm the indexes: `select c.relname, i.indisvalid, pg_size_pretty(pg_relation_size(c.oid)), pg_get_userbyid(c.relowner) from pg_class c join pg_index i on i.indexrelid=c.oid where c.relname in ('ix_iqvia_affil_snapshot_org_npi','ix_iqvia_affil_snapshot_org_ccn');` Expect both `indisvalid = t`, owner = the table owner, combined size near 130-155 MB (estimate; depends on distinct identifier counts).
2. Capture the statement from the application (do not retype it): the ORG_NPI hco-key lookup is `SELECT DISTINCT hco_record_key FROM iqvia_affiliation_observation WHERE source_snapshot_id = :snap AND nullif(btrim(payload ->> 'ORG_NPI'), '') IN (:npi)`. Run it as `EXPLAIN (ANALYZE, BUFFERS)` with the snapshot afb55a68-... and a known synthetic or staged-extract ORG_NPI, twice (cold, then warm). Expected: `Index Scan using ix_iqvia_affil_snapshot_org_npi`, no `Seq Scan on iqvia_affiliation_observation`. The unindexed baseline was **28.2 s**; expect milliseconds on the warm run and at most a few seconds cold (the 120-IOPS disk dominates a cold read).
3. Repeat for the ORG_CCN_ID statement (`IN ('0xxxxx','xxxxx')`), expecting `ix_iqvia_affil_snapshot_org_ccn`.
4. Repeat for the per-organisation aggregate and the page query for an organisation found in step 2; expect `idx_iqvia_affiliation_hco`/the unique index, not a seq scan.
5. Time the whole endpoint once through the flagged route (read-only) and record: wall time, `found_in_extract`, `total_relationships`.
6. Record the numbers next to the 28.2 s baseline. Any `Seq Scan` on the table, an invalid index, or a lookup over 5 s warm is a FAIL to report, not to work around.

## Decisions required before any index or activation
1. Option A or B (or neither); approval of the migration, window and storage.
2. COR acceptance of the S5 amendment; QA-lead approval of snapshot afb55a68; source policy status.
3. Whether registry candidates may be persisted, and how analysts see the (unwired) coverage statement in reports.
4. HCO (DEMOGRAPHIC) import to DEV, or retire snapshot de35e15a.
