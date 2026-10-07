# DEV release automation: sequential migrations and the temporary firewall window

DEV only. Nothing here touches PROD. Nothing is active until the one-time Azure role below is granted and a
release is dispatched with the new inputs.

## What changed

| Before | After (opt-in per dispatch) |
|---|---|
| One migration per dispatch, one approval per dispatch, operator adds and removes a firewall rule each time | `sequential_to_head=true`: every pending revision in ONE approved run |
| Operator firewall rule for the database job and again for post-deploy verification | `manage_firewall=true`: the run opens and closes both windows itself |
| Leftover rules after a failed run | `always()` cleanup that fails the job if it cannot prove removal, plus a sweep of finished runs' leftovers |

Defaults are `false`, so a push, an old-style dispatch and the manual handshake behave exactly as before.

## Guarantees

**Sequencing** (`scripts/release/apply_migrations_sequentially.sh`)
- The plan is a single linear chain from `expected_current` to the repository head; a branch, gap or merge point refuses to run.
- The database revision is read back (not taken from a log line) before and after every step; any mismatch stops the run.
- One `alembic upgrade <revision>` per revision, so one transaction each: a failing revision leaves the database at the last verified one.
- Per revision: identity is the dedicated migration identity assuming `docuaction_owner`; a second upgrade is a no-op; `rce_delivery_jobs` privileges stay SELECT/INSERT/UPDATE with no DELETE.
- Success requires the final revision to equal head. Otherwise the job fails.
- Deployment: unchanged gate. The deploy job still needs `proceed == 'true'`, which now means DEV equals the head the candidate image expects (PR #119), and the post-deploy Government-integrity check still runs.

**Firewall** (`scripts/release/dev_firewall.sh`)
- The rule is the runner's own IPv4, start = end. A private, reserved, zero or malformed address is refused, and a rule that reads back wider than requested is deleted and the run stops. There is no input that widens it.
- Only `temp-run-<run id>` (database phase) and `temp-run-<run id>-gv` (post-deploy verification) can be created, deleted or swept. Standing `devapp-*` rules and manually named rules are never touched.
- The close step runs on success, failure and cancellation, re-authenticates first, retries, and fails the job with the exact delete command if removal cannot be confirmed. The next run's sweep removes rules whose run has completed.

## One-time administrator configuration (needs Owner or User Access Administrator on the server)

The migration identity `github-actions-docuaction-backend-dev` (object id `1a7b94b5-7618-42ea-9b3f-7994e9032c5d`) has no Azure role on the database server today. Grant the smallest set that covers create, read and delete of firewall rules, scoped to the one DEV server, not the resource group or subscription:

```bash
SUB=6ce81f40-7f0f-4e6d-97e3-2569b4d18611
SERVER_SCOPE="/subscriptions/$SUB/resourceGroups/rg-docuaction-dev/providers/Microsoft.DBforPostgreSQL/flexibleServers/docuaction-db-dev"

az role definition create --subscription $SUB --role-definition '{
  "Name": "DocuAction DEV Postgres Firewall Operator",
  "IsCustom": true,
  "Description": "Create, read and delete firewall rules on the DEV PostgreSQL flexible server only, for the temporary runner /32 of a governed release.",
  "Actions": [
    "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/write",
    "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/delete"
  ],
  "NotActions": [], "DataActions": [], "NotDataActions": [],
  "AssignableScopes": ["'"$SERVER_SCOPE"'"]
}'

az role assignment create --subscription $SUB \
  --assignee-object-id 1a7b94b5-7618-42ea-9b3f-7994e9032c5d --assignee-principal-type ServicePrincipal \
  --role "DocuAction DEV Postgres Firewall Operator" --scope "$SERVER_SCOPE"
```

No Contributor role is needed or wanted. The scripts use `az rest` (not `az postgres flexible-server firewall-rule`) deliberately: the CLI polls a long-running operation through `Microsoft.DBforPostgreSQL/locations/...` actions, which can only be assigned at subscription scope.

Revoke: `az role assignment delete --assignee 1a7b94b5-7618-42ea-9b3f-7994e9032c5d --role "DocuAction DEV Postgres Firewall Operator" --scope "$SERVER_SCOPE"`.

**Validate before relying on it:** dispatch `dev-release.yml` on `main` with `manage_firewall` checked and everything else at its default. With `apply_migrations` off, this opens the window, reads the revision, closes the window and stops at the migration gate, changing nothing in the database. If Azure answers `AuthorizationFailed`, the error names the exact missing action; add only that action to the role.

## Approvals

GitHub asks for a separate approval for every job that declares `environment: development`, and Azure only issues the identity's token to such a job. A full release therefore needs four approvals regardless of how many migrations it applies: image push, database phase, deploy, post-deploy verification. Done by hand, a seven-migration release currently needs about sixteen (two for each of the first six dispatches, four for the last). There is no repository setting that turns these into one, and the safe alternative (a second, reviewer-free environment trusted by the same identity) is not recommended: `main` has no branch protection, so it would be an unreviewed path to the owner role.

## Residual risk

Azure RBAC cannot restrict the address range inside a firewall rule. The limits above live in the script and the reviewed workflow, and each run needs the human environment approval. Anyone with write access to `main` could change those files, so branch protection with required review on `.github/workflows/` and `scripts/release/` is the matching control. That is a repository-administration decision and is not part of this change.

## Using it

```
Run workflow (dev-release.yml, branch main)
  apply_migrations     checked
  sequential_to_head   checked
  manage_firewall      checked
  expected_current     <the revision DEV is at now>
  target_revision      (leave empty)
  handshake_issue      (not needed with manage_firewall)
  migration_confirmed_current  unchecked
```
