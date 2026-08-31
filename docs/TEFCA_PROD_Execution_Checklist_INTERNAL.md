# TEFCA ARC — Production Execution Checklist

**Internal engineering record. Not a Government deliverable. No secrets, no
credentials, no Government values.**
Contract 7571MN26F80064 · prepared at Step #18B · 31 August 2026

**NOTHING HERE HAS BEEN EXECUTED.** This is the production sequence built from
the DEV configuration that Step #18B proved. Every step names what DEV did, so
production repeats a rehearsed action rather than a designed one.

**This checklist does not authorize itself.** It needs a named approver, a named
release, and — for anything touching Government data — a separate governance
decision that engineering may not make.

---

## 1. PROD backup and precheck

- [ ] Recovery point later than the last write, recorded by timestamp
- [ ] **PITR rehearsed on PROD** the way DEV was: restore to an isolated
      server, validate read-only, delete. DEV took **~7 minutes**; PROD is the
      same tier and should be comparable, but that is an expectation until it
      is measured
- [ ] Approved release ref — a tag or commit, not a branch, not "latest"
- [ ] Current PROD state captured: image digest, Alembic head, app-setting
      **names**, instance count
- [ ] Expected Government data state recorded, so "unchanged" is provable after

## 2. PROD database role and ownership

- [ ] Enable Entra authentication **alongside** password auth (DEV: nothing was
      disabled) and establish an Entra administrator
- [ ] Verify from a token-authenticated session — **never by reading the stored
      password**:
      - [ ] `docuaction_owner` and `docuaction_app` exist
      - [ ] Area 1 tables owned by `docuaction_owner`
      - [ ] `docuaction_app` holds **INSERT, SELECT only** on Area 1
      - [ ] `docuaction_app` **cannot** CREATE in `public`
- [ ] If CREATE is held, revoke it as DEV did:
      `REVOKE CREATE ON SCHEMA public FROM docuaction_app` and `FROM PUBLIC`;
      `GRANT CREATE, USAGE ON SCHEMA public TO docuaction_owner`
- [ ] Re-run the 13 permission probes as `docuaction_app` via `SET ROLE`, inside
      a rolled-back transaction, targeting **no Government row**
- [ ] All 13 as expected before proceeding

> **Expect PROD to differ from DEV here.** Ownership was never transferred in
> production. If the runtime connects as an administrator, Area 1 immutability
> is inert and **this is a stop condition**, not a note.

## 3. PROD Key Vault

- [ ] Confirm the app identity holds **Key Vault Secrets User** (it already did
      on both vaults at Step #18)
- [ ] Obtain data-plane access **without opening the vault**: public access
      `Enabled` with `defaultAction: Deny` and **a single IP rule**, plus a
      temporary Secrets Officer grant — exactly the DEV pattern
- [ ] Migrate all eight PROD secrets, values never printed:
      `SECRET_KEY`, `ANTHROPIC_API_KEY`, `AZURE_AD_CLIENT_SECRET`,
      `SENDGRID_API_KEY`, `DATABASE_URL`, `SAM_GOV_API_KEY`,
      `USPS_CLIENT_SECRET`, `PERIGON_API_KEY`
- [ ] Re-point settings to `@Microsoft.KeyVault(VaultName=…;SecretName=…)`
      — apply via a **JSON file**; the shell mangles the parentheses
- [ ] Restart, then confirm **every** reference reports `Resolved` on
      `config/configreferences/appsettings`
- [ ] **Revert both**: `publicNetworkAccess` to `Disabled`, remove the temporary
      role. Verify.
- [ ] Extend the IaC so the four secrets it never covered are declared

## 4. PROD Blob storage

- [ ] Storage account: StorageV2, HTTPS only, TLS 1.2, blob public access
      **disabled**, **shared-key access disabled** (DEV: no storage key exists
      to leak)
- [ ] Private container `report-artifacts`, no anonymous access
- [ ] App identity granted **Storage Blob Data Contributor**, scoped to the
      account
- [ ] Private endpoint into the PROD VNet; public access disabled
- [ ] Consider GRS rather than LRS for production durability
- [ ] Set `REPORT_ARTIFACT_AZURE_ACCOUNT` and `_CONTAINER`
- [ ] **Do not set `REPORT_ARTIFACT_BACKEND=azure` until the image carrying the
      implementation is deployed** — configuration ahead of code breaks artifact
      writes. This is the mistake DEV made and reverted.

## 5. PROD networking

- [ ] Confirm no broad `AllowAllAzureServices` rule (PROD did not have one)
- [ ] Cover **all possible** outbound IPs, not just the active set, before any
      rule removal
- [ ] Prefer a **private endpoint or delegated subnet** over an IP allow-list.
      DEV ended with 35 hand-maintained rules; that is fragile, and production
      should not inherit it
- [ ] Verify a database-backed endpoint after any change

## 6. PROD monitoring

- [ ] App Insights and Log Analytics already exist for PROD
- [ ] Confirm the four existing alerts are enabled with a **live** receiver
- [ ] Add the DEV baseline where missing: availability, 5xx, database liveness
- [ ] Decide who is paged for worker and export-job failure — a log-based alert
      with **no named recipient is not monitoring**

## 7. Approved release

- [ ] The workflow already refuses to deploy production from a tag push;
      `deploy-prod` requires an explicit `workflow_dispatch` choosing
      `production` and carries `environment: production`
- [ ] Confirm the GitHub `production` environment **requires reviewers** — the
      workflow declares the gate, but enforcement is a repository setting that
      Step #18 did not verify
- [ ] Deploy by **digest**, never a moving tag

## 8. Migration

- [ ] Run Alembic as **`docuaction_owner`**, as a separate step before the app
      starts. After §2 this is no longer optional: the runtime role cannot
      create objects
- [ ] Repo head equals the single Alembic head; PROD head recorded before
- [ ] `STARTUP_SCHEMA_MUTATION_ENABLED` stays `false`. The guard refuses in
      production and, since Step #18, on a deployed host whose `ENVIRONMENT` has
      gone missing

## 9. Deployment

- [ ] Freeze the window; no other change lands
- [ ] Capture the rollback point
- [ ] Deploy the approved digest
- [ ] Wait for the health probe
- [ ] **Then** flip `REPORT_ARTIFACT_BACKEND=azure` and restart

## 10. Smoke validation

- [ ] Authentication: a known account signs in, an unknown one does not
- [ ] RBAC: a viewer cannot produce an export; a QA lead can
- [ ] `SELECT current_user` returns the runtime role, not an administrator
- [ ] Area 1: DELETE, TRUNCATE and DDL all refused
- [ ] A report renders and states its classification
- [ ] An artifact download re-hashes and matches
- [ ] A **synthetic** export job reaches SUCCEEDED and downloads
- [ ] Artifact survives an app restart — the whole point of §4
- [ ] Data state reports the expected environment, identity and authorization

## 11. Rollback

- **Application**: redeploy the previous digest. The default response to any
  validation failure.
- **Migration**: downgrade is **not** assumed safe — a dropped column loses its
  data. The safe rollback for a schema change is the recovery point from §1,
  which is why that precheck blocks.
- **Configuration**: reapply captured settings by name; values come from Key
  Vault, never from this document.
- After any rollback, **re-run §10**. A rollback is a deployment.

## 12. Initial TEFCA production state

Unless an authorized production state proves otherwise, production starts and
stays at:

    ENVIRONMENT            = PRODUCTION
    Government dataset     = NOT_LOADED
    Dataset identity       = NONE
    Government authorization = ABSENT

- [ ] **Do not copy DEV data to initialise PROD.**
- [ ] **Do not set the authorization marker.** Authority for it is undefined and
      is open Government decision 7.
- [ ] The application should be operationally ready **before** any Government
      ingestion, which is a separate decision.

---

## Stop conditions

Stop, roll back and escalate if:

* the runtime connects as a database administrator;
* `docuaction_app` can create objects;
* Alembic proposes a migration nobody reviewed;
* the application creates tables at startup;
* Area 1 counts or the corpus digest change;
* a report or export is classified GOVERNMENT while the marker is absent;
* an artifact download fails its integrity check;
* a Key Vault reference reports anything but `Resolved`.
