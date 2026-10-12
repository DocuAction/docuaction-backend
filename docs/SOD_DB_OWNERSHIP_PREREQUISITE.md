# SoD database enforcement - ownership prerequisite (DRAFT, for DBA review)

Companion to `docs/SOD_DB_ENFORCEMENT_MIGRATION_NOTES.md` and
`docs/SOD_DB_OWNERSHIP_PREREQUISITE.sql` (the runbook). Status: **implemented, tested locally on a
disposable PostgreSQL fixture, CI not green by design (see 4), not applied anywhere, not verified on
DEV or PROD, awaiting an owner decision and a DBA.**

## 1. Why a prerequisite exists

Migration `20261012_sod_db_enforcement` installs three triggers on `review_decision_events`. Anyone who
owns the table (or is a member of its owner) can `ALTER TABLE ... DISABLE TRIGGER` or `DROP TRIGGER`, and
anyone who owns `review_event_enforce_sod()` can replace it. If the runtime role `docuaction_app` owns
either object, the guards constrain nobody who matters. The migration's preflight
(`SodDbEnforcementPreconditionError`, "Nothing was changed") therefore REFUSES to install them in that
state. That refusal is correct and is retained unchanged; it is what turns the PROD-like convergence
fixture red (see 4).

## 2. The required end state (option A - recommended)

| Object | Owner | `docuaction_app` privileges |
|---|---|---|
| `review_decision_events` | `docuaction_owner` | `SELECT`, `INSERT` only (no `UPDATE`, `DELETE`, `TRUNCATE`, `TRIGGER`, `REFERENCES`) |
| `review_event_enforce_sod()` / `review_event_append_only_guard()` | `docuaction_owner` | none needed (trigger functions; `EXECUTE` revoked from PUBLIC) |
| sequences owned by table columns | `docuaction_owner` | `USAGE, SELECT` (the model defines none; the runbook handles them if a database has one) |
| view `review_effective_determination` | unchanged | unchanged (a view runs with its owner's rights; the runbook asserts the view owner can still `SELECT` the table) |

This is the same "append-only by grant" convention as `20260917_delivery_traceability` and the
`TRACEABILITY` list in the convergence tests. `docs/SOD_DB_OWNERSHIP_PREREQUISITE.sql` does it in one
transaction with read-only PRE-CHECKS, in-transaction POST-CHECK assertions, a second POST-CHECK report
and a reference ROLLBACK section. It is idempotent (second run changes nothing; tested).

**Order.** (1) run PRE-CHECKS, save output; (2) run APPLY; (3) run POST-CHECKS; (4) the governed pipeline runs
MANAGED PREPARE -> MANAGED MIGRATE -> FINALIZE. The runbook works before or after PREPARE (it grants
`CREATE` on schema `public` to `docuaction_owner` only if missing, as PREPARE does anyway).

## 3. Findings from building the tests (these matter for the owner decision)

1. **The managed gate rejected the end state.** `managed_gate()` required `docuaction_owner` to own exactly the
   four measured Area-1 tables (plus those PREPARE moves). An owner-owned `review_decision_events` made
   `--managed-prepare` and `--managed-migrate` refuse. Fixed in a separate commit
   (`MANAGED_SOD_PREREQUISITE_TABLES` in `scripts/prod_legacy_convergence.py`): the table is tolerated as
   owner-owned. It is NOT added to the measured Area-1 set.
2. **FINALIZE would have undone the DBA's move.** `finalize_ownership()` hands every owner-owned table that is
   not Area-1 to `docuaction_app`, so it would return `review_decision_events` to the runtime role after the
   migration, leaving the guards on a runtime-owned table. Fixed in the same commit: the table is kept
   owner-owned **only when** the runtime role holds `SELECT` and `INSERT` and neither `UPDATE` nor `DELETE`
   (the shape the runbook creates). Anything else is handed back exactly as before, so the change never strips
   access and the legacy path (owner grants nothing, access by ownership) is unchanged. Tested.
3. **A preflight refusal is not a no-op for the chain.** `20261009_iqvia_affil_org_indexes` uses
   `CREATE INDEX CONCURRENTLY` inside alembic's `autocommit_block`, which commits everything applied before
   it. When 20261012's preflight refuses, the earlier pending revisions are already committed and the database
   is left at the revision before 20261009, not at `20260829_report_artifacts`. "Nothing was changed" is true
   of `review_decision_events` and the SoD function only. A re-run after the ownership move resumes from that
   revision. Operationally: do the ownership move BEFORE the pipeline run, not in response to a failure.
   (Asserted in `test_app_owned_fixture_is_refused_by_the_preflight`.)
4. **Legacy-path builds.** On a database built through the legacy path (Bootstrap A / Migration B), the chain
   creates `review_decision_events` as `docuaction_owner` and FINALIZE hands it to `docuaction_app` (access by
   ownership). Such a database would carry the same removable-guard weakness after this migration. Not
   exercised or changed here; it is the same decision in another place.

## 4. How the convergence fixture models this (updated 2026-10-12)

* `_build_managed_prod_like` now also models what PROD is believed to have TODAY from `20260825_qa_events`: the SoD
  function (old body), trigger and effective view, all owned by `docuaction_app` (UNVERIFIED on the real database).
* The existing end-to-end test `test_managed_prepare_migrate_finalize_end_to_end` now performs the real TRANSITION
  first: it seeds historical events, applies `docs/SOD_DB_OWNERSHIP_PREREQUISITE.sql` as a **SIMULATED DBA** (the
  scratch-cluster role `legacy_owner` standing in for the server admin, with the memberships the runbook requires; no
  real DBA or Azure identity), asserts the event rows are untouched, then runs prepare -> migrate -> finalize.
* The retained preflight is exercised by SEPARATE negative tests (`test_app_owned_fixture_is_refused_by_the_preflight`,
  `test_app_owned_table_with_owner_owned_function_is_still_refused`): documented error, "Nothing was changed",
  SoD objects and `review_decision_events` rows byte-identical (count, md5 of rows, md5 of xmin).
* `test_transition_app_owned_to_owner_owned_via_runbook_then_managed_path_succeeds` is the full positive test.

Before ANY governed pipeline run on a real environment: run the runbook PRE-CHECKS read-only and record the live
owners, ACLs and `alembic_version`; the fixture models believed state, it is not evidence.

## 5. Option B - keep the table app-owned and convert the guards into the managed chain (rejected)

Add `review_decision_events` (and the function) to `MANAGED_CHAIN_ALTERS` so PREPARE temporarily re-owns them
to `docuaction_owner` for the chain and FINALIZE hands them back to `docuaction_app`, as is done for
`review_records`. This makes the fixture green with a one-line change. Rejected because:

* after FINALIZE the runtime role owns the table and function again, so the application account (or any
  SQL-injection / compromised-credential path using it) can `DISABLE TRIGGER` / `DROP TRIGGER` / replace the
  function and then UPDATE or DELETE events. The guards would be installed and unprotected: the control would
  exist on paper only;
* to keep the migration's own preflight satisfied it would also have to be relaxed (the runtime-role-owner check
  would fire at the next migration), i.e. the finding would be hidden rather than fixed;
* it keeps the runtime role holding `UPDATE`/`DELETE`/`TRUNCATE` on an evidence table, contrary to the
  documented append-only model.

Cost of A, for completeness: a one-off DBA change in a maintenance window (short `ACCESS EXCLUSIVE` lock on a
small table), a reviewed runbook, the tooling change in 3.1/3.2, and a re-verification of the application's
read/insert paths on DEV. Residual risk (unchanged by A): the owner role and superusers can still disable
triggers; `session_replication_role = replica` bypasses triggers for roles allowed to set it.

## 6. UNVERIFIED on DEV and PROD (nothing here was read from either environment)

Actual owners of `review_decision_events`, `review_event_enforce_sod()` and `review_effective_determination`;
the runtime role's real ACL on the table and whether PUBLIC holds anything; whether other grantees (reporting
roles) exist; whether a sequence or other dependent object exists; the identity the DBA will run as and its
role memberships; whether `docuaction_owner` already has `CREATE` on schema `public`; the current
`alembic_version`; whether the application has any code path (outside this repository's `app/` and
`scripts/`, which only INSERT) that UPDATEs or DELETEs events - an `UPDATE` revoke would break it.
The PRE-CHECKS section of the runbook reads exactly these.

## 7. Tests (disposable local PostgreSQL only; skip unless `CONV_SUPERUSER_URL` is set)

`tests/test_sod_ownership_convergence_2026_10_12.py` (run in the `fixture` job; the transition test supersedes the former directly-modelled owner-owned test):

* `test_app_owned_fixture_is_refused_by_the_preflight` - positive test of the retained guard.
* `test_app_owned_table_with_owner_owned_function_is_still_refused` - the table-owner leg of the guard.
* `test_transition_app_owned_to_owner_owned_via_runbook_then_managed_path_succeeds` - PRE-CHECKS (read-only), APPLY
  twice (idempotent), POST-CHECKS, then prepare -> migrate -> finalize; historical rows byte-identical (md5 + xmin +
  count); independent QA accepted; self-QA refused; runtime role: SELECT/INSERT only, no UPDATE/DELETE/TRUNCATE,
  cannot disable/drop/alter triggers or replace the function.
* `test_runbook_refuses_wrong_identity_and_changes_nothing` - the runbook's own guards.
* `test_finalize_hands_back_an_owner_owned_table_that_is_not_in_the_append_only_shape` - the tooling change
  never strips access.
