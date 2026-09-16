# Migration preflight — 20260917_delivery_traceability

Scope: the single revision this remediation adds. Nothing here was executed
against shared DEV or PROD; every command is for a named operator to run at
Gate 5 or later. No credentials appear in this document.

## Chain order (verified by `alembic heads` / `alembic history` on the branch)

```
20260903_delivery_grants -> 20260915_curated_text_columns -> 20260917_delivery_traceability (head)
```

The chain has exactly one head (`tests/test_migration_chain.py::test_the_chain_has_exactly_one_head`).

## What the revision does

Creates five tables and one view, all owned by the connecting role (the
managed identity that assumes `docuaction_owner` through `DB_MIGRATION_ROLE`),
and grants the runtime role:

| Object | SELECT | INSERT | UPDATE | DELETE |
|---|---|---|---|---|
| rce_delivery_stage_events | yes | yes | yes (close an attempt) | no |
| rce_disposition_events | yes | yes | no | no |
| rce_reconciliation_snapshots | yes | yes | no | no |
| tefca_identifier_decision_events | yes | yes | no | no |
| rce_delivery_report_links | yes | yes | no | no |
| view rce_current_dispositions | yes | n/a | n/a | n/a |

No table is altered. No row is written. The revision is guarded (each create
is skipped when the object already exists) and renders in offline `--sql` mode.

## Ownership and REFERENCES

The new tables declare foreign keys to `rce_delivery_jobs`,
`rce_source_intakes`, `rce_source_records`, `rce_curated_records`,
`tefca_reg_entities`, `rce_issues`, `tefca_entity_versions` and `audit_logs`.
Declaring a foreign key requires the `REFERENCES` privilege on the referenced
table. On DEV, tables created by startup `create_all()` are owned by the
runtime role, so the owner role may lack `REFERENCES` on some of them.

The revision checks this FIRST and, if anything is missing, raises
`TraceabilityPreconditionError` naming the exact statements. Nothing is
created in that case. Expected shape of the operator step (run as the owner of
each named table, as a recorded change):

```sql
GRANT REFERENCES ON "rce_curated_records"   TO "docuaction_owner";
GRANT REFERENCES ON "tefca_reg_entities"    TO "docuaction_owner";
GRANT REFERENCES ON "rce_issues"            TO "docuaction_owner";
GRANT REFERENCES ON "tefca_entity_versions" TO "docuaction_owner";
GRANT REFERENCES ON "audit_logs"            TO "docuaction_owner";
```

Only the tables the preflight actually lists need the grant. The managed
convergence script (`scripts/prod_legacy_convergence.py`, PREPARE step)
issues the same grants for PROD.

## Operator sequence for DEV (not executed)

1. Confirm the current revision (read-only):
   `SELECT version_num FROM alembic_version;` → expected `20260915_curated_text_columns`.
2. Dispatch `dev-release.yml` on `main` after merge with
   `apply_migrations=true`, `expected_current=20260915_curated_text_columns`,
   `handshake_issue=27`. The preflight job runs `alembic upgrade head` with
   `DB_MIGRATION_ROLE=docuaction_owner` and `DB_APP_ROLE=docuaction_app`.
3. If the preflight fails with `TraceabilityPreconditionError`, apply the listed
   `GRANT REFERENCES` statements as a recorded operator step, then re-run.
4. After the apply, verify (read-only):
   ```sql
   SELECT version_num FROM alembic_version;
   SELECT tablename, tableowner FROM pg_tables WHERE tablename IN
     ('rce_delivery_stage_events','rce_disposition_events','rce_reconciliation_snapshots',
      'tefca_identifier_decision_events','rce_delivery_report_links');
   SELECT t, has_table_privilege('docuaction_app', t, 'INSERT') AS ins,
             has_table_privilege('docuaction_app', t, 'UPDATE') AS upd,
             has_table_privilege('docuaction_app', t, 'DELETE') AS del
   FROM unnest(ARRAY['rce_delivery_stage_events','rce_disposition_events',
     'rce_reconciliation_snapshots','tefca_identifier_decision_events',
     'rce_delivery_report_links']) AS t;
   ```
   Expected: owner `docuaction_owner` on all five; `ins` true on all; `upd`
   true only for `rce_delivery_stage_events`; `del` false on all.
5. The two temporary runner `/32` firewall rules are added and deleted by the
   operator within the workflow's handshake windows, as for every release.

## Verified on the isolated cluster

`tests/test_traceability_migration.py` (throwaway database): upgrade head →
downgrade -1 on empty tables → upgrade again → grant matrix as above →
`SET LOCAL ROLE docuaction_app` INSERT succeeds, UPDATE/DELETE/TRUNCATE are
refused → with one evidence row present `alembic downgrade -1` exits non-zero
with `DowngradeWouldDestroyEvidenceError` and the version stays at head → the
view returns the highest-sequence row → the equation CHECK rejects a passing
snapshot whose counts do not sum. Convergence suites on a pristine cluster:
`tests/test_prod_convergence_integration.py` 6/6, `tests/test_prod_managed_migration_integration.py` 3/3.

## Rollback

The tables are additive; the previous application image ignores them. Roll
back by redeploying the previous image digest and leave the tables and their
rows in place. `alembic downgrade` is only possible while the tables are empty,
by design: evidence is never dropped by a rollback.


## Ownership precheck (added 2026-09-16 after independent review F2)

The revision now refuses to run when any of the five evidence tables, or the
`rce_current_dispositions` view, already exists and is **not owned by the role
running the migration** (`TraceabilityOwnershipError`, naming the object and
its owner). Nothing is applied in that case. Startup `create_all` excludes the
five tables (`schema_guard.create_all_except_migration_owned`), so the runtime
role can no longer create them even where startup schema mutation is enabled;
on DEV `STARTUP_SCHEMA_MUTATION_ENABLED=false` in any case. Sequence for DEV
remains: apply the migration as `docuaction_owner` **before** the image switch
(`apply_migrations=true`), then deploy.
