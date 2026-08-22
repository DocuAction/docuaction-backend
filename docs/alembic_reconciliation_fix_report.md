# Alembic Reconciliation — Fix Report

**Date:** 2026-08-22
**Branch:** `fix/tefca-stabilization`
**Scope:** migration scripts and tests only. No historical data was modified, no
`alembic_version` row was written, nothing was deployed or merged.
**Predecessor:** `docs/alembic_reconciliation_plan.md` (the read-only investigation).

---

## 1. What this changes, in one paragraph

The migration chain could not be run. Revision `20260817_audit_fields` aborted on
a `jsonb`-only operator applied to a `json` column, and four later revisions
called `op.create_table()` unconditionally against tables that application
startup had already created. Both classes of defect are now fixed: every DDL call
in the nine affected revisions is routed through an existence check, and the
251-row audit backfill has been lifted out of the migration into a separately
authorized data-remediation script. The chain now converges to head from an empty
database, from a simulated drifted database and from a clone of the live schema —
and doing so adds **41 indexes, zero tables and zero rows**.

The live database is untouched by this change. `alembic_version` still reads
`20260627_tefca_dashboard`, `audit_logs` still has 251 unclassified rows and zero
`ix_` indexes, and all five integrity gates reproduce their pre-change digests.

---

## 2. Migration files changed, and the exact defect corrected in each

### 2.1 `20260817_audit_log_fields.py` — rewritten

Four defects, all of which had to be fixed before the chain could move at all.

| # | Defect | Correction |
|---|--------|------------|
| 1 | `WHERE ... AND details ? 'correlation_id'`. The `?` key-existence operator is defined for `jsonb` only; `audit_logs.details` is `json`. Every run aborted with `operator does not exist: json ? unknown`, taking the nine revisions behind it down too. | The statement is gone from the migration. Its replacement in the remediation script uses `details ->> 'correlation_id' IS NOT NULL`, which is valid for both `json` and `jsonb`. |
| 2 | Index creation nested inside the column-existence guard. On the live schema — three columns present, three indexes absent — the guard correctly skipped the column and silently skipped the index with it, so the three indexes could never be created by running this revision. | Column creation and index creation are now two independent guarded loops. The index loop re-reads the inspector after the `ADD COLUMN`s. |
| 3 | Three unguarded `UPDATE` statements rewrote 251 existing audit records as a side effect of a schema migration. | Removed. See §6. `upgrade()` now issues no DML at all; a test enforces that. |
| 4 | `downgrade()` dropped each index only if its *column* existed, and never checked whether the index existed. Against the live schema it would have failed on the first `DROP INDEX`. | Index drops and column drops are separately guarded on the existence of the object actually being dropped. |

A fifth issue surfaced during testing and is handled here but flagged in §8.2:
`audit_logs` is not created by any revision in the chain. On a database built by
`alembic upgrade head` alone the table simply does not exist. The revision now
detects that, logs a warning naming the cause, and adds nothing rather than
aborting the chain over a table it does not own.

### 2.2 `20260819_tefca_dimension_evidence.py`, `20260819_ppef_snapshots.py`, `20260820_ppef_ingest_jobs.py`, `20260822_rce_pipeline.py` — guarded

All four called `op.create_table()` and `op.create_index()` unconditionally;
`20260822_rce_pipeline` also called `op.add_column()` eleven times against
`tefca_reg_entities`. Every one of those objects already exists on the live
database, so every one of these revisions would have failed on its first
statement.

Each file now carries a small, self-contained guard block —
`_create_table` / `_create_index` / `_add_column` / `_drop_*` — that checks the
inspector before issuing DDL. The guards are duplicated per file rather than
imported from a shared module, deliberately: a revision that imports application
code stops being a fixed record of what it did (§8.1 is that same problem).

**Nothing about what these revisions create was changed** — same tables, same
columns, same indexes, same order. Only the decision to issue each statement.

### 2.3 `20260823` … `20260826` — offline-mode tolerance

These four were already guarded and idempotent, but they called `sa.inspect(bind)`
directly. In offline (`--sql`) mode Alembic binds a `MockConnection`, which cannot
be inspected, so `alembic upgrade --sql` raised `NoInspectionAvailable` and no
reviewable script could be produced. Each now routes inspection through
`_inspect(bind)`, which returns an empty-schema stand-in when
`op.get_context().as_sql` is set. `upgrade()` then emits its full DDL.

Limitation, stated rather than hidden: offline **downgrade** rendering is a no-op
for these four, because their drop guards read the inspector inline. The five
revisions in §2.1–§2.2 render offline in both directions.

### 2.4 New files

| File | Purpose |
|------|---------|
| `scripts/remediate_audit_log_classification.py` (299 lines) | The 251-row backfill, as a separately authorized operation. §6. |
| `tests/test_migration_chain.py` (255 lines, 36 tests) | Static tests that fail in review if a bare `op.create_table()`, an unguarded drop, a DML statement in `20260817`, or a second head is reintroduced. |

Diff: 9 migration files, +616 / −200.

---

## 3. Test results

Four scenarios. A, B and C drive the revision modules through Alembic's
`MigrationContext`/`Operations`; D goes through the real `alembic` CLI, `env.py`
and its asyncpg engine — the code path a deployment uses.

All four run inside throwaway schemas of the **separate `docuaction` database**.
The application database `docuaction-db` was only ever read (`pg_dump
--schema-only` for the clone).

### A — fresh empty database, full chain base → head

```
20260627_tefca_initial       ok      20260822_rce_pipeline    ok
20260627_tefca_dashboard     ok      20260823_vocab_version   ok
20260725_platform_config     ok      20260824_evidence_prov   ok
20260725_tefca_registry      ok      20260825_qa_events       ok
20260817_audit_fields        ok      20260826_area1_audit     ok
20260819_dim_evidence        ok
20260819_ppef_snapshots      ok      reached: 20260826_area1_audit (head)
20260820_ppef_jobs           ok      58 tables, 266 indexes
```
**PASS.** Before the fix this run stopped at `20260817_audit_fields`.

`20260817_audit_fields` reports `ok` here by skipping: on a chain-only database
`audit_logs` does not exist (§8.2).

### A2 — idempotency: the entire chain re-run over its own output

All 13 revisions `ok` a second time; the table, column and index sets are
byte-identical to the first run. **PASS.**

### A3 — full downgrade head → base

The nine reconciled revisions downgrade cleanly:

```
20260826_area1_audit ok   20260822_rce_pipeline  ok   20260819_dim_evidence ok
20260825_qa_events   ok   20260820_ppef_jobs     ok   20260817_audit_fields ok
20260824_evidence_prov ok 20260819_ppef_snapshots ok
20260823_vocab_version ok
20260725_tefca_registry  FAILED: cannot drop type entitytype because other
                                 objects depend on it
```

The failure is in `20260725_tefca_registry`, which is not one of the revisions
this change touches, and it is pre-existing. See §8.4. **Downgrade of everything
in scope: PASS.**

### B — simulated drift, reproduced the way it actually happened

Built by running the chain to `20260627_tefca_dashboard`, then calling
`create_all()` on both declarative Bases the way `app/main.py` startup does, then
stamping `20260627_tefca_dashboard`.

```
drifted start state: 111 tables, 327 indexes
all 11 remaining revisions  ok
reached: head    121 tables, 398 indexes, +71 indexes
```
**PASS.**

### C — clone of the current local dev schema

`pg_dump --schema-only` of the live `public` schema, replayed into a throwaway
schema, stamped at the revision the live database records.

```
clone: 87 tables, 348 indexes, stamped 20260627_tefca_dashboard
all 11 remaining revisions  ok
reached: head
new tables : 0
new indexes: 41
```
**PASS.** This is the result that matters: applying the chain to the live schema
adds nothing but indexes.

### D — the real `alembic upgrade head` CLI against the same clone

```
$ alembic current            -> 20260627_tefca_dashboard
$ alembic upgrade head       -> rc=0, 11 revisions applied
$ alembic current            -> 20260826_area1_audit (head)
tables  87 -> 87   (new: none)
indexes 348 -> 389 (new: 41)
```
**PASS.**

The 41 indexes, by owning revision:

| Revision | Count | Indexes |
|---|---|---|
| `20260817_audit_fields` | 3 | `ix_audit_logs_event_type`, `ix_audit_logs_outcome`, `ix_audit_logs_correlation_id` |
| `20260819_dim_evidence` | 1 | `ix_tefca_dimension_evidence_dimension` |
| `20260819_ppef_snapshots` | 4 | `ix_tefca_ppef_records_snapshot`, `_enrollment`, `_related`, `ix_tefca_ppef_snapshots_version` |
| `20260822_rce_pipeline` | 33 | 27 × `idx_rce_*`, `idx_tefca_contact_source_record`, 5 × `idx_tefca_reg_ent_*` |

### Offline `--sql` rendering

`alembic upgrade 20260627_tefca_dashboard:head --sql` now returns rc=0 and emits
the whole chain (47 `CREATE TABLE`, 143 DDL statements in the reconciled tail).
Before the fix it aborted at `20260817_audit_fields`.

### Regression and integrity gates — run against the live database, read-only

| Gate | Result |
|---|---|
| Full test suite | **1440 passed, 40 skipped, 0 failed** (pre-change baseline reproduced), **+36 new** in `tests/test_migration_chain.py` |
| Reconciliation gate | **18 checks, 0 failed**, `passed: True` |
| Area 1 hash revalidation | **23,566 records recomputed, 0 mismatches**, `intact: True` |
| Evidence integrity | 1,984 rows, digest `eca047f9bdf4afb8567c43c83325fa92` — **matches baseline** |
| Determination integrity | 43 rows, digest `a6fa52f503f6cf35dbe9d85bfaaadf2f` — **matches baseline** |
| Area 1 record digest | `d65e51cfbd424bab7ad1703d4a1fba98` — **matches baseline** |
| `audit_logs` | 251 rows; 251 NULL in each of the three columns; 0 `ix_` indexes — **unchanged** |
| `alembic_version` | `20260627_tefca_dashboard` — **unchanged** |

---

## 4. Is a stamp still required?

**No. A blanket `alembic stamp head` is no longer the right move, and it is now
the worse of the two options.**

The investigation recommended Strategy B — selective execution plus a stamp —
because ten of the eleven unapplied revisions were classified SCHEMA_PRESENT and
the eleventh was unexecutable. That reasoning held only while the revisions could
not be run. It no longer does.

Stamping records the eleven revisions as applied without executing them. Scenario
D shows what executing them actually does on this schema: it creates 41 indexes
that are genuinely missing and nothing else. **Stamping would mark those 41
indexes as delivered while leaving them permanently absent**, and no later
revision would ever create them. Three of them are the `audit_logs` indexes whose
absence was the original AT-007 complaint.

So: run the chain, do not stamp it. One caveat gates that recommendation — §8.3.

---

## 5. Proposed final reconciliation commands

**Not yet authorized. Nothing below has been run.**

The sequence is split at the point where it stops being purely additive.

### Step 0 — capture a restore point (required)

```bash
pg_dump -Fc -h localhost -p 5432 -U docuaction -d docuaction-db \
        -f backup/pre_reconciliation_$(date +%Y%m%d_%H%M).dump
```

### Step 1 — the purely additive part (no authorization beyond this report)

```bash
alembic current                              # expect: 20260627_tefca_dashboard
alembic upgrade 20260820_ppef_jobs --sql > /tmp/step1.sql   # review first
alembic upgrade 20260820_ppef_jobs
alembic current                              # expect: 20260820_ppef_jobs
```

Effect: creates 8 of the 41 indexes (3 audit, 1 dimension-evidence, 4 PPEF).
Adds no table, no column, no row, and changes no privilege.

### Step 2 — `20260822_rce_pipeline` (needs Area 1 privilege authorization)

This revision creates the remaining 33 indexes **and** calls
`_apply_immutability_grants()`, which revokes table-wide `UPDATE`/`DELETE` on
`rce_source_intakes` and `rce_source_records` from the application role. Today
that role holds both. Revoking them table-wide breaks `promote_delivery`, which
updates `promotion_status` on `rce_source_records`. See §8.3.

Run steps 2 and 3 together, in one window, or run neither:

```bash
export DB_APP_ROLE=docuaction        # be explicit; do not let it infer current_user
alembic upgrade 20260822_rce_pipeline

# immediately restore the intended column-level grants
python - <<'PY'
from app.tefca_registry.rce.repository import immutability_grants_sql
print(";\n".join(immutability_grants_sql("docuaction")) + ";")
PY
# review, then apply the printed statements as the table owner
```

### Step 3 — the remaining four revisions

```bash
alembic upgrade head
alembic current                              # expect: 20260826_area1_audit (head)
```

All four are already-present-schema no-ops on this database; scenario D confirms
they add nothing.

### Step 4 — re-verify

```bash
python scripts/verify_rbac_matrix.py
pytest -q                                    # expect 1476 passed, 0 failed
```
plus the reconciliation gate, the 23,566-record hash revalidation and the three
digests in §3. All must reproduce the values in that table.

### Locking note

The 41 `CREATE INDEX` statements take a `SHARE` lock, blocking writes to the
affected tables for their duration. The largest table involved holds 23,566 rows,
so this is sub-second — but it is still a write-blocking operation and belongs in
a maintenance window rather than mid-request.

---

## 6. The 251 `audit_logs` rows

**They have not been changed and this change cannot change them.**

The backfill now lives in `scripts/remediate_audit_log_classification.py` and is
not reachable from any migration. `upgrade()` in `20260817_audit_fields` issues no
DML, and `tests/test_migration_chain.py::test_it_writes_no_row` fails the build if
that ever stops being true.

The separation is not bureaucratic. Assigning a classification to an audit record
that already exists rewrites the audit trail — the one table whose value depends
on nobody rewriting it. That is a records-management decision, not a schema
change, and it should not ride along unattended with whatever deployment happens
to run next.

**What the operation would do**, from a dry run against the live database today:

| Column | Rows set | Values |
|---|---|---|
| `event_type` | 251 | `authentication` 237, `administration` 9, `other` 5 |
| `outcome` | 251 | `success` 147, `failure` 104 |
| `correlation_id` | 104 | 104 distinct values, lifted from `details->>'correlation_id'` |
| **distinct rows touched** | **251** | |

**How it is gated.** Default invocation is a dry run that writes nothing.
Applying requires all three of `--authorized-by "<name, role>"`,
`--expect-rows 251` (which must equal what the dry run reports, so the operation
cannot run blind against a table that changed since review) and `--journal
<path>`. The script reads the proposed value for every affected row and writes
the before/after pairs to the journal *before* committing, and `--revert
<journal>` puts every one of them back.

**The argument for eventually running it**, recorded so the decision is made on
its merits: leaving history NULL makes the new Audit Trail filters lie by
omission. An empty result for "failed logins before today" is indistinguishable
from "there were none". That is a real reason to do it — and a reason to do it
deliberately, not silently.

**Recommendation:** run the dry run at review time, attach its output to the
authorization, then apply with a named approver. Not part of the reconciliation.

---

## 7. Rollback plan

| If | Then |
|---|---|
| Step 1 misbehaves | `alembic downgrade 20260627_tefca_dashboard`. Verified in scenario A3: all nine reconciled revisions downgrade cleanly. Drops the 8 indexes; touches no row. |
| Step 2 misbehaves | `alembic downgrade 20260820_ppef_jobs` drops the 33 indexes and the RCE tables' DDL additions. **This does not restore the grants** — reapply `immutability_grants_sql()` output, or `GRANT UPDATE, DELETE ON rce_source_intakes, rce_source_records TO docuaction` to return to today's state. |
| Step 3 misbehaves | `alembic downgrade 20260822_rce_pipeline`. |
| A downgrade itself fails | Restore the Step 0 dump. `20260725_tefca_registry`'s downgrade is known broken (§8.4), so do not attempt to unwind past `20260817_audit_fields` with Alembic. |
| The remediation script was run and needs undoing | `python scripts/remediate_audit_log_classification.py --revert <journal> --authorized-by "<name>"`. Restores the exact prior value of every column it set. |
| Only the code needs reverting | `git revert <this commit>`. The migration scripts are the whole change; nothing else depends on them at runtime. |

The safest property of the whole sequence: **every step is additive to schema and
inert to data.** No step in §5 inserts, updates or deletes a row.

---

## 8. Reported, not changed — each needs its own authorization

These were found during this work. Changing any of them would alter application
behaviour or the reproducibility of historical migrations, so none was touched.

### 8.1 `20260725_tefca_registry` reads its table list at runtime

```python
def _registry_metadata_and_tables():
    import app.tefca_registry.models as rm
    md = Base.metadata
    return md, [md.tables[name] for name in rm.TEFCA_REG_TABLE_ORDER]
```

`TEFCA_REG_TABLE_ORDER` is read from the live models module when the revision
runs. Its docstring says 10 tables; the list now holds **18**. The same revision
id therefore creates a different set of tables today than it did when it was
first applied, and a fresh database and an upgraded one do not converge through
it. (`20260822_rce_pipeline`'s docstring already records this — it is why that
revision states its DDL literally.)

**Why it was not changed now.** Freezing the list would make this revision create
18 tables on a fresh database instead of the 10 it created historically, or 10
instead of 18 — either way, a revision that has already been applied in
environments would start meaning something different from what it meant when it
ran there. That is exactly the reproducibility property this exercise is meant to
protect.

**Safest correction, in order of preference:**

1. **Leave `20260725_tefca_registry` byte-frozen** and add a new revision that
   literally declares the eight tables added after the original ten, guarded the
   way §2.2 guards its four. Historical reproducibility is preserved because the
   old revision stops changing; convergence is restored because the new revision
   states its own DDL. This is the recommendation.
2. Freeze the list *inside* the existing revision as a literal tuple of the ten
   names it created when written, and let option 1 cover the other eight. Same
   end state, one more edit to an applied revision.
3. Do nothing. Acceptable only while every environment is built by
   `create_all()` anyway — which is the situation this whole exercise exists to
   end.

### 8.2 The project has two disjoint declarative Bases

| Base | Tables | Who builds them |
|---|---|---|
| `app.database.Base` | 47 | `alembic/env.py`'s `target_metadata`; the migration chain |
| `app.core.database.Base` | 16 | `app/main.py` startup's `create_all()` — nothing else |

They share no registry. `audit_logs` is in the second one, which is why no
revision creates it and why `20260817_audit_fields` had no table to alter on a
chain-only database. It is also why `alembic revision --autogenerate` has never
seen those 16 tables.

**Consequence:** `alembic upgrade head` alone does not produce a working
database. Application startup is a required, unversioned part of schema
construction.

**Correction:** point `env.py` at a metadata covering both Bases (or merge them),
then add a revision that declares the 16 tables. Both steps change what
autogenerate proposes and what a fresh deployment builds, so both need their own
gate.

### 8.3 `20260822_rce_pipeline` applies the pre-Phase-4 Area 1 grants

```python
f'REVOKE UPDATE, DELETE ON {table} FROM "{role}"'
f'GRANT SELECT, INSERT ON {table} TO "{role}"'
```

Phase 4 established that a blanket `REVOKE UPDATE` breaks `promote_delivery`,
which writes `promotion_status` and `canonical_entity_id` on
`rce_source_records` after the entities are committed. `immutability_grants_sql()`
was corrected to transfer ownership, revoke `UPDATE, DELETE, TRUNCATE`, and then
`GRANT UPDATE (promotion_status, canonical_entity_id)` back. **The migration
still emits the old, promotion-breaking form.**

Today this is latent: the live role still holds full `UPDATE`/`DELETE` on both
Area 1 tables (`verify_immutable()` reports `enforced: False`), because
`immutability_grants_sql()` has never been applied. Making the migration runnable
makes the risk live — hence the Step 2 / Step 3 pairing in §5.

**Correction:** replace the two statements with the column-level form that
`immutability_grants_sql()` emits, so a fresh deployment lands on the intended
end state without a manual follow-up. Left unchanged here because it alters
database privileges, which this authorization excludes.

### 8.4 `20260725_tefca_registry` cannot be downgraded

`downgrade()` fails with `cannot drop type entitytype because other objects
depend on it`. Pre-existing, unrelated to this change, and the reason §7 says not
to unwind past `20260817_audit_fields` with Alembic. Fixing it means dropping the
enum types after the tables, with dependency ordering.

### 8.5 `alembic check` still reports drift at head

After scenario D reached head, `alembic check` still proposed 92 `add_table` and
168 `remove_table` operations. This is not residue from the chain; it is §8.2
plus the fact that `app.database.Base.metadata` contains tables from unrelated
product areas that no migration owns. **`alembic check` cannot be used as a CI
gate until §8.1 and §8.2 are resolved.** `alembic current` is the only usable
signal today.

---

## 9. Evidence

| Artefact | Where |
|---|---|
| Read-only investigation this builds on | `docs/alembic_reconciliation_plan.md` |
| Scenario A/B/C harness | scratchpad `mig_harness.py` |
| Scenario D harness (real CLI) | scratchpad `mig_e2e.py` |
| Post-change integrity gates | scratchpad `verify_integrity.py`, `integrity_after_fix.json` |
| Offline `--sql` render | scratchpad `offline_tail.sql` (614 lines, 143 DDL statements) |
| Standing tests | `tests/test_migration_chain.py` — 36 tests, DB-free |

Nothing in this change was applied to `docuaction-db`. Every scenario ran in a
throwaway schema of the separate `docuaction` database, and every one of those
schemas was dropped afterwards.
