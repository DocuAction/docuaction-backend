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
Option A: two partial expression indexes, no new table and no population job, plus the two-step query already implemented. It is the smaller and simpler change, it answers organisation lookup by NPI or CCN, and conflict detection per organisation is cheap because it runs over that organisation's own rows through the existing HCO index. Choose B only if reviewers need organisation LIST views or precomputed whole-snapshot conflict reports. Do not add C. Index creation on DEV (about 150 MB; DB at 9.25 GB of 32 GiB) should be a governed migration using CREATE INDEX CONCURRENTLY, in a quiet window, after owner approval. Not done here.

## What the code in this PR does (default off, read-only)
`organisation_relationships(...)` and `POST .../organisation-relationships`: find organisations by ORG_NPI / ORG_CCN_ID / HCO key; list up to 25 organisations with per-organisation counts and distinct-identifier counts (the rest are counted as `organisations_omitted`); return one page of relationships (default 100, hard cap 500, keyset cursor), with `total_relationships`, `returned`, `truncated`, `next_cursor` and a `LIMIT_CAPPED_AT_500` note when the caller asked for more. Conflicts reported with codes: NPI and CCN identify different organisations; an HCO key carrying more than one ORG_NPI or ORG_CCN; an identifier shared by several HCO keys. Every relationship row is kept (the same HCP under two affiliation types is two rows). Returned person-level data: HCP key and relationship kind only. Registry candidates are separate and labelled `AUTOMATED_CANDIDATE_NOT_CONFIRMED`. A PENDING snapshot is refused except through the diagnostic switch, which is never routed.
Without the proposed indexes the identifier lookup works but is slow (about 28 s on DEV), so the flag must stay off until indexes are approved or the lookup is limited to HCO-key requests.

## Test evidence
`tests/test_iqvia_affiliation_org_lookup.py` (10, real database, synthetic rows): refusal of PENDING; positive lookup preserving all relationships including one HCP under two types; CCN leading-zero restoration; absent identifier = not found in this extract; invalid identifier reported; NPI/CCN conflict; shared identifier and two ORG_NPI on one HCO key; complete duplicate-free pagination; page cap; no status change. Plus the 31 DB-free tests from the earlier commit (41 total pass locally).

## Decisions required before any index or activation
1. Option A or B (or neither); approval of the migration, window and storage.
2. COR acceptance of the S5 amendment; QA-lead approval of snapshot afb55a68; source policy status.
3. Whether registry candidates may be persisted, and how analysts see the (unwired) coverage statement in reports.
4. HCO (DEMOGRAPHIC) import to DEV, or retire snapshot de35e15a.
