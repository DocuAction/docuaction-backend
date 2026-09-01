# TEFCA ARC — Security and Azure Readiness

**Internal engineering record. Not a Government deliverable. Contains no secrets,
no credentials, no Azure keys and no Government values.**
Contract 7571MN26F80064 · Step #18 · 31 August 2026

Companion to `TEFCA_Production_Readiness_Matrix_INTERNAL.md`, which carries the
per-control table. This document records what was *observed*, what was *changed*,
and what remains — with the reasoning.

---

## 1. What this gate did and did not do

Assessed the infrastructure under an application that Steps #1–#17C had already
certified. **No Azure resource was created, modified or deleted. No production
change of any kind. No Government data was read for its content, written, or
exported.**

Three narrow code changes were made and are described below. Everything else is
evidence.

---

## 2. The most important finding

**The Key Vault work is complete and entirely unused.**

Both vaults exist with public network access **disabled**, RBAC authorization,
soft delete and purge protection. Both App Services have a system-assigned
managed identity. Both identities already hold **Key Vault Secrets User** on
their vault. Private endpoints and private DNS zones are in place.

And **zero application settings reference the vault** — the ARM
`config/configreferences/appsettings` endpoint returns empty for both apps. Seven
DEV and eight PROD secrets sit in App Service settings as literal values.

The infrastructure was built, proven, and then never wired up. That is a
different problem from "no Key Vault", and a much smaller one — the remaining
work is configuration, not architecture.

### Why the migration was not completed here

§8 permitted a DEV migration if it could be done with existing access and
without weakening anything. It cannot be, and the reason is the control working
correctly:

* **The DEV vault refused on RBAC.** The signed-in human holds Owner at the
  management plane and *no data-plane role* — `Assignment: (not found)` for
  `secrets/readMetadata`. Least privilege, exactly as intended.
* **The PROD vault refused on the network, before RBAC was even consulted:**
  *"Public network access is disabled and request is not from a trusted service
  nor via an approved private link."*

Completing the migration from a developer workstation would require either
granting a human Secrets Officer or opening the vault to the public internet.
The gate forbids the second outright and the first is the elevation Step #14
deliberately removed. **Classified PARTIAL. The controls are not weakened to
make a task convenient.**

The migration belongs in a change executed from inside the VNet, or from CI with
a workload identity — which is a Class B change and needs authorization.

---

## 3. What was changed

### 3.1 Startup schema mutation now fails closed on a deployed host (backend)

`schema_guard._is_production()` asked only "does `ENVIRONMENT` say production?".
An **unset** variable therefore meant not-production, which meant startup schema
mutation was **allowed**. Unset is precisely the state a restored configuration,
a new deployment slot, or a mis-copied setting begins in — so the most likely way
to lose the variable was also the way to grant the capability it guards.

The consequence is the one this module was written to prevent: on production, a
container restart could run `create_all()` and create the Area 1 tables. In
PostgreSQL the creating role owns the table, and an owner can always UPDATE and
DELETE its own rows regardless of grants — making immutability inert on the
tables that hold Government data.

**The fix reads silence in the light of where the process is running.** App
Service always sets `WEBSITE_SITE_NAME` and `WEBSITE_INSTANCE_ID`; a laptop has
neither. An explicit value is believed in both directions; an unset or
unrecognised value on a *deployed* host is treated as production. On a developer
machine nothing changes, which preserves the convenience the guard deliberately
kept — a control that is routinely switched off stops being a control.

Both flags are correctly set in both environments today, so this was latent, not
live. Seven new tests; four mutations, all detected.

### 3.2 The shared shell reflows at 320px (frontend)

Carried since Step #16. The sidebar was 192px and **in flow**, leaving 128px of
content at a 320px viewport. Below the `sm` breakpoint the same `<aside>` is now
taken out of flow as an overlay drawer, with a header menu button, a scrim,
Escape-to-close, focus into the drawer on open and back to the button on close.
At `sm` and above nothing changes.

Measured, rendered, in a same-origin iframe of exact CSS size:

| Viewport | Sidebar | Content width | Menu button | Page h-scroll | Overflowing elements |
|---|---|--:|---|---|--:|
| 1920 / 1440 / 1280 / 768 / 640 | static rail, 192px | 1713 / 1233 / 1073 / 561 / 433 | hidden | none | 0 |
| 639 / 375 / **320** | fixed, off-canvas | 624 / 360 / **305** | visible | none | 0 |

Drawer behaviour at 320: opens, all 28 nav links reachable, scrim present,
Escape closes, **focus returns to the menu button**.

**Two things found only by rendering it**, which is why rendered inspection is
not optional:

1. **A real defect.** The first focus-return implementation only restored focus
   when it had fallen to `<body>`. After Escape the focus was still on the
   drawer, so the condition never matched and the user was left focused on a
   panel that had slid away. Fixed and re-measured.
2. **A false alarm.** The drawer's computed transform appeared stuck at −224px
   even when open. It was not: the automated context never advanced the CSS
   transition. With `transition: none` the transform is `matrix(1,0,0,1,0,0)` and
   the drawer is open. During that investigation the transform was moved from a
   Tailwind utility toggle to an inline style — a defensive simplification, **not
   a defect fix**, and it should not be reported as one.

### 3.3 Dependency patches (frontend)

`npm audit fix` — lockfile only, `package.json` unchanged. 2 vulnerabilities → 0.
Build and all 43 UI guardrails re-verified.

---

## 4. Azure inventory

Subscription `AGT-DocuAction`, tenant `067477bc…`. Two resource groups.

| Capability | DEV | PROD |
|---|---|---|
| App Service | `docuaction-dev` (centralus) | `Docuaction` (eastus2) |
| Static Web App | `docuaction-frontend-dev` | `docuaction-frontend` |
| PostgreSQL Flexible | `docuaction-db-dev` v16, B1ms, 7-day backup | `docuaction-db` v16, B1ms, 14-day; plus `docuaction-db-geo` (geo-redundant) |
| Key Vault | `docuaction-kv-dev` | `docuaction-kv-prod` |
| Container registry | `acrdocuactiondev` | `acrdocuactionprod` |
| VNet + private endpoint + private DNS | yes (Key Vault) | yes (Key Vault) |
| App Insights / Log Analytics | **none** | `docuaction-appinsights`, `docuaction-logs` |
| Alerts | none | 4 metric alerts, 1 action group, 1 email receiver |
| **Storage Account** | **none** | **none** |

**There is no Storage Account anywhere in the subscription.** Report artifacts
are therefore on the App Service filesystem.

> **Corrected in Step #18A.** This section originally said the store "already has
> an Azure Blob backend implemented". It does not — `AzureBlobArtifactStore` is a
> declared seam whose every method raises. See the Step #18A section below.

---

## 5. Network

| Control | State |
|---|---|
| App Service HTTPS-only | Yes, both |
| Minimum TLS | 1.2, both |
| FTPS | Disabled, both |
| VNet integration | Yes, both (`app-integration` subnet) |
| Key Vault public access | **Disabled**, both — verified by being refused |
| PostgreSQL public access | **Enabled**, both. No private endpoint, no delegated subnet |
| PostgreSQL firewall | PROD 32 single-IP rules, no wildcard. DEV 17 rules **including `AllowAllAzureServices` (0.0.0.0)** |

**The DEV `AllowAllAzureServices` rule is the one clear-cut security finding.**
It permits any resource in any Azure tenant to reach the DEV database server,
subject to credentials. PROD does not have it. Removing it is a Class A change on
DEV, but it needs the DEV app's outbound IPs added first or the DEV app loses its
database — so it is proposed, not executed.

Reaching the database by public IP allow-list is brittle rather than unsafe:
App Service outbound IPs change when the plan changes, and 32 hand-maintained
rules is a fragile way to express "the application". A private endpoint or
delegated subnet is the durable answer.

---

## 6. Worker topology — the Step #17C carry-forward, answered

Step #17C recorded "multi-worker topology unreviewed". It can now be answered
with evidence rather than assumption:

* `numberOfWorkers: 1` on both App Services — one instance;
* the Dockerfile `CMD` runs `gunicorn … -k uvicorn.workers.UvicornWorker` with
  **no `--workers` flag**, so gunicorn defaults to **one** worker process;
* `appCommandLine` is **empty** on both apps, so the Dockerfile `CMD` is what
  actually runs — the override that once disagreed with it is gone.

**Exactly one process runs the schedulers today.** The PPEF and export
schedulers are therefore singletons in fact, not merely by intention.

If that ever changes, correctness does not depend on it: `claim_next_queued` uses
`FOR UPDATE SKIP LOCKED` and both job tables carry a partial unique index over
active jobs, so duplicate execution is prevented by the **database**. APScheduler
coordinates nothing of its own, so the topology must still be reviewed before
scaling out — but the failure mode would be wasted work, not corruption.

---

## 7. Export worker capacity

Step #17C measured a full-population export at roughly 7.5 minutes and **~690 MB
peak heap**. Both App Service plans are **B1ms** — a burstable tier with 2 GB of
memory shared with the web process and everything else in it.

**A Government-scale export on the current plan is not safe.** One export would
consume roughly a third of the instance's memory while serving requests. That is
not a code problem; the code already runs one export at a time and off the event
loop. It is a sizing decision, and it is a Class B change.

**Retry remains deliberately undefined.** Step #17C left it so, and the failure
classes seen since do not argue for changing that: a refused workbook and a
schema mismatch are permanent, and a seven-minute job that retries itself
unattended spends an afternoon of CPU on a permanent failure. **Recommendation:
MANUAL RETRY** — a person requests it again, which the existing design already
supports because a terminal job releases its identity.

---

## 8. Security headers — a correction to Step #17C

Step #17C recorded that adjacent non-TEFCA download routes "lack `nosniff`".
**That was wrong.** A global middleware sets `Strict-Transport-Security`,
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, CSP `default-src
'self'`, `Referrer-Policy` and `Permissions-Policy` on **every** response,
including 404s and error paths — verified by live probe, not by reading.

The per-response `nosniff` added in Step #17C is harmless duplication, not a fix
for a real gap. The genuine gap is **`Cache-Control`**, which the middleware does
not set; only the controlled-export routes declare `no-store, private`.

A static inventory found **28 file-serving routes across 10 modules. Every one is
authenticated**, via one of four mechanisms (`require_role` in the handler,
`require_role` at the router, `guard()` at the route, or `get_current_user`). An
earlier pass of the same scan appeared to show unauthenticated routes; it was
only looking inside handler bodies. One module (`routers/quotes.py`) is not
mounted at all.

---

## 9. Dependencies

| Ecosystem | Finding | Assessment |
|---|---|---|
| npm | `nanoid` HIGH, `dompurify` MODERATE | **Fixed** — patch-level, lockfile only. `nanoid` was reached through `postcss`, a build-time dependency; `dompurify` through `jspdf` and unused in application code. Both were low real risk; both are now gone |
| Python | `ecdsa` 0.19.2, PYSEC-2026-1325 (Minerva timing attack, P-256 signing) | **LOW — not exploitable here, and no fix exists.** The application signs JWTs with **HS256**; it performs no ECDSA signing. `ecdsa` arrives transitively through `python-jose`. Upstream has no fixed version. Verification is unaffected per the advisory |
| Container | `python:3.12-slim`, `USER appuser` (uid 10001), no embedded secrets | Non-root confirmed in the Dockerfile |

29 of 69 Python requirements are `==` pinned. PROD deploys by **image digest**;
DEV by tag.

---

## 10. What must change before a controlled rehearsal

| # | Item | Class | Why it blocks |
|---|---|---|---|
| 1 | Key Vault references for PROD secrets | B → C | Plaintext production secrets means production security is not ready |
| 2 | Durable artifact storage | B → C | A registered artifact whose bytes vanish on instance replacement is not an artifact |
| 3 | Restore rehearsal | B | Backups that have never been restored are a configuration, not a capability |
| 4 | PROD database role verification | A (read) → C | If the runtime connects as an administrator, Area 1 immutability is inert |
| 5 | DEV `AllowAllAzureServices` removal | A | The one finding fixable without new authorization |

## 11. Not done, and why

* **No Azure change of any kind.** Every remediation above is Class B or C.
* **No restore rehearsal.** It requires creating a server — cost, and a Class B
  authorization this gate does not have. **Restore remains OPERATIONALLY
  UNPROVEN**, and backup is not called PASS because Azure says backups exist.
* **No PROD database inspection.** Reading PROD app settings was refused by this
  session's own tooling; value-free surfaces were used instead. The PROD database
  runtime role was not observed and is honestly **UNVERIFIED** rather than
  assumed.
* **No MFA.** Adding an identity capability is not a readiness gate's work.
* **No Government authorization.** Marker absent, authority undefined, and
  engineering may not choose one.

---

# Step #18A — closure attempt, 31 August 2026

Step #18A set out to close the five blockers. **Four could not be closed within
this prompt's own authority, and the fifth was proven unsafe to close.** The
evidence is below; the gate outcome follows from it.

## 1. Key Vault — re-proven, and a drift finding

Re-verified without reading a value: **zero Key Vault references on either app**
(ARM `configreferences/appsettings` empty for both). Both vaults still have
public access disabled, RBAC and purge protection; both managed identities still
hold Key Vault Secrets User.

**New finding — the live configuration has drifted from the IaC.**
`infra/modules/appService.bicep` already declares
`keyVaultReferenceIdentity: 'SystemAssigned'` and four Key Vault references:

    SECRET_KEY             -> SECRET-KEY
    ANTHROPIC_API_KEY      -> ANTHROPIC-API-KEY
    AZURE_AD_CLIENT_SECRET -> AZURE-AD-CLIENT-SECRET
    SENDGRID_API_KEY       -> SENDGRID-API-KEY

The deployed App Services carry literal values instead. The intended
architecture was designed, committed, and never became the live state — the apps
were configured out of band.

Two consequences worth separating. On those four, the remaining work is
*configuration drift*, which is small. But **the IaC does not vault
`DATABASE_URL`, `SAM_GOV_API_KEY`, `USPS_CLIENT_SECRET` or `PERIGON_API_KEY`** —
four more secrets, including the most sensitive one. Those need a design change,
not merely a redeploy.

**Not closed.** DEV migration is blocked exactly as in Step #18: the human holds
no data-plane role (RBAC refusal) and the vault refuses the public network. PROD
migration is out of scope by instruction. Neither control was weakened to get
around this.

## 2. Artifact storage — a correction, and worse than recorded

Step #18 said "the artifact store already has an Azure Blob backend implemented;
there is nothing for it to point at." **That was wrong.**

`AzureBlobArtifactStore` is a *declared seam*, not an implementation. Its own
docstring says "NOT EXERCISED", `azure-storage-blob` and `azure-identity` are
deliberately absent from `requirements.txt`, and `put`, `get`, `head` and
`versions` **all raise** `ArtifactStoreUnconfigured`. Only `__init__` and
`_client` contain real code.

The local backend is also less durable than assumed:

    DEFAULT_LOCAL_ROOT = os.path.join("uploads", "report_artifacts")   # RELATIVE

`REPORT_ARTIFACT_BACKEND` and `REPORT_ARTIFACT_ROOT` are unset on both apps, so
the store resolves that relative path against the container's working directory
— the container's own writable layer. `WEBSITES_ENABLE_APP_SERVICE_STORAGE` is
`false` on DEV and unset on PROD, so `/home` (the persistent Azure Files share)
is not mounted, and would not be the root even if it were.

Artifacts therefore survive **none** of: process restart, App Service restart,
deployment, instance replacement, scale-out.

**Not closed.** Closing it needs a Storage Account (Class B), two packages, a
real implementation of four methods, an RBAC assignment, and tests — a scoped
piece of engineering, not a configuration change.

### Provisioning specification, for when it is authorized

| Item | Value |
|---|---|
| Resource | Storage Account, StorageV2, Standard_LRS (GRS for PROD) |
| Container | private, no anonymous access, **no SAS in the architecture** |
| Auth | the App Service system-assigned identity, `DefaultAzureCredential` |
| Role | **Storage Blob Data Contributor**, scoped to the container |
| Network | private endpoint into the existing VNet; public access disabled |
| Settings | `REPORT_ARTIFACT_BACKEND=azure`, `REPORT_ARTIFACT_AZURE_ACCOUNT`, `REPORT_ARTIFACT_AZURE_CONTAINER` |
| Packages | `azure-storage-blob`, `azure-identity` |
| Code | implement `put`/`get`/`head`/`versions`; preserve immutability by refusing to overwrite an existing version blob |
| Tests | write, read, download, hash, version, restart, missing object, unauthorized, path manipulation, concurrency |

## 3. Restore / PITR — not attempted

Azure PITR creates a **new Flexible Server** — a new paid resource, which SS3
does not treat as "clearly required, low-risk, DEV-only". **Not executed, and
not faked.** Restore remains **OPERATIONALLY UNPROVEN**.

The rehearsal plan is unchanged and ready to run the moment a server may be
created: restore DEV to an isolated server, validate version, schema, Alembic
revision, representative counts and the Area-1 digest read-only, record
start/finish/duration/restore point, then delete the server.

## 4. Azure database roles — blocked by a control, not by effort

Both PostgreSQL servers report **`activeDirectoryAuth: Disabled`,
`passwordAuth: Enabled`**. There is therefore no way to connect as a person; the
only credential is the stored password, and extracting it is secret disclosure.

Enabling Entra authentication on the DEV server would create a lawful path, but
that is an authentication-configuration change to a database server and needs
its own authorization.

**Azure DEV and PROD database roles remain UNVERIFIED.** Step #18's caution
stands: what was proven earlier was the *local developer* database
(`current_user = docuaction`), which says nothing about Azure.

## 5. DEV firewall — proven UNSAFE to remove

The plan was to drop `AllowAllAzureServices (0.0.0.0)` now that 17 individual
`devapp-out-*` rules exist. Measured before touching anything:

| | |
|---|--:|
| DEV app current outbound IPs | 17 |
| DEV app **possible** outbound IPs | 34 |
| Single-IP firewall rules | 17 |
| Current outbound IPs covered by rules | **No — `13.89.172.22` is missing** |
| Possible outbound IPs not covered | **18** |

**The DEV application is currently reaching its database through the broad
rule.** Removing it would break DEV immediately for at least one live address,
and unpredictably thereafter as App Service rotates within its possible set.

Hand-maintaining 34 addresses is not a fix — it is the same fragility with more
rows. The durable answer is a private endpoint or a delegated subnet, which is a
Class B change.

**Not closed, not attempted.** "Do not break DEV merely to make a matrix green"
is exactly what the measurement prevented.

## 6. Export capacity — determined

Measured independently of Step #17, on 317,394 cells and scaled to the delivered
population's 1,962,377:

| | |
|---|--:|
| Render peak, python heap, measured | 107 MB |
| Scale factor to the Government population | 6.2x |
| **Projected render peak** | **659 MB** |
| Projected workbook size | 6.6 MB |
| B1ms plan memory | 2,048 MB |
| Render peak as a share of the plan | **32%** |

659 MB corroborates Step #17's ~690 MB by a different method. The application's
own resident baseline could not be measured from this host, so a *total* figure
is deliberately not asserted.

**Determination: SAFE WITH CONCURRENCY LIMIT — and the limit already exists.**
The poller claims exactly one job per tick and runs it inline, and the scheduler
registers `max_instances=1`, so overlapping ticks are refused. Nothing needed
implementing; what was missing was a test asserting it, because this is now a
memory ceiling rather than a design preference. Added.

**No plan resize is recommended on this evidence, and none was made.**

## 7. Monitoring

DEV still has no telemetry resource. Creating App Insights or a Log Analytics
workspace is new provisioning (Class B). **Not created.** The proposed alert
baseline from Step #18 stands unchanged.

---

## #18A outcome

| Blocker | Closed? | Why |
|---|---|---|
| 1 Key Vault / plaintext secrets | **No** | DEV blocked by RBAC + network; PROD forbidden by instruction |
| 2 Durable artifact storage | **No** | The Azure backend is an unimplemented seam; needs a Storage Account |
| 3 Restore / PITR proof | **No** | Requires creating a paid server |
| 4 Azure DB role verification | **No** | Entra auth disabled; the only path is secret extraction |
| 5 DEV firewall | **No** | Measured: removal would break DEV connectivity |
| 5b Export capacity | **Closed** | 659 MB / 32%, one-export-at-a-time proven and tested |
| 5c Monitoring | **No** | Requires new provisioning |

**A — technically ready for a controlled production rehearsal: NO.**

Not because anything is broken, and not for want of trying: four blockers need an
authorization this prompt withholds, and the fifth was proven unsafe by
measurement. One item closed, with a test.

**Step #19 is therefore NOT authorized by the gate and was not executed.**
