# Delivery Workflow — End-to-End User Guide (Delivery Traceability, 2026-09-17)

**Audience.** Data Operations (registers deliveries), Analysts (work
exceptions), QA Leads (approve determinations), Program Managers (release
reports), and anyone asked "what happened to this delivery?"

**What changed.** Every delivery now leaves durable evidence: one row per stage
attempt, one append-only disposition per received record, one persisted
reconciliation snapshot per run, one event per identifier conflict or
decision, and one link per generated report. The screens, the API and the
Delivery Processing Report read that evidence; nothing is recomputed on the
page. Where evidence does not exist the system says so — it never fills a gap.

Canonical detail page: `/tefca-arc/deliveries/detail/?job={job_id}`.
Canonical detail API: `GET /api/tefca/rce/delivery-jobs/{id}/detail`.

---

## The 20 steps

### 1. Register the delivery
*Data Operations, `program_manager` or above.* Registered Deliveries → **Register**.
Attach the ONC/RCE file, enter the received date, the Government reference
(transmittal number or email subject) and any note. You get a **receipt**: a job
id, the state `QUEUED`, and the page to watch. The `REGISTERED` stage event is
written immediately.

### 2. Confirm receipt and SHA-256
On the receipt, compare the **SHA-256** and **file size** with the values the
sender gave you. `RECEIPT_PRESERVED` and `SHA256` are instant stage events; the
original bytes are preserved before the job exists. If the hash does not match
the transmittal, stop here and raise it with the sender — do not re-register.

### 3. Open the delivery from Registered Deliveries
Registered Deliveries lists every job with two status columns: **Processing
outcome** (what the machine did) and **Review state** (where the humans are).
Click the row or **View details** to open the canonical detail page. The list
keeps your sort, page and filters in the URL.

### 4. Read the processing outcome
Overview tab. One of `Queued`, `Processing`, `Completed — Clean`, `Completed —
With Exceptions`, `Partially Processed`, `Failed`. `Completed — Clean` is a
claim with a proof obligation: expand **Basis** to see each criterion
(reconciliation passed, no unresolved findings, no held/rejected/missing-key
records, no failed required verification, no invalid identifier promoted, no
unresolved conflicts, no unexplained records) and whether it held.

### 5. Read the review state
Same tab. `Not Ready` until processing completed **and** reconciliation
passed; then `Ready for Analyst Review` → `Under Review` → `Ready for QA` → `QA
Review` → `QA Approved` → `Closed`. The review state is never a statement about
data quality.

### 6. Check reconciliation
Processing Timeline / Overview → **Reconciliation**. The persisted snapshot shows
`Received = Created + Updated + Matched/Unchanged + Held + Rejected + Missing Key
+ Excluded`, whether it passed, its sequence, hash, build SHA and migration
revision. A snapshot that says PASSED cannot carry counts that do not sum (a
database CHECK enforces it).

### 7. Read the dispositions
Records tab. Every received line has exactly one **current** disposition
(highest sequence). Filter by disposition; totals match the snapshot unless an
analyst has appended a decision since, in which case the page says the current
table differs from the snapshot.

### 8. Open the exceptions
Exceptions tab (or Delivery Exceptions → pick the delivery). One row per
finding: line, entity name, submitted and existing values, rule (e.g.
`NPI-003`), issue type, severity, stage, status, assignee, decision history.
Filter by rule code, severity, stage, status, assignee, disposition, date.

### 9. Open a record
Click a line number anywhere. The record page shows the delivered line (Area 1),
the curated projection (Area 2), the matched or created registry entity, and
the disposition history for that line — sequence by sequence, actor by actor.

### 10. Compare source / curated / existing / verified
On the record page each field is shown in up to four columns: **Source**
(delivered), **Curated** (normalised, with any correction and its authority),
**Existing** (the registry value before this delivery), **Verified** (the
authoritative source's value when a verification ran). Differences are marked.

### 11. NPI: what the rules did
`NPI-001` presence (INFORMATIONAL unless the profile requires it), `NPI-002`
length, `NPI-004` format, `NPI-003` Luhn check digit — one primary finding per
value, in that order. `NPI-005` not found in NPPES, `NPI-006` deactivated
(`inactive_pending_review`), `NPI-009` verification unavailable. A value that
failed length is not evaluated for format or checksum.

### 12. PECOS and other coverage
Verification tab. Per source (NPPES, PECOS, LEIE, SAM): `Complete`, `Partial`,
`Not Run`, `Unavailable`, `Not Configured`, `In Progress`. Coverage counts
evidence rows for **this delivery's entities**; connector health is shown
separately and is not coverage.

### 13. Assign held records
Exceptions tab → select → **Assign**, or Validation Queue → work queue
(`queue_source=RCE_DQ_HUMAN_REQUIRED&intake_id=…`). A held record stays HELD
until a human decides; nothing releases it automatically.

### 14. Record analyst dispositions
On a finding: **Decide** → `{decision, reason, corrected_value?}`
(`POST /api/tefca/rce/issues/{issue_id}/dispositions`). On an identifier
conflict: `CONFIRM_EXISTING`, `CONFIRM_SUBMITTED`, `CORRECTED`,
`REQUEST_EVIDENCE`, `DEFERRED`, `ESCALATED`, `REJECTED` with a reason
(`POST /api/tefca/rce/identifier-decisions`). Only `CONFIRM_SUBMITTED` and
`CORRECTED` change the registry — only in answer to a raised conflict, only
with a value that passes the identifier validator and is not another
entity's active identifier — and each writes an entity version and an audit
row in the same transaction. Every decision appends; nothing is edited.

### 15. Send to QA
When every open item on the delivery has a determination the review state
becomes `Ready for QA`. The QA Lead takes the case from the QA queue; approvals
set `reportable_at` and move the state to `QA Approved`.

### 16. Generate the report
Reports tab → **Generate Delivery Processing Report**
(`POST /api/reports/generate` with `report_type: "delivery_processing"` and
`parameters.job_id`). The report is built from the persisted evidence and
**pinned to the latest reconciliation snapshot**; the response states
`snapshot_id`. To regenerate from an earlier snapshot pass
`parameters.snapshot_id`; the report prints which snapshot it used and never
silently adopts newer data. A report without a job or intake id is refused
(HTTP 422, `DELIVERY_IDENTIFIER_REQUIRED`).

### 17. Verify the totals
Page one of the report carries the identity block (job, intake, file, SHA-256,
size, registrant, timestamps, build SHA, migration revision, snapshot id +
hash, template version, generated at). Section 3 is the snapshot's equation;
section 4 is the current disposition table with its own totals; the CSV is the
record-level table, every row. All three come from one dataset and agree.

### 17a. Where the report lives (durability)
Generating a delivery report writes **two** records:

* `review_reports` — the dataset and HTML, in the application database
  (backed up with it, but a mutable row with no content address).
* the **artifact registry** (`report_artifacts`) plus the configured
  **artifact store** — one write-once, content-addressed copy of **every
  rendering**: the HTML, the CSV and, when the PDF engine's native libraries
  are present on the host, the PDF. Each copy is registered with its own
  `rendered_sha256`, `size_bytes`, `content_type`, `template_version`,
  `report_data_hash` (the SHA-256 of the dataset it was rendered from),
  `source_artifact_sha256` (the SHA-256 of the **delivered file** this report
  describes), `data_classification` and the retention record (period
  *pending programme guidance*, WORM lock off). When no PDF engine is present
  the response says so in `pdf_unavailable_reason` and `/pdf` renders on demand
  from the stored HTML.

Which store is decided by `REPORT_ARTIFACT_BACKEND`:

| Backend | Setting | Durable? | Use |
|---|---|---|---|
| Azure Blob | `azure` with `REPORT_ARTIFACT_AZURE_ACCOUNT` + `REPORT_ARTIFACT_AZURE_CONTAINER` (managed identity; no keys) | **Yes** — write-once blobs, overwrite refused by the service | DEV / PROD |
| Local filesystem | `local` (default), root `REPORT_ARTIFACT_ROOT` | **No** on App Service — the container's ephemeral layer | tests and single-host development only |

Every response that names an artifact states `storage_backend` and
`durable`. If a DEV or PROD response says `"storage_backend": "local",
"durable": false`, the App Service is not configured for durable storage and
the report's only lasting copy is the database row — raise it; do not treat the
artifact as retained.

One `rce_delivery_report_links` row is written **per artifact** (job, intake,
snapshot, report, artifact, template version, generation audit id, actor,
build SHA, correlation id). The link's API form adds `file_sha256`,
`content_type`, `size_bytes`, `storage_backend`, `durable` and `download_url`
from the registry join; the link table itself is unchanged.

### 18. Read the audit history
Audit History tab (`GET /api/tefca/rce/deliveries/{intake_id}/audit`) and the
platform Audit Trail. `report_generated` (naming every artifact and the
backend), every `report_downloaded` and every `report_download_failed` carry
the request id, the job, the intake, the snapshot and the build SHA.
`GET /api/reports/by-delivery/{job_id}` (reviewer and above) lists every
report linked to the job with its artifacts — id, `content_type`,
`rendered_sha256`, `size_bytes`, `storage_backend`, `download_url` — and a
`storage` block for the job.

### 18a. Reports role floor (raised 2026-09-16, pre-merge review Decision 1)

Generating a report and every way of downloading one (`/api/reports/generate`,
`/{id}/html`, `/pdf`, `/docx`, `/csv`, `/package`, `/artifacts/{id}`,
`/artifacts/{id}/download`, `/sow/{deliverable}`, and their deprecated
`/api/tefca/reports/*` aliases) require **reviewer**. A viewer reaches the
report listing, one report's metadata (`GET /api/reports/{id}`, with `dataset`,
`delivery_links` and `artifacts` null and an `availability` reason), release
status, the SOW family list, and engine health — never a report's actual
content. A denial below reviewer writes an audit row
(`event_type=security`, `outcome=blocked`, `resource_type=report`). The
Contract Reports page and the delivery detail Reports tab both hide
generation and download controls below reviewer and state the reason; the
server enforces the floor regardless of what the browser shows.

### 19. Download the evidence
Reports tab → HTML / PDF / CSV / package. Downloads are served from the
**stored** document, byte for byte; each download writes an audit row. The
package ZIP carries a manifest with the SHA-256 of every member.

The registered copies are fetched through
`GET /api/reports/artifacts/{report_id}/download?content_type=…&version=…`
(or `/api/reports/artifacts/{artifact id}/download` using the id a link
carries). The route **re-hashes the stored bytes before serving** and returns
the hash in `X-Artifact-SHA256` with `X-Artifact-Verified: true`. Outcomes:

| Situation | Answer | Audit row |
|---|---|---|
| Served | 200, attachment, `no-store` | `report_downloaded` |
| Registered but the blob/file is gone | **410** `code: ARTIFACT_MISSING` (never a 500); the registry row remains as the record of issue | `report_download_failed` |
| Never registered | 404 `ARTIFACT_NOT_REGISTERED` | `report_download_failed` |
| Bytes present but do not hash to the registered SHA-256 | 500, **refused** — nothing is served | `report_download_failed` (`ARTIFACT_INTEGRITY_FAILURE`) |

**Authorisation.** `reviewer` is the floor for the artifact download and for
`by-delivery`. Roles on this platform are **global**: no RCE or report table
carries a delivery-ownership or tenant column, so a reviewer who may read one
delivery's artifacts may read every delivery's by id. That is the documented
control — role floor plus an audit row on every download — not per-delivery
scoping, and the listing says so (`scope.per_delivery_scoping: false`).

**Restart.** Reports, listings and downloads are read from the registry and
the store, not from process memory: after an App Service restart everything
above still resolves, provided the store is the durable backend.

### 20. Final disposition
Program Manager → Reports → **Release** (`PM_REVIEWED` → `READY_FOR_DELIVERY`).
Release decisions are appended to the report and to the audit trail. The
delivery's review state becomes `Closed` when the QA Lead closes the case.

---

## Where to go for…

| Situation | Go to | What you will see |
|---|---|---|
| **Failed delivery** | Detail → Processing Timeline | the `FAILED` stage attempt with failure class and reason; remediation guidance on the Overview; register a **new** delivery after the cause is fixed (never edit the failed one) |
| **Clean delivery** | Detail → Overview → Basis | every clean criterion held; Records tab shows only CREATED / UPDATED / MATCHED_UNCHANGED |
| **Delivery with exceptions** | Detail → Exceptions | the ledger, filterable; assign and decide from there |
| **Invalid NPI** | Exceptions → rule `NPI-002/003/004` | the record is HELD (`HELD_QUALITY_ISSUE`); the submitted value is preserved and never promoted; decide or request evidence |
| **Existing-entity match with a different identifier** | Exceptions → rule `NPI-008` / `IDENTIFIER_EXISTING_VALUE_CONFLICT`; Detail → Records → the line | the record is HELD (`HELD_IDENTIFIER_CONFLICT`); existing and submitted values side by side; decide `CONFIRM_EXISTING` / `CONFIRM_SUBMITTED` / … |
| **Verification unavailable** | Verification tab | source state `Unavailable` or `Not Run`; `NPI-009` informational findings; this never counts against an entity |
| **PECOS partial** | Verification tab → PECOS | `Partial` with attempted/eligible counts and the coverage percentage; the report's section 7 states the same |
| **Analyst review** | Validation Queue → work queue for the delivery; Detail → Exceptions | open and claimed items; decisions append to the record's history |
| **QA review** | QA queue; Detail → Overview → Review state | `Ready for QA` / `QA Review` / `QA Approved` with counts |
| **Report** | Detail → Reports; `GET /api/reports/by-delivery/{job_id}` | every report linked to the job with its snapshot id; generate, regenerate from a named snapshot, download |
| **Audit** | Detail → Audit History; Audit Trail filtered by `reporting` / correlation id | who did what, when, under which request id and build |
| **A delivery processed before 2026-09-17** | Detail → Overview | `availability.records = never_ran`; the report lists the missing evidence under *Evidence limitations*; see `docs/rce/HISTORICAL_RECONSTRUCTION_POLICY.md` before proposing any reconstruction |

## Rules that never bend

1. A report describes **one** named delivery. There is no "newest delivery"
   default anywhere.
2. Evidence is appended, never edited: dispositions, decisions, snapshots and
   report links have no UPDATE or DELETE path from the application.
3. A number on a page comes from a persisted row. If the row does not exist the
   page says *not recorded*, *never ran* or *unavailable*.
4. Downloads serve the stored document. Regeneration produces a new report id
   with its own snapshot pin and its own link.
5. `Completed — Clean` is shown only when every criterion in the basis holds.
6. A finding discovered AFTER a record is promoted never rewrites the
   promotion. A confirmed NPI deactivation, an invalid active identifier, or a
   material identifier conflict found by a later verification pass sets the
   entity's verification status to "in review," opens exactly one analyst
   work item, blocks a final QA classification of that entity until resolved,
   and produces a NEW reconciliation snapshot — the earlier snapshot is
   untouched. While such a finding is unresolved the entity is also left out
   of the frame of any NEW sample draw (reported as an unresolved unit with
   a reason, never hidden); earlier draws are never redrawn. See
   `docs/rce/PREMERGE_REVIEW_2026-09-16.md` sections 9 and 10.
