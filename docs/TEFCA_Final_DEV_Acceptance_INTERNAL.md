# TEFCA ARC — Final DEV Acceptance (Step #18B)

**Internal engineering record. Not a Government deliverable. No secrets, no
credentials, no Government row-level values.**
Contract 7571MN26F80064 · Step #18B · 31 August 2026

---

## 1. What this gate closed

All six Step #18A blockers, using the DEV authorization granted for this gate.

| # | Blocker | Result | Evidence |
|---|---|---|---|
| 1 | Azure DEV database identity and roles | **CLOSED** | Entra auth enabled; 13/13 permission probes |
| 2 | Durable artifact storage | **CLOSED** | Blob account provisioned, backend implemented, 21/21 tests against real Azure |
| 3 | PITR restore | **CLOSED** | Restored, validated, deleted — 7 minutes |
| 4 | Key Vault | **CLOSED** | 7 references, all `Resolved` |
| 5 | DEV network | **CLOSED** | Broad rule removed after coverage proven |
| 6 | Monitoring | **CLOSED** | Workspace, App Insights, 3 alerts |

---

## 2. Blocker 1 — database identity and roles

### The path in

Both PostgreSQL servers had `activeDirectoryAuth: Disabled`, so the only
credential was the stored password and Step #18A could not verify anything
without extracting a secret. Entra authentication was enabled on DEV
**alongside** password authentication — nothing was disabled — and an Entra
administrator was established. Every subsequent connection used a short-lived
access token; **no stored password was read at any point**.

### What was found

| | |
|---|---|
| `docuaction_owner` | exists |
| `docuaction_app` | exists, is the runtime role |
| Area 1 tables (`rce_source_intakes`, `rce_source_records`, `rce_ingestion_runs`, `rce_rule_execution_history`) | owned by **`docuaction_owner`**, runtime holds **INSERT, SELECT only** |
| Other 84 tables | owned by `docuaction_app` — migrations have been running as the runtime role |
| `public` schema | owned by `azure_pg_admin` |

**The Area 1 boundary was already correct.** That is the property that matters,
and it holds: the runtime role cannot UPDATE, DELETE or TRUNCATE the immutable
source.

### The defect, and the correction

`docuaction_app` could **CREATE TABLE** in `public`. A role that can create
objects *owns* what it creates, and an owner can always UPDATE and DELETE its
own rows regardless of grants — which is precisely how Area 1 immutability
becomes inert on tables that come into existence at runtime. It is also why 84
of 88 tables are owned by the runtime role.

Corrected:

    REVOKE CREATE ON SCHEMA public FROM docuaction_app
    REVOKE CREATE ON SCHEMA public FROM PUBLIC
    GRANT  CREATE, USAGE ON SCHEMA public TO docuaction_owner

This affects only the creation of NEW objects. Existing tables, their ownership
and every runtime DML operation are untouched — the DEV application was verified
healthy on a database-backed endpoint immediately afterwards.

What it does change is that a **migration run as `docuaction_app` will now
fail**. That is the point: schema changes belong to `docuaction_owner`, and the
database now enforces it rather than leaving it to convention.

### Probes — 13 of 13 as expected

Run as the runtime role via `SET ROLE`, inside a transaction that was rolled
back. No Government row was targeted.

| Probe | Want | Got |
|---|---|---|
| Area 1 UPDATE / DELETE / TRUNCATE (`rce_source_records`) | refused | refused |
| Area 1 UPDATE / DELETE (`rce_source_intakes`) | refused | refused |
| Area 1 TRUNCATE (`rce_ingestion_runs`) | refused | refused |
| CREATE TABLE | refused | refused *(was permitted before the fix)* |
| ALTER / DROP Area 1 | refused | refused |
| Area 1 SELECT | permitted | permitted |
| Area 1 INSERT | permitted | permitted |
| Lifecycle UPDATE (`review_records`) | permitted | permitted |
| Curated UPDATE (Area 2) | permitted | permitted |

---

## 3. Blocker 2 — durable artifact storage

### What was wrong

`AzureBlobArtifactStore` was a declared seam: `put`, `get`, `head` and
`versions` all raised, and the packages were deliberately absent. The local
backend resolved a **relative** path against the container's working directory
with `WEBSITES_ENABLE_APP_SERVICE_STORAGE=false`, so artifacts survived no
restart, deployment or instance replacement.

### What was built

A DEV Storage Account with **shared-key access disabled** — so no storage key
exists to leak, and `DefaultAzureCredential` is the only way in — plus HTTPS
only, TLS 1.2, blob public access disabled, and a private container. The DEV
app's managed identity holds **Storage Blob Data Contributor**, scoped to the
account.

The backend now mirrors the local layout exactly
(`<key>/<version>/artifact.<ext>` plus a JSON record), so a locator is readable
and the two stores are diffable. **Immutability is enforced by the service**:
every write uses `overwrite=False`, so a second write to an existing blob is
refused by Azure rather than by a prior check that would have a race window.

### 21 tests, against the real container

Bytes round-trip · a fresh client still finds the object (durability) · `head`
without the bytes · identical content deduplicates · different content versions
without replacing · **overwrite refused by the service** · missing object ·
**eight hostile locators refused** (other container, traversal, hidden file,
malformed) · bad keys refused before any network call · non-bytes refused ·
unconfigured backend refuses to start · **four concurrent writers produce four
versions, none lost** · configuration selects the backend · no connection
string, account key or SAS anywhere in the source.

---

## 4. Blocker 3 — PITR restore, actually performed

| | |
|---|---|
| Source | `docuaction-db-dev` |
| Restore point | 2026-08-31T17:25:59Z |
| Start | 2026-08-31T17:45:59Z |
| Finish | 2026-08-31T17:52:53Z |
| **Duration** | **~7 minutes** |
| Target | `docuaction-db-pitr-18b`, isolated, never DEV or PROD |
| Cleanup | deleted 18:09:15Z; `rg-docuaction-DEV` holds one server again |

Validated read-only: PostgreSQL 16 · schema present · Alembic
`20260830_run_lifecycle` · 23,566 source / 23,566 curated / 36,916 issues ·
138 HUMAN_REQUIRED · 4 HELD · 1 intake · role and ownership architecture
preserved through the restore.

**Area 1 digest on the restored server: `bdafaf1fa53c85bad3dd5641da526216` —
identical to Azure DEV's own.** The restore is byte-faithful.

### A clarification worth stating plainly

That digest is **not** `3af240c30035b17d5d669a2f8ddbd33a`, and it should not
be. `3af240c…` is the **local developer database**, which is a different
database — as Step #18 recorded when it found the working DATABASE_URL pointed
at `localhost`. Both hold the Government-identical source population and their
source anchors agree exactly (23,566 / 36,916 / 138 / 4); their derived review
records differ (43 locally, 240 on Azure DEV) because they are different
environments with different operational history.

---

## 5. Blocker 4 — Key Vault

Seven secrets migrated and referenced. Values moved programmatically from the
App Service settings into the vault and were **never printed**.

| Setting | Vault secret | Status |
|---|---|---|
| `SECRET_KEY` | `SECRET-KEY` | Resolved |
| `ANTHROPIC_API_KEY` | `ANTHROPIC-API-KEY` | Resolved |
| `AZURE_AD_CLIENT_SECRET` | `AZURE-AD-CLIENT-SECRET` | Resolved |
| `SENDGRID_API_KEY` | `SENDGRID-API-KEY` | Resolved |
| `DATABASE_URL` | `DATABASE-URL` | Resolved |
| `SAM_GOV_API_KEY` | `SAM-GOV-API-KEY` | Resolved |
| `USPS_CLIENT_SECRET` | `USPS-CLIENT-SECRET` | Resolved |

The last three were **not in the original IaC** — Step #18A identified them as
secrets the design had missed, and `DATABASE_URL` is the most sensitive of the
set. All seven resolve through the site's **system-assigned managed identity**.

`DATABASE_URL` resolving is not a claim on paper: the DEV app answers a
database-backed endpoint, which it could not do if the reference had failed.

### How the access was obtained and given back

The vault refuses the public network, and the human held no data-plane role.
Rather than open the vault, public access was enabled with
`defaultAction: Deny` and **a single IP rule** — one address, not the internet —
and a temporary Secrets Officer role was granted. **Both were reverted
immediately after the migration:** `publicNetworkAccess` is `Disabled` again and
the role assignment is gone. Verified in cleanup.

---

## 6. Blocker 5 — DEV network

Step #18A proved the broad `AllowAllAzureServices (0.0.0.0)` rule was what the
DEV app was actually connecting through, so deleting it would have broken DEV.

The order was therefore: cover first, remove second. All **34 possible**
outbound addresses were added as individual rules — not just today's 17 active
ones, because App Service rotates within that set — the DEV app was verified
still reaching its database, and only then was the broad rule removed.

| | |
|---|--:|
| Possible outbound IPs | 34 |
| Already covered | 16 |
| Added | 18 |
| Firewall rules now | 35 |
| **Broad (0.0.0.0) rules** | **0** |

DEV verified healthy on a database-backed endpoint afterwards.

Thirty-five hand-maintained rules is still fragile — a private endpoint or
delegated subnet remains the durable answer, and is recorded for production.

---

## 7. Blocker 6 — monitoring

A Log Analytics workspace (`docuaction-logs-dev`, 30-day retention) and an
Application Insights component (`docuaction-appinsights-dev`) linked to it. The
DEV app carries the connection string and the agent extension.

Three alerts, deliberately few, each answering a question an operator actually
asks:

| Alert | Signal | Severity | Question |
|---|---|---|---|
| `dev-backend-unavailable` | HealthCheckStatus < 1 over 15m | 1 | Is DocuAction up? |
| `dev-backend-5xx` | Http5xx > 10 over 15m | 2 | Is it failing requests? |
| `dev-db-unavailable` | is_db_alive < 1 over 15m | 1 | Can it reach the database? |

They route to the existing `docuaction-alerts` action group. **No recipient was
invented.** Worker, export-job and Key Vault alerting need a log-based query and
a decision about who is paged; both are recorded as remaining operational
configuration rather than guessed at.

---

## 8. DEV deployment — NOT performed, and why

§11 requires the approved workflow and forbids bypassing CI/CD. The `gh` CLI is
not available or authenticated on this host, so the workflow could not be
dispatched. **No deployment was made, and CI/CD was not bypassed.**

This has a consequence that was acted on rather than left latent. The DEV App
Service runs image `52c2b91`, which predates the Blob implementation. With
`REPORT_ARTIFACT_BACKEND=azure` set, that image would construct the **old**
stub and fail on the first artifact write — a latent break introduced by
configuration getting ahead of code.

**`REPORT_ARTIFACT_BACKEND` was therefore reverted to `local` on the DEV app.**
The storage account, container, RBAC, packages and implementation are all in
place and tested; flipping that one setting is a step in the deployment, listed
in the production checklist. DEV was verified healthy after the revert.

---

## 9. Final synthetic E2E

Nine acceptance tests, all passing, on a synthetic delivery inside a rolled-back
transaction.

| Stage | Proven |
|---|---|
| Intake | checksum, 41-field schema fingerprint, record count, headers |
| Area 1 | every row present, each hashed, 41 parsed fields |
| DQ | AUTO_SAFE, HUMAN_REQUIRED and NO_CORRECTION all present; every finding carries its rule-set version |
| Curation | curated derived, HELD present, **the delivered value still lower-case `ma` after curation corrected it to `MA`** |
| Promotion | HELD not promoted; everything else promoted |
| Verification | `unavailable` and `not_found` both present, neither upgraded to a conclusion |
| Analyst / QA | three cases reach APPROVED, RETURNED, ESCALATED through the real gate |
| Reportability | **only the approved case is reportable** |
| Segregation | an analyst approving their own determination is **refused**; an independent reviewer succeeds |
| Workbook | ten sheets in order, 41 fields, no missing, row count reconciles, `01234` preserved, formula-shaped value stored as text with the literal marker |
| **Audit reconstruction** | approval → 2 decision events by **2 distinct actors** → entity → curated → source record → delivery → **the stored row still hashes to its recorded digest** |
| Immutability | a full export does not change one byte of the source |
| Idempotency | a duplicate export request returns the same job |

---

## 10. Government integrity

| | |
|---|---|
| Government source modified | **NO** |
| Government curated modified | **NO** |
| DQ / verification rerun | **NO** |
| Analyst or QA decision changed | **NO** |
| Assignment / sample / Priority Review created | **NO** |
| Government export created | **NO** (XLSX artifacts: 0) |
| Authorization marker changed | **NO** (still absent) |
| PROD data changed | **NO** |

Local Area-1 digest before `3af240c30035b17d5d669a2f8ddbd33a`, after
`3af240c30035b17d5d669a2f8ddbd33a` — **match**. Anchors unchanged: 23,566 /
23,566 / 36,916 / 138 / 23,562 / 4 / 43 / 0 / 0.

---

## 11. Cleanup — verified, not assumed

| Item | State |
|---|---|
| Temporary PITR server | deleted; one server in the resource group |
| Temporary workstation firewall rule | removed |
| Broad (0.0.0.0) database rules | **0** |
| Key Vault public network access | **Disabled** |
| My temporary Key Vault Secrets Officer | removed |
| My temporary Storage Blob Data Contributor | removed |
| DEV app identity's Blob role | **retained** — required at runtime |

The Entra administrator on the DEV database is **retained deliberately**: it is
the lawful, password-free path that made this verification possible, and
removing it would put the next audit back where Step #18A was.

---

## 12. Remaining, and honest

* **DEV image not deployed** — CI/CD could not be dispatched from this host.
  The Blob backend, the schema guard fix and the export job code are in the
  repository and tested, not yet in the running DEV container.
* **Migrations still run as `docuaction_app`** — which will now fail by design.
  The deployment must run Alembic as `docuaction_owner`.
* **84 tables owned by the runtime role** — a legacy of the above. Not
  corrected: reassigning ownership of 84 tables mid-gate risks more than it
  fixes, and the Area 1 boundary — the part that matters — is already right.
* **35 firewall rules** instead of a private endpoint.
* **Worker and Key Vault alerting** need a log query and a paging decision.
* **PROD is untouched** and every PROD item remains open.
