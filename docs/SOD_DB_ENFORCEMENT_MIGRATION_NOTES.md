# SoD database enforcement - migration notes (DRAFT)

Migration: `alembic/versions/20261012_sod_db_enforcement.py`
(`20261012_sod_db_enforcement`, down_revision `20261009_iqvia_affil_org_indexes`)

Status: **implemented, tested locally on a scratch PostgreSQL, not deployed, not verified on DEV.**
It is a file in a draft PR. It has not been applied to DEV or PROD and must not be, except through
the governed migration pipeline after the prerequisites below are verified.

Related: PR #146 (service-level fail-closed, `docs/SOD_EXCEPTION_AUTHORIZATION_DESIGN.md`). That PR
makes the application refuse self-QA; this change makes the database refuse it too, so a future code
path, script or direct INSERT cannot restore the old behaviour.

## 1. What changes

| Object | Before | After |
|---|---|---|
| `review_event_enforce_sod()` (trigger `trg_review_event_sod`, BEFORE INSERT) | a QA event by the determination's actor was allowed if `sod_exception_granted_by` (different UUID) and `sod_exception_reason` were present | such an event is refused whatever those columns contain; every other branch is byte-identical |
| `trg_review_event_append_only_rows` (new, BEFORE UPDATE OR DELETE, row) | none | UPDATE refused for every role; DELETE refused unless the caller holds the table owner's privileges |
| `trg_review_event_append_only_trunc` (new, BEFORE TRUNCATE, statement) | none | TRUNCATE refused unless the caller holds the table owner's privileges |
| rows in `review_decision_events` | - | **not read, scanned, rewritten or deleted** |
| GRANT / REVOKE / ownership | - | **none** (see 5) |

"Self-QA" in the trigger is exactly what the original function body meant: a row with
`event_type = 'QA_REVIEW'` whose `actor_user_id` equals the actor of the most recent
`ANALYST_DETERMINATION` or `SUPERSEDING_DETERMINATION` on the same `review_id` with a lower
`sequence_number`. Non-QA events pass through; a QA event with no preceding determination is still
refused. The message keeps the `segregation of duties:` prefix.

Append-only is consistent with the documented model: the model docstring and migration 20260825 call
the table append-only; `app/` and `scripts/` contain only INSERTs (`qa_gate.record_analyst_determination`,
`submit_qa_review`, `supersede`); supersession is a new row naming `supersedes_decision_id`. The only
DELETEs of these rows in the repository are test-cleanup statements run as a privileged role
(`tests/support_delivery_api.py`, `tests/test_report_storage_durable.py`), which is why DELETE and
TRUNCATE keep an owner carve-out while UPDATE does not.

Historical rows are not invalidated. A historical event that carries an exception (accepted by the
old trigger) stays readable and unchanged; the new trigger fires only on new INSERTs. Test:
`TestMigrationLifecycle` seeds one before the upgrade and compares `md5(row)` and `xmin` after.

## 2. Prerequisites (verify BEFORE running) - all UNVERIFIED on DEV and PROD

The statements need DDL rights. On DEV and PROD several chain tables are owned by `docuaction_app`
because startup `create_all` created them (see `qa-evidence/.../dev_verified.md`: `review_records`
and `tefca_entity_relationships` were app-owned on DEV on 2026-09-21; PROD's `docuaction_app` owns
89 tables). Whether `review_decision_events` and `review_event_enforce_sod()` are owned by
`docuaction_owner` or by `docuaction_app` on either environment has **not** been read. The migration
therefore refuses to start (before any DDL, error `SodDbEnforcementPreconditionError`,
"Nothing was changed") when:

* the function already exists and the migration role does not hold the privileges of its owner
  (`CREATE OR REPLACE FUNCTION` needs ownership). A function or trigger that is ABSENT (a database
  built from the models and stamped, never run through 20260825) is created by this migration;
* the migration role is neither the table owner nor holds `TRIGGER` on the table;
* the runtime role (`DB_APP_ROLE`) holds the privileges of the table owner or function owner. The
  runtime role could then `ALTER TABLE ... DISABLE TRIGGER` or replace the function, and the
  migration would be installing a control it cannot rely on.

If any of these fires, a DBA must first move ownership of `review_decision_events` and the function to
`docuaction_owner` and give the runtime role only the privileges it needs (`SELECT, INSERT` on the
table; see 5). That is a separate, approved change; this migration does not do it. Note also that the
PROD "managed convergence" tooling (`scripts/prod_legacy_convergence.py`, `MANAGED_CHAIN_ALTERS`)
temporarily re-owns only four tables to the owner role; `review_decision_events` is not in that list,
so the PROD convergence path will hit this preflight until ownership is arranged or that tooling is
extended (a decision for the PROD runbook owner; not changed here).

### Read-only catalog queries (run in one `BEGIN TRANSACTION READ ONLY` session; no writes)

```sql
BEGIN TRANSACTION READ ONLY;
-- current Alembic revision: expect 20261009_iqvia_affil_org_indexes before this migration
SELECT version_num FROM alembic_version;

-- who owns the table, and what is its ACL?
SELECT c.relname, c.relowner::regrole AS owner, c.relacl, c.relhastriggers, c.relrowsecurity
FROM   pg_class c WHERE c.oid = 'public.review_decision_events'::regclass;

-- who owns the SoD function, and is it SECURITY DEFINER?
SELECT p.proname, p.proowner::regrole AS owner, p.prosecdef, md5(p.prosrc) AS body_md5
FROM   pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE  n.nspname = 'public' AND p.proname = 'review_event_enforce_sod';

-- the triggers on the table, and whether they are enabled ('O' = enabled)
SELECT t.tgname, t.tgenabled::text, pg_get_triggerdef(t.oid)
FROM   pg_trigger t WHERE t.tgrelid = 'public.review_decision_events'::regclass AND NOT t.tgisinternal;

-- role attributes and memberships (is the runtime role a member of the owner role, or the reverse?)
SELECT rolname, rolsuper, rolcreaterole, rolbypassrls, rolcanlogin FROM pg_roles
WHERE  rolname IN ('docuaction_owner','docuaction_app');
SELECT r.rolname AS role, m.rolname AS member_of
FROM   pg_auth_members am JOIN pg_roles r ON r.oid = am.member JOIN pg_roles m ON m.oid = am.roleid
WHERE  r.rolname IN ('docuaction_owner','docuaction_app') OR m.rolname IN ('docuaction_owner','docuaction_app');

-- what can the runtime role do to the table today?
SELECT p AS privilege, has_table_privilege('docuaction_app', 'public.review_decision_events', p) AS runtime_role_has
FROM   unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS p;
-- does the runtime role hold the table-owner's privileges (which would let it DISABLE TRIGGER)?
SELECT pg_has_role('docuaction_app', c.relowner, 'USAGE') AS app_has_table_owner_privs,
       pg_has_role('docuaction_app', (SELECT proowner FROM pg_proc WHERE proname='review_event_enforce_sod'), 'USAGE') AS app_has_function_owner_privs
FROM   pg_class c WHERE c.oid = 'public.review_decision_events'::regclass;

-- the identity the pipeline runs as: can it act as the owner role?
SELECT current_user, pg_has_role(current_user, 'docuaction_owner', 'USAGE') AS migration_can_use_owner;

-- history to be preserved: how many events, and how many carry an exception? (aggregate only)
SELECT count(*) AS events,
       count(*) FILTER (WHERE sod_exception_granted_by IS NOT NULL) AS with_exception_fields,
       count(*) FILTER (WHERE event_type='QA_REVIEW' AND sod_exception_granted_by IS NOT NULL) AS qa_with_exception
FROM   review_decision_events;
ROLLBACK;
```

Decision table from the answers: owner of table and function is `docuaction_owner` **and**
`app_has_*_owner_privs` is false -> the migration will run. Anything else -> do not run; arrange
ownership first. Do not open a firewall rule for these queries from this repository's automation;
they are for a DBA session.

### Known CI consequence of this prerequisite

The `fixture` job (`convergence-fixture.yml`, runs `tests/test_prod_managed_migration_integration.py`)
builds a PROD-like database in which `docuaction_app` owns `review_decision_events`, and applies the
chain as a migration identity that is a member of `docuaction_owner` only. With this revision that
test **fails at the preflight** (`review_decision_events is owned by docuaction_app ... Nothing was
changed`); on main the same two files pass (9 passed). This is the preflight doing its job, not a
defect in the fixture: the managed PROD path cannot install a trigger on an app-owned table. Resolving
it is an owner decision, not made here: (A) a DBA moves `review_decision_events` and the function to
`docuaction_owner` with `SELECT, INSERT` for the runtime role (the `20260917` convention; recommended,
because it is also what makes the guard unbypassable by the runtime role), then the fixture is updated
to model that; or (B) extend `MANAGED_CHAIN_ALTERS` so PREPARE re-owns the table for the chain - but
FINALIZE would hand it back to `docuaction_app`, leaving the guard removable by the runtime role.
Ownership is not transferred by this change.

## 3. Upgrade steps (governed pipeline only)

1. Verify section 2 on the target. Record the results.
2. Merge the draft PR through the normal review (not part of this change).
3. Run the governed migration job (`apply_migrations`) with `DB_MIGRATION_ROLE=docuaction_owner` and
   `DB_APP_ROLE=docuaction_app`. Alembic runs the revision in one transaction (PostgreSQL
   transactional DDL), so a failure leaves the database unchanged.
4. Verify (read-only): the three triggers exist with `tgenabled = 'O'`; `md5(prosrc)` of
   `review_event_enforce_sod` differs from the pre-upgrade value; `SELECT count(*) FROM
   review_decision_events` equals the pre-upgrade count.
5. Deploy the application image that contains PR #146 (the service-level refusal). Order is not
   critical: either layer alone already refuses self-QA.

Lock behaviour: `CREATE OR REPLACE FUNCTION` and `CREATE TRIGGER` take a short `SHARE ROW EXCLUSIVE`
lock on `review_decision_events` (writers wait briefly); no table scan.

## 4. Rollback and its limits

`alembic downgrade -1` restores the exact 20260825 function body (tested: `md5(prosrc)` equals the
pre-upgrade value; `trg_review_event_sod` is left in place, and if this revision had to create it
because it was absent it is left too - it is the documented 20260825 design), drops `trg_review_event_append_only_rows`, `trg_review_event_append_only_trunc`
and `review_event_append_only_guard()`. It reads and changes no row.

Limits: after a downgrade the old behaviour returns, i.e. a self-QA event carrying a different grantor
and a reason is accepted by the database again (the service-level refusal in PR #146, if deployed,
still refuses it). Nothing inserted while the new version was in force needs repair, because no
self-QA row could have been inserted. A downgrade cannot restore anything that was deleted by the
owner (the owner carve-out) - deletion is not something this migration causes or reverses.

## 5. Privileges: deliberately unchanged

No GRANT or REVOKE is issued. The runtime role's privileges on this table are not verified on DEV or
PROD, and the controls here are triggers, not ACLs. A follow-up that would be consistent with the
repository's append-only convention (`20260917_delivery_traceability`: `SELECT`+`INSERT` only) is
`REVOKE UPDATE, DELETE, TRUNCATE ON review_decision_events FROM docuaction_app`, but only after the
query in section 2 shows what is granted and that the table is not owned by the runtime role (a
REVOKE on a table the role owns is meaningless). Ownership of the table and function is not
transferred by this change.

## 6. Residual risks

* **Owner-role bypass.** The table owner (and superusers) can `ALTER TABLE ... DISABLE TRIGGER`,
  `DELETE`, `TRUNCATE` (carve-out), `ALTER FUNCTION`, `session_replication_role = replica`, or
  re-create the function. Triggers are a guard against application code paths and the runtime role,
  not against a privileged operator. Mitigations that are outside this change: restricted membership
  of the owner role, DDL auditing (`pgaudit` or Azure diagnostic logs), and the migration record.
* **Runtime role owning the objects.** If the runtime role owns the table or function the guards can
  be removed by it. The preflight refuses to install them in that state.
* **Historical rows with exceptions remain valid.** Not re-reviewed or invalidated. The aggregate
  query in section 2 tells you how many exist; reviewing them is a separate decision.
* **UPDATE is refused even for the owner.** Any future legitimate "correct an event" need must be a
  new event, or a deliberate `DISABLE TRIGGER` window recorded in the change record.
* **Test-suite impact.** Cleanup that runs as the runtime role would fail; the repository's suites run
  as a privileged role (CI: `postgres`), which keeps the DELETE carve-out. The shared test databases
  are migrated to head, so the guards are active in the `isolation-postgres` job.
* **Another open migration** with `down_revision = 20261009_iqvia_affil_org_indexes` would create a
  second head; as of this change none exists on the fetched remote branches. `alembic heads` must show
  one head before merge.

## 7. Tests

`tests/test_sod_db_enforcement_2026_10_12.py` (real PostgreSQL; listed in the `isolation-postgres` job):

```
export SECRET_KEY=$(python -c "print('t'*64)") ALLOWED_HOSTS='*' DB_MIGRATION_ROLE=docuaction_owner DB_APP_ROLE=docuaction_app
export DATABASE_URL='postgresql+asyncpg://postgres@127.0.0.1:5531/test'   # scratch cluster, roles docuaction_owner/docuaction_app
python -m alembic upgrade head
python -m pytest tests/test_sod_db_enforcement_2026_10_12.py -q
```

Layer 1 runs in rolled-back transactions on the migrated database with `SET ROLE docuaction_app` /
`docuaction_owner`; layer 2 creates and drops throw-away databases and drives Alembic as a subprocess.
