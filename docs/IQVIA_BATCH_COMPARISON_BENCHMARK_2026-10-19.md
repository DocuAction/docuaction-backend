# IQVIA batch comparison - bounded read-only Cloud Shell benchmark (operator action)

Companion to `docs/IQVIA_BATCH_COMPARISON_BENCHMARK_2026-10-19.sql` (the SQL) and
`app/tefca_registry/rce/iqvia_batch_comparison.py` (the comparison code this benchmarks). Continues
`qa-evidence/2026-10-13-delivery-count-clarity/74_IQVIA_FULL_POPULATION_MATCHING_FEASIBILITY_2026-10-19.md`,
which could not safely measure a real number from this workstation. Status: **prepared, not run. One
operator action remains.**

## 1. Why this exists

Doc 74 answered the architecture questions (no full-population "our records -> IQVIA" path exists,
matching keys on NPI/CCN via existing indexes, a bounded batch join is feasible) but could not
measure real timing or real identifier-coverage counts: no DB access from this workstation without
a firewall rule, and the only deployed HTTP path for that direction requires
`ENABLE_IQVIA_AFFILIATION_CONSUMPTION=true`, which must stay off. This benchmark closes that gap
with a SELECT-only script the operator runs themselves against the real DEV database.

## 2. What the script measures

| Step | What it measures | Why it matters |
|---|---|---|
| 1 | Identifies the current APPROVED `IQVIA_HCO` snapshot and walks to its observation root | Needed to scope step 4 to the real approved extract, not a stale/PENDING one |
| 2 | `EXPLAIN ANALYZE` over a 1,000-row representative sample of `review_records` | Cost of reading a bounded slice of "our records," ordered, not an arbitrary unordered `LIMIT` |
| 3 | `EXPLAIN ANALYZE` over the identifier lookup for that sample's entities (`tefca_entity_identifiers`, `idx_tefca_ident_entity`) | This is side A of the batch comparison tool's two queries - the one real per-batch cost that scales with chunk size |
| 4 | `EXPLAIN ANALYZE` over the full approved-snapshot observation read (`iqvia_hco_observation`) | Side B - a single bounded read, not one row at a time; this is the number that tells us whether 24,589 records' worth of batches would ever need to re-read this, or whether it can be read once and held in memory |
| 5 | Population characterization: total `review_records`, how many have no entity, how many have an entity with neither NPI nor CCN | Directly answers doc 74's open question ("how many of the 24,589 lack a usable identifier") - **the operator should narrow the `WHERE` in step 5 to the actual September delivery's scoping (intake id or date range) before trusting the count as September-specific**, since an unscoped count also includes any other delivery's review records |

## 3. Safety

- Every statement is `SELECT` / `EXPLAIN ANALYZE` (which runs the query but writes nothing - no
  `INSERT`/`UPDATE`/`DELETE` appears anywhere in the file).
- Wrapped in `BEGIN; SET TRANSACTION READ ONLY; ... ROLLBACK;` - belt-and-suspenders on top of the
  SELECT-only shape.
- Does not touch `ENABLE_IQVIA_AFFILIATION_CONSUMPTION` or any other flag.
- Does not require, request, or add a firewall rule from this side; the operator runs it from
  somewhere that already has access (Cloud Shell).

## 4. Exact operator action

1. Open Azure Cloud Shell (or any host already firewalled for DEV Postgres access).
2. Connect to the real DEV database with the existing read-capable credential already used for this
   engagement's other read-only checks.
3. Run:
   ```
   psql "$DEV_DATABASE_URL" -f IQVIA_BATCH_COMPARISON_BENCHMARK_2026-10-19.sql
   ```
   (Upload the file first, or paste its body into an interactive `psql` session - the file is plain
   SQL, no external tooling needed.)
4. **Before running step 4 of the script**, copy the `observation_root_snapshot_id` UUID printed by
   step 1 into the `<ROOT_SNAPSHOT_ID>` placeholder.
5. Report back the four `\timing`/`Execution Time` lines and the step 5 result set. No other action
   is needed from this file alone.

## 5. Checksum

| File | SHA-256 |
|---|---|
| `IQVIA_BATCH_COMPARISON_BENCHMARK_2026-10-19.sql` | `d7bea7cffb5f83265ddf39868aaef94aaf5a39479b3408d513fd0e6ee6d882af` |

## 6. What happens with the result

This benchmark's output feeds back into doc 74's open items (§5 measured sample, §6 full-run time
range) - it does not itself authorize a full run. No full-population DEV execution should start from
this result alone; that remains a separate, later decision.
