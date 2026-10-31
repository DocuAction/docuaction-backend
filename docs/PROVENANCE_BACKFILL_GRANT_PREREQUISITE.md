# Provenance backfill — grant prerequisite (DRAFT, for DBA review)

Companion to `docs/PROVENANCE_BACKFILL_GRANT_PREREQUISITE.sql` (the runbook).
Status: **written for review, not applied anywhere; nothing here was read from
DEV or PROD except where a run log or live API read is explicitly cited.**

## 1. The failure this repairs

Governed release run
[41170540825](https://github.com/DocuAction/docuaction-backend/actions/runs/41170540825)
failed applying `20261030_default_routing_prov`:

```
permission denied for table review_records
```

The run's own log proves everything upstream worked: `session_user =
github-actions-docuaction-backend-dev | current_user = docuaction_owner`,
`expected_current` matched, the direct-child gate passed, Alembic began the
single approved migration, and its FIRST statement (the backfill's SELECT) was
refused. Nothing persisted — verified live afterwards: `migration_revision`
still `20261012_sod_db_enforcement`, the one rule-less review unchanged, no
backfill audit row. The run's temporary /32 was removed.

## 2. Why a GRANT runbook and not an ownership transfer

The first repair attempt (PR #192 as originally opened) cloned the
`ownership-fix-rce-issues.yml` one-time ALTER-OWNER workflow. Validation
against the repository's own record killed it, twice over:

1. **It would not work.** The identical workflow FAILED on 2026-09-16 (run
   35158910794) against `rce_issues`, owned — like `review_records` — by
   `docuaction_app`: `docuaction_owner` cannot reassign an object it does not
   own, and the pipeline identity holds no membership in `docuaction_app`
   (`permission denied to set role "docuaction_app"`, in that run's log).
2. **It would be wrong even if it worked.**
   `docs/SOD_DB_OWNERSHIP_PREREQUISITE.md` §5 records that the managed
   convergence chain DELIBERATELY keeps `review_records` runtime-owned:
   PREPARE temporarily re-owns it for DDL and FINALIZE hands it back —
   "access by ownership". Whether any EXPLICIT grants also exist on the live
   table is unverified from here (the PRE-CHECKS read the actual ACL, and the
   DBA must confirm it before APPLY); if the runtime role's access rests on
   ownership alone — the documented convention — a permanent transfer to
   `docuaction_owner` would have stripped the application's entire access to
   `review_records`, while the workflow's grants-unchanged check passed. The
   application's review, reconciliation and reporting paths all read this
   table, so that risk alone disqualifies the transfer shape.

The smallest correct change is therefore two narrow GRANTs **to the migration
role**, leaving ownership and the runtime role untouched:

| Object | Grant to `docuaction_owner` | Why |
|---|---|---|
| `public.review_records` | `SELECT, UPDATE` | backfill selector + the 1-row UPDATE (and the downgrade's revert) |
| `public.tefca_reg_audit_log` | `SELECT, INSERT` | the per-row audit INSERT (and the downgrade's audit-row lookup) |

Deliberately absent: `DELETE`/`TRUNCATE` anywhere; `UPDATE`/`DELETE` on the
audit log (append-only to everyone). The runbook's in-transaction POST-CHECK
asserts both the presence of the approved set and the absence of anything
more, and that ownership did not change — any failure rolls back everything.

## 3. Who runs it, and when

A human DBA (in practice: the server's Entra administrator, who can `SET ROLE
docuaction_app`), over an authorized connection to
`docuaction-db-dev.postgres.database.azure.com` — e.g. the operator's own
temporary workstation /32. Not CI, not the pipeline identity, not the app.

Order: PRE-CHECKS (read-only, save output — includes the live ACLs of both
tables, which the DBA confirms before APPLY, and the expected affected-set
count, which must be exactly 1) → APPLY (one transaction,
`psql -v ON_ERROR_STOP=1`) → POST-CHECKS (save output) → re-dispatch the
governed release unchanged (`apply_migrations=true`,
`expected_current=20261012_sod_db_enforcement`,
`target_revision=20261030_default_routing_prov`, `handshake_issue=191`).

Idempotent; a ROLLBACK (REVOKE) section is included as reference text. DEV
only in intent; the same question will come back for PROD at its own gate and
is NOT pre-authorized by this document.
