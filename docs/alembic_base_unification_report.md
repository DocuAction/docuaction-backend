# Alembic Base Unification and Area 1 Privilege Correction

**Date:** 2026-08-22
**Branch:** `fix/tefca-stabilization`
**Predecessors:** `docs/alembic_reconciliation_plan.md` (investigation),
`docs/alembic_reconciliation_fix_report.md` (drift-safe chain)
**Scope:** Alembic configuration, two new forward revisions, tests. No model was
changed, no historical revision was rewritten, no historical data was touched,
nothing was stamped, deployed or merged.

---

## 1. What was actually wrong

`alembic/env.py` pointed `target_metadata` at `app.database.Base` and imported
`app.models`. That combination let Alembic see **47 of the 135 modelled tables**.

The project declares models on two independent declarative Bases, and — more
importantly — metadata is populated by *importing the module that declares the
model*, not by declaring the Base. Both halves were wrong:

| | tables | how it was |
|---|---|---|
| `app.database.Base` | 47 | the ERP/business models — the only Base Alembic targeted |
| `app.core.database.Base` | 89 | core, TEFCA, platform, registry, RCE — invisible |
| union, `users` counted once | **135** | |

So `alembic check` was comparing 47 business-domain models against a database
full of TEFCA tables, and proposing to drop nearly all of it. The 260-operation
figure quoted earlier came from that comparison; it was noise produced by a
misconfiguration, not a measurement of drift.

Underneath the configuration problem was a real one: **80 of the 135 tables are
created by nothing except `app/main.py` startup's `create_all()`**. `alembic
upgrade head` on an empty database produced a database with no `users`, no
`documents` and no `audit_logs` — one the application could not run against.

---

## 2. Issue 1 — unifying the two Bases

### 2.1 `env.py`

Three changes, no model touched.

**Both Bases are imported, and so is every module that registers on them.**
Eight model modules are now imported explicitly. `app.main` is deliberately not:
importing it registers routers and reads network feed configuration, and a
migration run must do neither.

**The metadata is merged into one collection.** A list of `MetaData` is the
obvious shape and it does not work here — Alembic raises

```
ValueError: Duplicate table keys across multiple MetaData objects: "users"
```

before `include_object` is ever consulted. A merged collection can express what
a list cannot: *these two disagree, and this is the definition that wins*.

**The `users` collision is resolved on evidence.** `users` is declared twice:

| declaration | columns | matches the live table? |
|---|---|---|
| `app/models/__init__.py:339` → AppBase | 9 | no |
| `app/models/database.py:13` → CoreBase | 16 | **yes, exactly — nothing left over on either side** |

CoreBase is authoritative. The AppBase copy is dropped from the comparison
rather than deleted from the codebase: removing a model is an application
change, and env.py's job is to describe the schema, not edit it. `saved_searches`
has a foreign key to `users`, and in the merged collection it resolves against
the authoritative definition instead of dangling.

### 2.2 Coverage inventory

Coverage was decided empirically, not by reading migrations: build a schema from
`alembic upgrade head` alone, then compare it to the merged metadata with
`alembic revision --autogenerate`. Whatever autogenerate proposes there is, by
construction, exactly what the chain fails to build.

| classification | count |
|---|---|
| Total modelled tables (both Bases, `users` once) | **135** |
| Previously invisible to Alembic | **88** |
| ALREADY_COVERED_BY_MIGRATION | **55** |
| CREATED_ONLY_BY_STARTUP_DDL → added to the new migration | **80** |
| PARTIALLY_COVERED | **0** |
| UNKNOWN | **0** |
| Collisions across the two Bases | **1** (`users`) |

`PARTIALLY_COVERED` is zero on measurement, not assumption: autogenerate
proposed 0 `add_column`, 0 `drop_column` and 0 `alter_column` against the
chain-built schema. Every table the chain does create, it creates correctly.

### 2.3 `20260827_startup_coverage`

Creates the 80 uncovered tables and their 59 indexes, each guarded by
`_has_table` / `_has_index`. It also creates 13 model-declared indexes on tables
the chain already builds, each missing on at least one of the two paths. Ten are
missing on both. The three on `review_decision_events` exist on a fresh database
but not on the live one, where that table came from `create_all()` instead — so
the guarded create is a no-op on one path and the fix on the other.

Three things in it are worth calling out.

**Enum types are hoisted.** Autogenerate writes `sa.Enum(...)` inline in every
table that uses a type, and each `create_table` then emits its own `CREATE TYPE`.
Seven of the 35 types are shared, so the second table sharing one failed with
`type "cmmoduletype" already exists`. All 35 are now created once, guarded, before
any table, and every column carries `create_type=False`.

**Index names are reconciled, not rebuilt.** 41 indexes exist under a name a
migration chose while the model declares the same table and the same columns
under a different name. `ALTER INDEX ... RENAME` is a catalogue update: it does
not rebuild the index and does not block reads. Where *both* names already exist
— which happens on the live database, because some indexes came from a migration
and some from `create_all()` — the migration-named duplicate is dropped instead.
Left alone it is a second copy of the same b-tree that every write has to
maintain, and it reappears in `alembic check` forever.

**Autogenerate's drop proposals were discarded.** It proposed dropping
`area1_mutation_log` and its three indexes because that table has no ORM model.
It has no model on purpose: it is written by database triggers and read by
auditors, and giving it a model would put the Area 1 mutation log within reach of
an ORM session. It proposed dropping 43 other indexes that are hand-written
composites and partials the models cannot express — `idx_dim_evidence_entity_dimension`,
`idx_rce_issue_open` — which exist because a query needed them.

**One redundancy was resolved rather than tolerated.** `rce_issues.issue_code`
ended up guaranteed unique three times: a `UNIQUE` constraint from 20260822, a
plain index from 20260822, and the unique index the model declares. Three b-trees
on one column. The migration keeps the one the model declares and drops the other
two, and only after the model's index exists — so uniqueness is never unenforced,
not even inside the transaction. `downgrade()` puts both back first.

### 2.4 Startup DDL is unchanged

`app/main.py` still calls `create_all()` at startup. Nothing about how the
application boots was modified. After this change:

- Alembic knows about all 135 modelled tables
- startup DDL still runs and finds everything already there
- future schema changes have somewhere to go that is not `create_all()`

---

## 3. Issue 2 — the Area 1 revoke hazard

### 3.1 `20260822_rce_pipeline` is frozen

Not modified. A test asserts its original `REVOKE UPDATE, DELETE ON {table} FROM
"{role}"` is still present, so a later edit fails the build rather than passing
silently.

### 3.2 `20260828_area1_grants`

A forward corrective revision, parent `20260827_startup_coverage`. It states the
Phase 4 end state absolutely rather than as a delta, so it lands the same way on
a fresh database and on a drifted one:

```
REVOKE UPDATE, DELETE, TRUNCATE ON rce_source_intakes  FROM <role>
GRANT  SELECT, INSERT              ON rce_source_intakes  TO   <role>
REVOKE UPDATE, DELETE, TRUNCATE ON rce_source_records  FROM <role>
GRANT  SELECT, INSERT              ON rce_source_records  TO   <role>
GRANT  UPDATE (promotion_status, canonical_entity_id) ON rce_source_records TO <role>
```

`TRUNCATE` is in the revoke because it is not covered by `DELETE` and empties
Area 1 just as effectively. The two workflow columns are granted back at column
level because PostgreSQL evaluates UPDATE privilege against the columns a
statement names — which is what lets `promote_delivery` keep working while all
fourteen evidence columns become unwritable.

A test pins the revision's `IMMUTABLE_TABLES`, `MUTABLE_WORKFLOW_COLUMNS` and
`OWNER_ROLE` to `repository.py` by value, so the two cannot drift apart. The
migration does not import the repository: a revision that reads application code
stops being a fixed record of what it did.

### 3.3 A defect the testing found

Applying the design on a fresh database broke Area 1 **inserts**:

```
permission denied for table rce_source_intakes
CONTEXT: SQL statement "SELECT 1 FROM ONLY rce_source_intakes x
                        WHERE id = $1 FOR KEY SHARE OF x"
```

`rce_source_records.source_intake_id` references `rce_source_intakes`, and
PostgreSQL enforces that on every insert with a `FOR KEY SHARE` row lock — which
needs `UPDATE` or `DELETE` on top of `SELECT`. It runs that query **as the owner
of the referenced table** (`ri_triggers.c` switches to `relowner`). So revoking
UPDATE from a role that also *owns* `rce_source_intakes` does not merely make
Area 1 unwritable: it makes it un-insertable, and the RCE ingestion pipeline
stops dead.

This is the sharp end of the ownership prerequisite that
`immutability_grants_sql()` names in a comment. Two states, and the revision
behaves differently in each:

| ownership | what the revision does |
|---|---|
| moved to a role the application cannot authenticate as | nothing extra. The FK check runs as that role, which kept its privileges. The intended state. |
| still the application role (today) | grants `UPDATE (status)` on `rce_source_intakes` — one column, the least consequential one, written once at insert and never updated by any code path in `app/tefca_registry/rce`. It carries no evidence. That single column-level grant satisfies the row lock while `has_table_privilege(..., 'UPDATE')` stays false and every other column stays unwritable. |

The grant is conditional on ownership, so it stops being issued the moment
ownership moves. A logged warning says why it was issued when it is.

### 3.4 Measured result on a fresh database

```
role: docuaction    table owner: docuaction

update promotion_status       True     <- promote_delivery works
update canonical_entity_id    True     <- promote_delivery works
update raw_line               False
update record_sha256          False
update parsed                 False
update npi                    False
delete   rce_source_records   False
truncate rce_source_records   False
insert   rce_source_records   True
select   rce_source_records   True

promotion_status write, row actually updated : PASS
raw_line write refused (permission denied)   : PASS
```

### 3.5 Downgrade

Restores what `20260822_rce_pipeline` leaves behind — the blanket revoke plus
`SELECT, INSERT` — because that is what the revision immediately before this one
produces. It does not restore whatever a particular database happened to have
before the chain was ever run; a downgrade returns you to the previous
revision's state, not to your history. No data is touched.

### 3.6 Transactional DDL — is the intermediate state observable?

**Measured, not assumed. No.**

*Does `alembic upgrade head` use one transaction or one per revision?* env.py
wraps `run_migrations()` in `context.begin_transaction()` and does not set
`transaction_per_migration`. Proven by forcing a mid-chain failure: afterwards
`alembic_version` was still at the starting revision and **zero** tables from the
revisions after the failure point survived. One transaction for the whole
upgrade.

*Are GRANT/REVOKE transactional?* Yes:

| observer | state |
|---|---|
| another session, before the REVOKE | `UPDATE = True` |
| another session, REVOKE issued but **uncommitted** | `UPDATE = True` |
| the revoking session itself, same moment | `UPDATE = False` |
| another session, after ROLLBACK | `UPDATE = True` |

So during a single `upgrade head`, no other session can observe the window
between 20260822's blanket revoke and 20260828's correction. **One caveat worth
stating:** the migration's own session *does* see it. Anything running inside the
migration transaction between those two revisions would hit the broken state,
which is exactly why the correction is a later revision in the same transaction
rather than a script run afterwards.

---

## 4. Issue 3 — drift

Measured with the real `alembic check`, through env.py, against two databases.

| | before | after |
|---|---|---|
| empty database built by `upgrade head` | not measurable — the chain could not reach head | **0 operations** |
| disposable clone of the live schema | 260 operations reported against a 47-table comparison | **0 operations** |

Everything that had been reported is now in one of these buckets:

| classification | count | what it was |
|---|---|---|
| RESOLVED | 80 tables | created by `20260827_startup_coverage` |
| RESOLVED | 41 indexes | renamed to the model's name, or the duplicate dropped |
| RESOLVED | 13 indexes | model-declared, now created |
| RESOLVED | 2 constraints | the redundant `rce_issues_issue_code_key` and `idx_rce_issue_code` |
| EXPECTED | 12 tables | no ORM model **by design** — declared in `UNMODELLED_TABLES` with the reason written next to each |
| EXPECTED | 4 indexes | hand-written composites/partials the models cannot express — `MIGRATION_OWNED_INDEXES` |
| NEEDS_MIGRATION | 0 | |
| NEEDS_INVESTIGATION | 0 | |

The twelve EXPECTED tables are `area1_mutation_log` (written by triggers, read
by auditors — a model would put it within reach of an ORM session) and eleven
created by hand-written `CREATE TABLE IF NOT EXISTS` at startup, in
`app/bulletin_intelligence/bulletin_store.py` and `app/Tefca/qa_engine.py`.
Bringing those eleven under Alembic means writing models or migrations inside two
subsystems this work is not authorised to touch, so it is named as outstanding
rather than done badly. It is the one piece of "Alembic has complete authority"
still missing, and it is bounded: eleven tables, two files, both known.

One of the eleven turned up only when the application was actually booted against
a chain-built database. `bulletin_recipients` does not exist on the current live
database, so no amount of comparing against live would have found it — the
argument for booting the app in Scenario A rather than trusting `alembic check`
alone.

Two near-misses are worth recording because they look like the same problem and
are not. `articles` and `briefings` are created with `CREATE TABLE IF NOT EXISTS`
in `app/bulletin_intelligence/story_repository.py`, but that file drives a local
**SQLite** store. They never appear in PostgreSQL, before or after boot, so they
are not excluded from comparison: naming them would imply Alembic is choosing to
ignore tables that exist.

`alembic heads` → **1** (`20260828_area1_grants`). No merge needed.

---

## 5. Verification

All four scenarios ran the real `alembic` CLI through `env.py` and its asyncpg
engine, inside throwaway schemas of the **separate `docuaction` database**. The
application database `docuaction-db` was only ever read (`pg_dump --schema-only`).

### Scenario A — empty database: **PASS**

```
alembic upgrade head            rc=0
alembic current                 20260828_area1_grants (head)
tables built                    138
modelled tables                 135      modelled but missing: 0
alembic check                   0 operations
```

The 3 extra are `alembic_version`, `area1_mutation_log` and the
`review_effective_determination` view. Schema matches the models: `alembic
check`, which is exactly that comparison, is clean.

**And the application starts on it.** `alembic check` compares metadata; it does
not boot anything. So the schema was built by `upgrade head` alone — no
`create_all()` — and the app was then started against it through `TestClient`,
which runs the startup event:

```
alembic upgrade head (no create_all)   rc=0
application start                      PASS
GET /health                            200
tables after boot                      148   (+11, all raw-SQL bulletin tables)
alembic check after the app booted     clean
```

The eleven tables startup still adds are the raw-SQL ones from §4 — outstanding
item 1, not a coverage gap in the migration. Nothing the models declare was left
for `create_all()` to create.

### Scenario B — drifted database: **PASS (executed, not `--sql`)**

A disposable clone of the live schema (`pg_dump --schema-only` replayed into a
throwaway schema), stamped at the revision the live database records, then
migrated for real. This is execution, not offline rendering.

```
clone                           87 tables, 348 indexes, stamped 20260627_tefca_dashboard
alembic upgrade head            rc=0
alembic current                 20260828_area1_grants (head)
tables                          87 -> 148   (61 new, all from the coverage revision)
indexes                         348 -> 453
modelled but missing            0
alembic check                   0 operations
```

**No historical data mutation.** The clone is schema-only, so there is no data in
it to mutate; the guarantee for the real database is that no revision in the
chain issues an INSERT, UPDATE or DELETE, which a test enforces per revision. The
live database's own integrity digests were re-measured after all of this work and
are unchanged — see below.

### Scenario C — chain integrity: **PASS**

```
downgrade -> 20260827_startup_coverage   rc=0
downgrade -> 20260826_area1_audit        rc=0
downgrade -> 20260825_qa_events          rc=0
downgrade -> 20260824_evidence_prov      rc=0
downgrade -> 20260823_vocab_version      rc=0
downgrade -> 20260822_rce_pipeline       rc=0
upgrade head again                       rc=0
tables restored after re-upgrade         True
alembic heads                            1
```

The four overnight migrations downgrade and re-apply cleanly, and so do the two
new ones. Getting there needed one fix: `DROP TYPE` in the coverage revision's
downgrade failed on `casestatus`, which `tefca_priority_cases` — a table the
chain owns and this revision did not create — also uses. A type something still
depends on is now left in place, which is the correct outcome.

Linear chain, base to head, no orphans, one head.

### Scenario D — idempotency: **PASS**

`alembic upgrade head` a second time: `rc=0`, and the table, index and column
sets are identical to the first run.

### Tests and integrity gates

| gate | result |
|---|---|
| Full suite | **1501 passed, 40 skipped, 0 failed** in 11m46s (1440 baseline + 61 migration-chain tests) |
| `tests/test_migration_chain.py` | 61 tests |
| Reconciliation gate | **18 checks, 0 failed** |
| Area 1 hash revalidation | **23,566 recomputed, 0 mismatches** |
| Evidence integrity | 1,984 rows, `eca047f9bdf4afb8567c43c83325fa92` — **unchanged** |
| Determination integrity | 43 rows, `a6fa52f503f6cf35dbe9d85bfaaadf2f` — **unchanged** |
| Area 1 record digest | `d65e51cfbd424bab7ad1703d4a1fba98` — **unchanged** |
| `audit_logs` | 251 rows, 251 NULL in each column, 0 `ix_` indexes — **unchanged** |
| `alembic_version` | `20260627_tefca_dashboard` — **unchanged** |

---

## 6. Reconciliation — ready, and what it should be

The chain is now runnable end to end on a copy of the live schema, converging to
head with zero drift. That changes what the reconciliation step should be.

**A stamp is not the right instrument any more.** Stamping records eleven
revisions as applied without executing them. Scenario B shows what executing them
does to this schema: it creates 61 tables the application needs and that the
database does not have, plus the indexes, and changes no row. Stamping would mark
all of that as delivered while leaving it absent.

**Recommended, in a maintenance window, after a `pg_dump -Fc` restore point:**

```bash
alembic current                 # expect 20260627_tefca_dashboard
alembic upgrade head
alembic current                 # expect 20260828_area1_grants (head)
alembic check                   # expect: No new upgrade operations detected
```

Two things to know before authorising it, both consequences of running rather
than stamping:

1. It applies the Area 1 privilege change. After it, the application role holds
   `SELECT, INSERT` on both Area 1 tables, `UPDATE` on
   `(promotion_status, canonical_entity_id)` of `rce_source_records`, and
   `UPDATE (status)` on `rce_source_intakes` for the foreign-key row lock. It
   holds no other UPDATE, no DELETE and no TRUNCATE. That is the Phase 4 design,
   and `verify_immutable()` will report `enforced: True` for the first time.
2. The 61 `CREATE TABLE`s and ~105 `CREATE INDEX`es take locks. The largest table
   involved holds 23,566 rows, so this is seconds — but it is write-blocking and
   belongs in a window.

**If a stamp is preferred anyway** — for example to separate the schema change
from the privilege change — the command is:

```bash
alembic stamp head
```

Its consequence, stated plainly: the 61 tables and ~105 indexes stay missing, the
Area 1 privileges stay as they are, and the chain records work as done that was
not done. A later `upgrade` cannot fix it, because the revisions would be marked
applied.

**Not executed. Awaiting authorisation.**

---

## 7. Still outstanding

| # | item | why it was not done here |
|---|---|---|
| 1 | Eleven tables — ten `bulletin_*` and `tefca_qa_audit` — have no ORM model and no migration; they are created by raw `CREATE TABLE IF NOT EXISTS` at startup. | Fixing it means writing models or migrations inside `app/bulletin_intelligence/` and `app/Tefca/qa_engine.py`. `app/bulletin_intelligence/` is explicitly out of scope. |
| 2 | Area 1 ownership still belongs to the application role. | Creating `docuaction_owner` needs a superuser and is a deployment step. Until it happens the revoke is self-reversible, and the FK row lock needs the one-column grant described in §3.3. |
| 3 | `20260725_tefca_registry` still reads `TEFCA_REG_TABLE_ORDER` at runtime. | Unchanged from the previous report (§8.1 there). Recommendation stands: freeze it and add a new revision for the eight tables added since. |
| 4 | `20260725_tefca_registry`'s downgrade cannot drop its enum types. | Pre-existing. The same `dependent_objects_still_exist` guard used in `20260827` would fix it. |
| 5 | The stale 9-column `User` model on `app.database.Base`. | Excluded from comparison, not deleted. Deleting a model is an application change. |
