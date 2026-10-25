# IQVIA full-population September comparison -- THE ONE EXECUTION PACKET

Companion to `docs/IQVIA_FULL_POPULATION_EXECUTION_PACKET_2026-10-20.sql`. This is the single,
concrete approval ask for running the real September -> approved-IQVIA comparison against the real
DEV database. Status: **prepared, validated end-to-end on synthetic data, not run against real
data. One operator decision + one operator action remain.**

## 1. What this is, in one paragraph

Compares all 24,589 real September delivery records (reconciled population — see
`qa-evidence/2026-10-13-delivery-count-clarity/75_SEPTEMBER_POPULATION_RECONCILIATION_AND_B1B4_METHODOLOGY_2026-10-20.md`)
against the currently APPROVED `IQVIA_HCO` snapshot, using each record's own submitted NPI/CCN.
Writes **zero rows to the database** — the only output is three local CSV files written by `\copy`.
No migration, no new table, no deployment, no flag change.

## 2. Exact effect

| | |
|---|---|
| Reads | `rce_source_records` (scoped to intake `4417b334-7440-4c30-9afa-05f4268f17c7` only), `iqvia_hco_observation`, `source_snapshot`, `rce_source_intakes` |
| Writes | **None.** `BEGIN; SET TRANSACTION READ ONLY; ... ROLLBACK;` — Postgres itself enforces this, not just the comment saying so |
| New DB objects | None — no table, no migration, no index |
| Flags changed | None — `ENABLE_IQVIA_AFFILIATION_CONSUMPTION` and `ENABLE_IQVIA_SOURCES` both untouched |
| Side effects outside the DB | Three CSV files written to the local filesystem of whoever runs `psql` |

## 3. Tests / validation already done (synthetic data, not real data)

- `app/tefca_registry/rce/iqvia_batch_comparison.py` (the Python module this SQL's methodology
  mirrors): 12/12 tests passing on a disposable ephemeral Postgres 18 instance, covering all five
  outcome buckets plus the reconciliation invariant. See PR `feat/iqvia-batch-comparison-dryrun`
  (#168), commit `985b07d`.
- **This SQL file itself was run end-to-end** against the same disposable instance, seeded with:
  a synthetic `rce_source_intakes` row under the real intake id, five synthetic
  `rce_source_records` rows (one per outcome bucket), and a real approved `IQVIA_HCO` snapshot
  built through the actual `iqvia_import.import_hco_csv` + `source_matching.approve_snapshot`
  pipeline (not hand-inserted rows). Result: reconciliation row
  `total_records=5, matched=1, missing_identifier=1, not_found=1, ambiguous=1,
  processing_error=1`, last column (`bucket_total_minus_total_records_must_be_zero`) `= 0`. All
  three CSV exports produced exactly the expected rows, verified by reading their contents.
- **An earlier draft of this SQL was wrong and was caught before being sent anywhere**: it used
  `CREATE TEMP TABLE AS` to avoid repeating the comparison query, which Postgres refuses inside a
  read-only transaction ("cannot execute CREATE TABLE AS in a read-only transaction"). Fixed by
  repeating the plain `SELECT` in each of the three exports instead, and re-validated end-to-end
  after the fix.
- **Not tested**: the real 24,589-row population, the real approved IQVIA_HCO snapshot's actual
  content, and therefore the real bucket counts. Those numbers do not exist yet.

## 4. Known limitation, stated plainly

The SQL's `PROCESSING_ERROR` check (`npi !~ '^[0-9]{10}$'`) is a **basic format check**, not the
full Luhn check-digit algorithm `app/services/npi_validator.validate_npi()` applies in application
code. A 10-digit string that fails Luhn will be counted `MATCHED`/`NOT_FOUND`/`AMBIGUOUS` by this
SQL instead of `PROCESSING_ERROR`. This is a deliberate, disclosed simplification for a one-shot
SQL export, not a claim of full parity with the tested Python validator. If this distinction
matters for the real run, the operator should ask for the Python module to be used instead (it
already does the full Luhn check) — that would need a different execution path (an admin endpoint
or a one-off script with real DB credentials), which is a larger ask than this SQL-only packet and
is not what is being proposed here.

## 5. Recovery / resume

Trivially re-runnable at will. Because nothing is written to the database, there is no partial-run
state — a second run from scratch reproduces the same result unless the approved snapshot or the
source records themselves change. No rollback procedure is needed beyond the `ROLLBACK` already in
the script.

## 6. Exact operator action

1. Open Azure Cloud Shell (or any host already firewalled for DEV Postgres access — the agent did
   not request or add one).
2. Run psql interactively against `$DEV_DATABASE_URL`, run Step 1 of the script, copy the printed
   `observation_root_snapshot_id` UUID.
3. Replace EVERY `<ROOT_SNAPSHOT_ID>` placeholder in the file with that UUID (there are five: Step 2, Export 1, Export 2 twice, Export 3) with one whole-file find/replace; `grep -c "'<ROOT_SNAPSHOT_ID>'::uuid"` must print 0 before running.
4. Run: `psql "$DEV_DATABASE_URL" -f IQVIA_FULL_POPULATION_EXECUTION_PACKET_2026-10-20.sql`
5. Report back the reconciliation row (confirm `total_records = 24589` and the last column `= 0`)
   and the three CSV files, for the next step (building the PDF/data-dictionary deliverables from
   real results).

## 7. What happens after this runs

Only then do the remaining deliverables from the master brief's item 5 (the September review PDF,
the reconciled data dictionary) become buildable from real results rather than synthetic test data.
Nothing in this packet performs that step — it produces the three CSVs only.
