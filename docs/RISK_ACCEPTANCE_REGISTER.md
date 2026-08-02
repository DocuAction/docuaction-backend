# Risk Acceptance Register

**Contract:** 7571MN26F80064 · **Date opened:** 2026-08-02 · **Review cadence:** 90 days

Findings that will not be remediated in the current sprint, each with the reason
and any compensating control. An entry here is a **decision**, not a backlog
item — the review date is when the decision is revisited, not when the work is
scheduled.

**Accepted by:** ______________________ (Imran Siddiqui)  **Date:** ____________

*Signature block intentionally unsigned — risk acceptance is a human decision
and must not be recorded as taken until it has been.*

**Next review date: 2026-10-31**

---

## RA-001 — Next.js advisory (`next`)

| Field | Value |
|-------|-------|
| Severity | High |
| Component | `next` 16.2.12 (frontend) |
| Decision | ~~Accept~~ → **CLOSED — REMEDIATED 2026-08-02** |
| Closed | 2026-08-02 |

**Closure note.** This acceptance rested on the premise that no fix existed
short of a breaking downgrade. That premise was wrong, and the error was in
where the fix was sought. The advisory was reported against `next` only because
`next` depends on a vulnerable `sharp`; the actual vulnerable component is
`sharp <0.35.0` (libvips CVEs). Pinning `sharp` forward to 0.35.3 through an
`overrides` entry cleared the advisory without touching the framework.
`npm audit` now reports **0 vulnerabilities**. See
`frontend/docs/ADR-001_NextJS_Version_Selection.md`.

The original rationale is retained below for the record.

**Rationale.** There is no fixed stable release. The advisory range covers
through `16.3.0-preview.7`, and the latest stable release is `16.2.12` — the
version in use. `npm audit fix --force` resolves the advisory by **downgrading
Next 16.2.12 → 14.2.35**, a major *downgrade* that breaks the App Router build
entirely. Trading a working application for a clean audit line is not a security
improvement.

**Compensating controls.** Static analysis on every push (CodeQL); the frontend
is a static export with no server-side rendering, which excludes the SSR request
paths most Next advisories concern.

**Exit condition.** Upgrade when 16.3.0 ships stable. Re-check at review.

---

## RA-002 — `sharp` advisory (transitive)

| Field | Value |
|-------|-------|
| Severity | High |
| Component | `sharp` (transitive via `next`) |
| Decision | ~~Accept~~ → **CLOSED — REMEDIATED 2026-08-02** |
| Closed | 2026-08-02 |

**Closure note.** Remediated at the correct layer: `sharp` pinned to 0.35.3 via
an `overrides` entry, rather than through the `next` dependency tree. Confirmed
low risk before applying — the build is a static export with
`images: { unoptimized: true }` and no `next/image` imports, so the image
optimisation server that `sharp` serves is never run. Build re-verified after
the change (77 static routes). See
`frontend/docs/ADR-001_NextJS_Version_Selection.md`.

The original rationale is retained below for the record.

**Rationale.** Same root cause and same fix path as RA-001 — it resolves only
through the Next.js dependency tree, and the only available "fix" is the same
breaking downgrade. Tracked with RA-001 rather than separately, because they
cannot be resolved independently.

**Compensating controls.** The static export performs no runtime image
processing, so the vulnerable code path is not reachable in the deployed
artifact.

---

## RA-003 — `plans.py:33` `GET /info` flagged as unauthenticated

| Field | Value |
|-------|-------|
| Severity | High (scanner-assigned) |
| Component | `app/api/plans.py:33` |
| Decision | **Accept** |
| Review date | 2026-10-31 |

**Rationale.** The endpoint returns public pricing data and is *intended* to be
unauthenticated. It is deliberately **not** added to the scanner's exact-public
allowlist: the rule matches on the decorator path only, so an entry for `/info`
would simultaneously whitelist `/api/v1/case-management/info`, which **is**
protected and is an explicit DAST target. Silencing a genuine finding to clear a
cosmetic one is the wrong trade.

**Compensating controls.** No sensitive data is returned; the protected
`/case-management/info` endpoint remains guarded and is covered by the RBAC
suite.

**Exit condition.** Resolvable by making the scanner rule match the full route
path rather than the decorator fragment. Tracked as a scanner improvement, not
an application change.

---

## RA-004 — `ecdsa` PYSEC-2026-1325

| Field | Value |
|-------|-------|
| Severity | Medium |
| Component | `ecdsa` 0.19.2 (transitive) |
| Decision | **Accept** |
| Review date | 2026-10-31 |

**Rationale.** The only finding reported by `pip-audit -r requirements.txt`. The
package is a transitive dependency and the vulnerable code path is not reachable
from this application — no ECDSA signing or verification is performed. No fixed
version is available.

**Note on scanning method.** Auditing the local virtualenv reports ~40 CVEs;
auditing `requirements.txt` reports 1. The virtualenv accumulates packages that
were never shipped. **Always audit `-r requirements.txt`** — the venv number is
not a measure of deployed risk.

---

## RA-005 — Geo-redundant backup disabled on the primary production database

| Field | Value |
|-------|-------|
| Severity | Medium |
| Component | `docuaction-db` (rg-docuaction-prod) |
| Decision | **Accept, time-limited** |
| Review date | At Railway→Azure cutover |

**Rationale.** Geo-redundancy on Azure PostgreSQL Flexible Server is a
**create-time-only** setting and cannot be enabled on an existing server.
`docuaction-db-geo` exists with it enabled and is the intended destination at
cutover. Backups are currently regionally redundant with 14-day retention.

**Compensating controls.** 14-day point-in-time restore; `docuaction-db-geo`
provisioned and ready.

**Exit condition.** Closes at cutover. See `docs/BACKUP_RESTORE_PROCEDURE.md`.

---

## RA-006 — Database restore never rehearsed

| Field | Value |
|-------|-------|
| Severity | Medium |
| Component | Disaster recovery |
| Decision | **Mitigate — not accepted** |
| Review date | Before ATO |

**Rationale.** Listed here for visibility rather than acceptance. The restore
procedure is documented but has never been executed, so the recovery time
objective is **unmeasured**. A procedure that has not been rehearsed is a
hypothesis.

**Action required.** Perform a test restore into a scratch server and record
actual RTO before ATO.

---

## RA-007 — Azure PostgreSQL public network access (prod)

| Field | Value |
|-------|-------|
| Severity | High |
| Component | `docuaction-db` (rg-docuaction-prod) |
| Scanner rule | `AZ-DB-006-docuaction-db` |
| Status | **Acknowledged — requires Azure infrastructure change** |
| Owner | Imran (Azure portal) |
| Review date | 2026-10-31 |

**Action.** Enable a private endpoint, cut the application over to it, and only
then disable public access — **in that order**. See *Remediation sequence* below.

> **Do not disable public network access as a standalone change.** The
> application currently connects over the public endpoint. Disabling it before
> the cutover is complete causes an immediate production outage.

**Notes.** Application code is not affected. Database access is restricted by
firewall rules and credentials. Full remediation requires Azure Private Link
configuration.

---

## RA-008 — Azure PostgreSQL public network access (geo)

| Field | Value |
|-------|-------|
| Severity | High |
| Component | `docuaction-db-geo` (rg-docuaction-prod) |
| Scanner rule | `AZ-DB-006-docuaction-db-geo` |
| Status | **Acknowledged — requires Azure infrastructure change** |
| Owner | Imran (Azure portal) |
| Review date | 2026-10-31 |

**Action.** Enable a private endpoint, cut the application over to it, and only
then disable public access — **in that order**. See *Remediation sequence* below.

> **Do not disable public network access as a standalone change.** The
> application currently connects over the public endpoint. Disabling it before
> the cutover is complete causes an immediate production outage.

**Notes.** Application code is not affected. Database access is restricted by
firewall rules and credentials. Full remediation requires Azure Private Link
configuration.

---

## RA-009 — Azure PostgreSQL public network access (dev)

| Field | Value |
|-------|-------|
| Severity | High |
| Component | `docuaction-db-dev` (rg-docuaction-dev) |
| Scanner rule | `AZ-DB-006-docuaction-db-dev` |
| Status | **Acknowledged — requires Azure infrastructure change** |
| Owner | Imran (Azure portal) |
| Review date | 2026-10-31 |

**Action.** Enable a private endpoint, cut the application over to it, and only
then disable public access — **in that order**. See *Remediation sequence* below.

> **Do not disable public network access as a standalone change.** The
> application currently connects over the public endpoint. Disabling it before
> the cutover is complete causes an immediate production outage.

**Notes.** Application code is not affected. Database access is restricted by
firewall rules and credentials. Full remediation requires Azure Private Link
configuration.

---

## Remediation sequence for RA-007 / RA-008 / RA-009

Applies to all three servers. **The order is the control** — steps 1–3 are
non-disruptive and reversible; step 4 is the only one that can take production
down, and it is safe only once step 3 has actually passed.

1. Create a Private Endpoint for the PostgreSQL Flexible Server.
2. Update the application connection string to the `.privatelink.` hostname.
3. **Verify the application works over the private endpoint.** Do not proceed on
   assumption — this is the gate for step 4.
4. Only then, disable public network access.

**Current posture is deliberate, not an oversight.** The application connects
over the public endpoint today. Public access stays enabled until the cutover is
verified; there is no partial state that improves on this.

**Estimated cost.** ~$7–10/month per private endpoint. Deferred pending budget,
which is why these are acknowledged with an owner rather than scheduled.

**Standing instruction.** No automated tooling or agent is to make Azure
networking changes for these findings. Remediation is a deliberate, manually
sequenced operation performed by the owner.

---

## Register summary

| ID | Severity | Decision | Closes when |
|----|----------|----------|-------------|
| RA-001 | High | **CLOSED — remediated 2026-08-02** | Closed |
| RA-002 | High | **CLOSED — remediated 2026-08-02** | Closed |
| RA-003 | High | Accept | Scanner rule matches full path |
| RA-004 | Medium | Accept | Upstream fix |
| RA-005 | Medium | Accept (time-limited) | Azure DB cutover |
| RA-006 | Medium | **Mitigate** | Restore rehearsed, RTO measured |
| RA-007 | High | Acknowledged — infra change | Private endpoint on `docuaction-db` |
| RA-008 | High | Acknowledged — infra change | Private endpoint on `docuaction-db-geo` |
| RA-009 | High | Acknowledged — infra change | Private endpoint on `docuaction-db-dev` |

**0 Critical. 0 unaccepted High findings from static analysis** (Bandit: 0 High).

**Update 2026-08-02.** RA-001 and RA-002 are closed by remediation, not by expiry: `npm audit` on the frontend now reports 0 vulnerabilities. Three new High findings surfaced from Azure database configuration (public network access on `docuaction-db`, `docuaction-db-geo` and `docuaction-db-dev`); they are now recorded as **RA-007, RA-008 and RA-009**.

These three are **acknowledged, not accepted** — the distinction matters. They
remain open High findings with a named owner and a defined remediation, and they
are not resolvable in code: each requires an Azure Private Link configuration
change made through the portal. They were assigned RA-007 onward rather than
RA-003 onward because those identifiers were already in use and are cited by the
enterprise documentation generators; reusing them would have silently altered
published Volumes II and III.
