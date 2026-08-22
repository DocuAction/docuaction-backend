# ALEMBIC RECONCILIATION PLAN

**Date:** 2026-08-26 · **Branch:** `fix/tefca-stabilization` · **HEAD:** `3315ec9`
**Status:** ANALYSIS ONLY — **nothing was stamped, upgraded, downgraded, executed or committed.**
**Scope:** the LOCAL DEV database (`docuaction@localhost/docuaction-db`). Azure dev and prod were **not** inspected — see §11.

---

## HEADLINE

The chain cannot be reconciled by `alembic upgrade head`, and it cannot be reconciled by `alembic stamp head` either — for two different reasons, both discovered by inspection rather than assumed:

1. **Four migrations would fail on execution.** They use bare `op.create_table()` against tables that already exist.
2. **One migration is unexecutable at all.** `20260817_audit_fields` backfills using the `?` operator, which exists only for `jsonb`. `audit_logs.details` is **`json`**. The statement errors before touching a row.

That same migration is also the only one carrying real outstanding work: **3 missing indexes and 251 un-backfilled rows**. Stamping over it would mark that work permanently done.

**Recommended: Strategy B — selective execution + stamp.** Details in §7.

---

## 1. EXACT MIGRATION CHAIN

```
alembic_version (database) : 20260627_tefca_dashboard
script head                : 20260826_area1_audit
heads                      : 1        branches: 0        orphans: 0
revisions in chain         : 13       unapplied: 11
```

Linear, single root, single head — verified by `alembic history`:

```
<base>
  -> 20260627_tefca_initial          APPLIED
  -> 20260627_tefca_dashboard        APPLIED   <-- alembic_version points here
  -> 20260725_platform_config        unapplied  ┐
  -> 20260725_tefca_registry         unapplied  │
  -> 20260817_audit_fields           unapplied  │ 7 SKIPPED
  -> 20260819_dim_evidence           unapplied  │   (pre-existing)
  -> 20260819_ppef_snapshots         unapplied  │
  -> 20260820_ppef_jobs              unapplied  │
  -> 20260822_rce_pipeline           unapplied  ┘
  -> 20260823_vocab_version          unapplied  ┐
  -> 20260824_evidence_prov          unapplied  │ 4 OVERNIGHT
  -> 20260825_qa_events              unapplied  │   (Phases 1-4)
  -> 20260826_area1_audit  (head)    unapplied  ┘
```

**How the gap arose.** `app/main.py:startup()` calls `Base.metadata.create_all()`, which creates missing **tables** from the models. Every table in the seven skipped migrations therefore exists — created by the application, not by the chain. `create_all` cannot add a **column** to an existing table, which is why the `audit_logs` indexes and backfill are the one thing it did not cover.

---

## 2. THE SEVEN SKIPPED MIGRATIONS

| Revision | Purpose | Guard style | Executable today? |
|---|---|---|---|
| `20260725_platform_config` | 13 `platform_*` tables | `create_all(checkfirst=True)` | **YES** — idempotent no-op |
| `20260725_tefca_registry` | `tefca_reg_*` tables | `create_all(checkfirst=True)` over `TEFCA_REG_TABLE_ORDER`, **read at runtime** | **YES** — idempotent no-op |
| `20260817_audit_fields` | `audit_logs` +3 cols, +3 indexes, **backfill** | per-column `if not in existing`; backfill unguarded | **NO — hard failure**, see §6 |
| `20260819_dim_evidence` | `tefca_dimension_evidence` | **none** — bare `op.create_table` | **NO** — "relation already exists" |
| `20260819_ppef_snapshots` | `tefca_ppef_snapshots`, `tefca_ppef_records` | **none** | **NO** |
| `20260820_ppef_jobs` | `tefca_ppef_ingest_jobs` | **none** | **NO** |
| `20260822_rce_pipeline` | 8 RCE tables | **none** | **NO** |

**Note on `20260725_tefca_registry`:** its docstring says "10 tables"; `TEFCA_REG_TABLE_ORDER` now holds **18**, because the Tasks 3-5 tables and (as of Phase 3) `review_decision_events` were appended to the same list. The migration reads the list at runtime, so its meaning has drifted from its docstring. All 18 tables exist, so this is documentation drift rather than a schema risk — but it is why the file cannot be trusted as a record of what it created.

---

## 3. THE FOUR OVERNIGHT MIGRATIONS — SCHEMA OBJECTS PROVEN PRESENT

Every object each migration creates was checked against `information_schema`, `pg_indexes`, `pg_trigger` and `pg_proc`.

| Revision | Tables | Columns | Indexes | Triggers/Views/Fns | Verdict |
|---|---|---|---|---|---|
| `20260823_vocab_version` | — | 1/1 | 1/1 | — | **ALL PRESENT** |
| `20260824_evidence_prov` | 2/2 | 11/11 | 6/6 | — | **ALL PRESENT** |
| `20260825_qa_events` | 1/1 | 1/1 | 2/2 | 3/3 | **ALL PRESENT** |
| `20260826_area1_audit` | 1/1 | — | 2/2 | 4/4 | **ALL PRESENT** |

Named objects confirmed: `source_version_snapshots`, `evidence_relationship_path`, `review_decision_events`, `area1_mutation_log`; `tefca_dimension_evidence.vocabulary_version` + 11 provenance columns; `review_records.reportable_at`; triggers `trg_review_event_sod`, `trg_area1_record_mutation`, `trg_area1_record_delete`, `trg_area1_intake_mutation`; view `review_effective_determination`; functions `review_event_enforce_sod`, `area1_log_mutation`.

**All four are guarded and idempotent** — each checks the inspector before creating, and uses `CREATE OR REPLACE` / `DROP … IF EXISTS` for functions, views and triggers. Re-executing any of them is a safe no-op.

---

## 4. RECONCILIATION MATRIX

| Revision | Parent | Purpose | Schema expected | Present? | Data mutation in upgrade? | Already effectively applied? | Safe to execute? | Safe to stamp over? | Recommended treatment |
|---|---|---|---|---|---|---|---|---|---|
| `20260725_platform_config` | `20260627_tefca_dashboard` | 13 platform tables | 13 tables | **YES 13/13** | No | **YES** | **YES** (idempotent) | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260725_tefca_registry` | `20260725_platform_config` | registry tables | 18 tables (runtime list) | **YES 18/18** | No | **YES** | **YES** (idempotent) | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260817_audit_fields` | `20260725_tefca_registry` | audit cols + indexes + backfill | 3 cols, 3 indexes, 251-row backfill | **PARTIAL** — cols yes, **indexes 0/3**, backfill **not run** | **YES — 251 rows** | **NO** | **NO — hard failure** (`json ? unknown`) | **NO** — would bury real work | **DATA_MUTATION_REQUIRED** → §7 step 2 |
| `20260819_dim_evidence` | `20260817_audit_fields` | evidence table | 1 table, 2 indexes | **YES** | No | **YES** | **NO** — bare `create_table` | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260819_ppef_snapshots` | `20260819_dim_evidence` | PPEF snapshot tables | 2 tables | **YES** | No | **YES** | **NO** — bare `create_table` | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260820_ppef_jobs` | `20260819_ppef_snapshots` | ingest job table | 1 table, 1 partial-unique index | **YES** | No | **YES** | **NO** — bare `create_table` | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260822_rce_pipeline` | `20260820_ppef_jobs` | 8 RCE tables | 8 tables | **YES 8/8** | No | **YES** | **NO** — bare `create_table` | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260823_vocab_version` | `20260822_rce_pipeline` | vocabulary stamp | 1 col, 1 index | **YES** | No | **YES** | **YES** (guarded) | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260824_evidence_prov` | `20260823_vocab_version` | provenance | 2 tables, 11 cols, 6 idx | **YES** | No | **YES** | **YES** (guarded) | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260825_qa_events` | `20260824_evidence_prov` | QA decision events | 1 table, 1 col, 2 idx, 3 objects | **YES** | No | **YES** | **YES** (guarded) | **YES** | **SCHEMA_PRESENT** → stamp |
| `20260826_area1_audit` | `20260825_qa_events` | Area 1 audit | 1 table, 2 idx, 4 objects | **YES** | No | **YES** | **YES** (guarded) | **YES** | **SCHEMA_PRESENT** → stamp |

**Classification tally:** SCHEMA_PRESENT ×10 · DATA_MUTATION_REQUIRED ×1 · SCHEMA_MISSING_SAFE ×0 · OBSOLETE ×0 · UNKNOWN ×0.

---

## 5. ACTUAL SCHEMA VS EXPECTED

**Match on everything except three indexes.**

| | |
|---|---|
| Public tables | 87 |
| Public columns | 1,216 |
| Schema fingerprint (md5 of `table.column:type`) | `b3a620e57f3670278d31e4ec42930e87` |
| Objects expected by the 11 unapplied revisions | **all present except 3** |

**The three missing objects**, all from `20260817_audit_fields`:

```
ix_audit_logs_event_type       MISSING
ix_audit_logs_outcome          MISSING
ix_audit_logs_correlation_id   MISSING
```

`audit_logs` currently carries exactly one index — `audit_logs_pkey`.

**Why executing the migration would not create them.** The index creation sits *inside* the column-existence guard:

```python
if "event_type" not in existing:
    op.add_column("audit_logs", sa.Column("event_type", sa.String(50), nullable=True))
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])   # same block
```

The columns already exist (created by `create_all` from `app/models/database.py`, which declares them), so the guard is false and **both** statements are skipped. The indexes can never be created by running this migration.

---

## 6. UNSAFE HISTORICAL-DATA OPERATIONS

**One migration in the chain mutates data: `20260817_audit_fields`.** Three `UPDATE` statements against `audit_logs` (251 rows).

| Statement | Target | Operator | Rows it would touch | Status |
|---|---|---|---|---|
| 1 — `correlation_id` from `details` | `audit_logs` | **`details ? 'correlation_id'`** | 251 NULL | **FAILS — `operator does not exist: json ? unknown`** |
| 2 — `outcome` from `details->>'result'` / action | `audit_logs` | `->>` | 251 NULL | would succeed |
| 3 — `event_type` from action | `audit_logs` | `->>` | 251 NULL | would succeed |

### The `json` / `jsonb` defect

`audit_logs.details` is declared **`json`** in the live database. The `?` key-existence operator is defined for **`jsonb` only**. Verified live, read-only:

```
details ?  'correlation_id'    ->  FAILS: operator does not exist: json ? unknown
details ->> 'result'            ->  OK
```

Statement 1 is the **first** backfill in `upgrade()`, so the migration aborts there and statements 2 and 3 never run. **`20260817_audit_fields` is currently unexecutable against this database.**

This is a direct consequence of the chain gap: the migration was written expecting the `jsonb` the migration chain would have produced, while `create_all` produced the model's `json`.

### Are the backfills otherwise safe?

Yes. All three are `WHERE <column> IS NULL`, so they are idempotent in effect and cannot overwrite a value someone has already set. They touch **only** `audit_logs` — no TEFCA evidence, no determinations, no Area 1 row.

### Everything else in the chain

**No other migration performs any UPDATE, DELETE, DROP or TRUNCATE on existing data.** The other ten are pure DDL.

---

## 7. PROPOSED RECONCILIATION METHOD

### Strategy: **B — SELECTIVE EXECUTION + STAMP**

Not **A (stamp)**: stamping alone marks `20260817_audit_fields` applied while three indexes are missing and 251 rows are un-backfilled. That work would then be invisible — no tool would ever report it again.

Not **C (chain repair)**: rewriting the four bare `create_table` migrations to be idempotent is the *correct* long-term fix, but those files may already have executed cleanly in other environments, and rewriting applied migration history is both prohibited tonight and risky without knowing Azure's state.

Not **D (manual reconciliation required)**: the required work is fully determined and small. Deferring it to "a human will figure it out" would be under-delivering on an answerable question.

**B, in three steps.** Steps 1 and 3 are safe DDL/metadata. Step 2 is a data mutation and needs explicit authorization.

---

## 8. EXACT COMMANDS PROPOSED — **NOT EXECUTED**

### Step 0 — capture a restore point (mandatory, before anything)

```bash
pg_dump --format=custom --file=pre_reconciliation_$(date +%Y%m%d_%H%M).dump \
        "postgresql://docuaction:***@localhost:5432/docuaction-db"
```

### Step 1 — create the three missing indexes *(safe DDL, no data change)*

```sql
-- The three indexes 20260817_audit_fields would have created, had its guard
-- not coupled index creation to column absence. CONCURRENTLY is unnecessary at
-- 251 rows but harmless; IF NOT EXISTS keeps the step re-runnable.
CREATE INDEX IF NOT EXISTS ix_audit_logs_event_type     ON audit_logs (event_type);
CREATE INDEX IF NOT EXISTS ix_audit_logs_outcome        ON audit_logs (outcome);
CREATE INDEX IF NOT EXISTS ix_audit_logs_correlation_id ON audit_logs (correlation_id);
```

### Step 2 — the backfill *(DATA MUTATION — requires explicit authorization)*

The migration's own statement cannot run. A `json`-compatible equivalent, preserving the original semantics exactly:

```sql
BEGIN;

-- Statement 1, rewritten for `json`. The migration used `details ? 'key'`,
-- which is jsonb-only; `->> 'key' IS NOT NULL` is the json equivalent and is
-- equivalent here because a JSON null would not be a usable correlation id.
UPDATE audit_logs
   SET correlation_id = details ->> 'correlation_id'
 WHERE correlation_id IS NULL
   AND details ->> 'correlation_id' IS NOT NULL;

-- Statements 2 and 3 are copied VERBATIM from the migration — they already
-- work against `json`. See alembic/versions/20260817_audit_log_fields.py
-- lines 80-105 (outcome) and 107-140 (event_type).

COMMIT;
```

**Before committing, confirm the row counts changed are what was predicted:** 251 candidate rows for `outcome` and `event_type`; the `correlation_id` count is whatever `details ->> 'correlation_id' IS NOT NULL` yields (measured as 0 on this database today, because `details ? …` could not be evaluated to confirm — re-measure with the `->>` form before running).

**This step may be deferred.** If it is, the reconciliation is still valid: record the deferral, and do **not** stamp past `20260817_audit_fields` until it is resolved. See §11 risk 2.

### Step 3 — stamp the chain to head

```bash
alembic stamp head
```

Equivalent explicit form, if a reviewer prefers to see the target named:

```bash
alembic stamp 20260826_area1_audit
```

### Step 4 — verify

```bash
alembic current          # expect: 20260826_area1_audit (head)
alembic check            # expect: "No new upgrade operations detected"
```

```sql
SELECT count(*) FROM pg_indexes
 WHERE tablename = 'audit_logs' AND indexname LIKE 'ix_audit_logs_%';   -- expect 3
SELECT count(*) FROM audit_logs WHERE event_type IS NULL;                -- expect 0 after step 2
```

Then re-run the integrity baseline in §10 and confirm every digest is unchanged.

### Required follow-up, in a separate reviewed change

Correct the four bare `create_table` migrations and the `json`/`jsonb` operator, so the chain becomes replayable on a fresh database:

- `20260819_dim_evidence`, `20260819_ppef_snapshots`, `20260820_ppef_jobs`, `20260822_rce_pipeline` → guard with an inspector check, as the four overnight migrations already do.
- `20260817_audit_fields` → `details::jsonb ? 'correlation_id'`, or switch the model column to `JSONB`.
- Decouple index creation from the column-existence guard.

**Not done tonight** — rewriting migration files is outside this gate.

---

## 9. ROLLBACK / RECOVERY

| Step | Reversal | Data at risk |
|---|---|---|
| 0 — `pg_dump` | n/a | none |
| 1 — create indexes | `DROP INDEX IF EXISTS ix_audit_logs_event_type, ix_audit_logs_outcome, ix_audit_logs_correlation_id;` | none — indexes hold no data |
| 2 — backfill | **Not reversible by statement.** The prior state was NULL in all three columns on all 251 rows, so the reversal is `UPDATE audit_logs SET event_type = NULL, outcome = NULL, correlation_id = NULL;` — valid **only** while no new row has been written since. Otherwise restore from the step-0 dump. | 251 `audit_logs` rows |
| 3 — stamp | `alembic stamp 20260627_tefca_dashboard` — writes one row in `alembic_version` and touches nothing else | none |

**Full recovery:** `pg_restore` the step-0 dump. `alembic_version` is captured in the dump, so the restore returns the metadata pointer as well as the data.

**Recovery is not needed for a failed step 3.** `alembic stamp` is a single-row write to `alembic_version` and can be re-pointed at any revision at any time.

---

## 10. INTEGRITY BASELINE — captured 2026-08-26, pre-reconciliation

| | |
|---|---|
| `alembic_version` | **`20260627_tefca_dashboard`** |
| Schema fingerprint | `b3a620e57f3670278d31e4ec42930e87` (87 tables, 1,216 columns) |
| Evidence digest | `eca047f9bdf4afb8567c43c83325fa92` — **1,984 rows** |
| Determination digest | `a6fa52f503f6cf35dbe9d85bfaaadf2f` — **43 rows** |
| Area 1 record digest | `d65e51cfbd424bab7ad1703d4a1fba98` — **23,566 rows** |
| Area 1 hash verification | **23,566 / 23,566**, 0 mismatches; stored delivery file intact |
| Reconciliation gate | **18 / 18 passing** |
| Regression | **1440 passed, 40 skipped, 0 failures** |
| `audit_logs` | 251 rows; `event_type` / `outcome` / `correlation_id` NULL on **all 251** |
| Working tree | clean at `3315ec9` |

Row counts of note: `rce_source_records` 23,566 · `rce_curated_records` 23,566 · `rce_issues` 36,916 · `tefca_reg_entities` 23,756 · `tefca_dimension_evidence` 1,984 · `review_records` 43 · `review_decision_events` **0** · `area1_mutation_log` **0** · `source_version_snapshots` **0** · `tefca_ppef_snapshots` **0** · `tefca_ppef_records` **0**.

**All baseline expectations met.**

---

## 11. REMAINING RISKS

| # | Risk | Severity | Note |
|---|---|---|---|
| 1 | **Azure dev and prod `alembic_version` are unknown.** This plan is derived entirely from the local dev database. Azure may be at a different revision with a different schema drift, and the same commands could be wrong there | **HIGH** | Run §1's inventory against both before applying anything. This is the same access gap that blocks the CHECK 4 Stage B inventory |
| 2 | **Stamping past `20260817_audit_fields` without step 2** permanently marks the backfill done. No tool would report it again | **HIGH** | Either complete step 2, or stop the stamp at `20260725_tefca_registry` and track the remainder explicitly |
| 3 | **The chain is not replayable on a fresh database.** Four migrations fail on existing tables; one fails on `json`. A new environment built by `alembic upgrade head` from scratch would work (tables absent), but any environment that has ever run `create_all` first cannot be migrated | **HIGH** | The §8 follow-up is the fix. Until then, new environments must be created by migration **only**, never by `create_all` |
| 4 | **No offline dry-run is possible.** `alembic upgrade --sql` fails at `20260817_audit_fields` (`sa.inspect` on a `MockConnection`). Five migrations use runtime inspection — `audit_fields` plus **all four overnight ones** | MEDIUM | A DBA cannot be handed a reviewable SQL script for this chain. My four migrations inherited this pattern; making them offline-safe is worth doing when the §8 follow-up is written |
| 5 | **`20260725_tefca_registry` reads `TEFCA_REG_TABLE_ORDER` at runtime**, so what it creates changes as the list grows — 10 at authorship, 18 today. Its docstring is wrong | MEDIUM | Freeze the list inside the migration, or accept that the file is not a record of what it created |
| 6 | `create_all` at startup will keep re-creating any table a future `downgrade` drops | MEDIUM | A downgrade is not reliably reversible while startup `create_all` remains |
| 7 | Step 2's `->>` rewrite is semantically equivalent to `?` **except** for a key whose JSON value is `null` — `?` would match, `->>` would not | LOW | Measured impact: `details ->> 'correlation_id' IS NOT NULL` matches 0 rows today, so the difference is currently moot. Re-measure before running |
| 8 | The three indexes are being created outside the migration that "owns" them, so a fresh replay of the chain would create them a second time | LOW | `IF NOT EXISTS` makes that harmless |

---

## WHAT WAS NOT DONE

No `alembic stamp`. No `alembic upgrade`. No `alembic downgrade`. No `UPDATE alembic_version`. No migration executed. No migration file edited. Nothing committed.

Every command in §8 is written to be reviewed, not run.
