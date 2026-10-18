# D-05: IQVIA staging fails as the runtime role (source_snapshot bookkeeping)

## Failing operation
DEV, 2026-10-06T06:25:28Z, QA case SUN-08. The import scheduler logged:

    permission denied for table source_snapshot
    [SQL: UPDATE source_snapshot SET metadata=$1::JSONB WHERE source_snapshot.id = $2::UUID]

The import job stayed RUNNING and the file was never staged.

## Why it happens
`20260921_september_snapshot` grants the runtime role only SELECT and INSERT on `source_snapshot`
(append-only by grant). The importer updates a PENDING snapshot while it stages: the reference-preflight
record and rejected-row count in `metadata`, progress/status/summary keys in `metadata`, and
`record_count` ("corrected to the real total once the file has been read in full"). Every database-backed
IQVIA test connects as a superuser, so none of them ran these statements as the runtime role.

## Intended lifecycle (why successor rows are NOT the fix here)
- Provenance is immutable: `sha256`, `source_system`, `snapshot_label`, `received_at`, `intake_id`,
  `created_by`, `build_sha`, and the approval lifecycle (`status`, `approved_*`, `supersedes_snapshot_id`).
  Approval, rejection, supersession and rollback already create successor rows. This change keeps all of that.
- `metadata` and `record_count` on a PENDING staging snapshot are documented working state
  (`iqvia_routes` and `iqvia_import` docstrings). A resume is allowed only into a PENDING snapshot, never an
  approved or rejected one. They are not provenance, so a successor row per progress tick would add rows
  without adding evidence.

## The change
`20261006_snapshot_bookkeeping`: `GRANT UPDATE (record_count, metadata) ON source_snapshot TO <DB_APP_ROLE>`.
- Column-level. No table-wide UPDATE, no DELETE, no TRUNCATE.
- Post-assertion inside the migration: it fails and rolls back if the role has table-wide UPDATE or UPDATE
  on any column other than those two.
- Downgrade is a plain REVOKE; no data is touched.
- No schema change, no data change, no code change.

## Regression test
`tests/test_iqvia_import_runtime_privilege_2026_10_06.py` (added to the CI isolation-postgres list):
- runs the real importer under `SET ROLE docuaction_app`; fails at `20261004_recheck_jobs` with the DEV
  error text, passes after the migration;
- asserts the runtime role can update exactly `record_count` and `metadata`;
- asserts UPDATE of `sha256`, `status` and `snapshot_label` is still denied.

## Verification (local, disposable PostgreSQL 18)
- New test: 2 failed / 3 passed before the migration, 5 passed after.
- Migration, IQVIA and convergence suites: 54 passed; with CONV_SUPERUSER_URL on a clean cluster the two
  prod convergence suites: 9 passed.
- Head is a single revision (`20261006_snapshot_bookkeeping`, 29 characters; Alembic's limit is 32).

## Release
One new revision, applied through the governed dev-release workflow: dispatch with `apply_migrations=true`,
`expected_current=20261004_recheck_jobs`, `target_revision=20261006_snapshot_bookkeeping`,
`handshake_issue=93`. DEV only. Rollback limitation is unchanged: this is a forward grant; the downgrade
only removes the grant.

## Addendum 2026-10-06: row-state guard (database enforcement)

A column-level grant cannot distinguish a PENDING staging row from an APPROVED one. Reproduced on a local
PostgreSQL 18 with the grant alone: as `docuaction_app`, `UPDATE source_snapshot SET metadata = ..., record_count = 1`
on an APPROVED snapshot succeeded and rewrote the evidence the approval was given on.

Migration `20261006_snapshot_bookkeeping` therefore also installs `trg_source_snapshot_guard`
(`source_snapshot_guard()`, plain plpgsql, not SECURITY DEFINER), which applies to every role including the owner:

| Operation | Allowed when |
|---|---|
| UPDATE | `OLD.status = NEW.status = 'PENDING'` and every column other than `record_count` and `metadata` is unchanged |
| DELETE | the row is `PENDING` (staging cleanup) |
| INSERT | always (approval, rejection, supersession, rollback remain new rows) |

Everything else raises `source_snapshot_immutable` (SQLSTATE 23000). The comparison is `to_jsonb(NEW) - 'record_count' - 'metadata'`,
so a column added later is protected by default. The migration verifies after installing that the trigger exists and is enabled
(`tgenabled = 'O'`) and refuses to complete otherwise; downgrade drops the trigger and function with the grant.

Bypass: only `ALTER TABLE source_snapshot DISABLE TRIGGER ...` (or `session_replication_role = replica`), both superuser/owner DDL or
session settings that the application and the runtime role cannot perform. The two IQVIA test fixtures that delete approved
synthetic snapshots in teardown now use `SET LOCAL session_replication_role = replica` for that cleanup transaction only.

Not covered by the guard: TRUNCATE (the runtime role has no TRUNCATE privilege; owner-only), and a superuser disabling the trigger.

Tests: `tests/test_source_snapshot_immutability_2026_10_06.py` (30 cases, run as the runtime role and as the owner/superuser). With the
trigger disabled 26 of 30 fail (negative control), which shows the tests exercise the guard.
