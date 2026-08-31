# TEFCA ARC — Controlled Excel Export: Operationalization

**Internal engineering record. Not a Government deliverable.**
Contract 7571MN26F80064 · Step #17C · 31 August 2026

Companion to `TEFCA_Controlled_Excel_Export_INTERNAL.md`, which describes the
workbook itself and is **frozen**. Nothing here changes a single cell of the
produced file. This document covers how the certified generator is run,
classified, audited and downloaded in an operational system.

---

## 1. The three problems this closes

Step #17 measured the export and left four operational findings. Three of them
are engineering problems and are closed here; the fourth is a governance
question and is recorded rather than solved.

| # | Finding | Outcome |
|---|---|---|
| 1 | full-scale generation is ~7.5 minutes, unsafe as a synchronous request | **closed** — background job |
| 2 | `_classification()` may never return GOVERNMENT | **closed** — a real defect, smallest fix |
| 3 | the Government authorization marker is absent | **not solved, and must not be** — §7 |
| 4 | the artifact response exposed a storage locator | **closed** — every download path swept |

---

## 2. Background job architecture

### What already existed

`Tefca/ppef_jobs.py` and `Tefca/ppef_scheduler.py` had solved this exact problem
for PPEF quarterly ingestion, and had solved it correctly:

* the **database** is the authoritative state store, not the scheduler;
* a **partial unique index** over active jobs makes concurrent duplicates
  impossible without a check-then-insert window;
* `SELECT ... FOR UPDATE SKIP LOCKED` makes claiming safe for more than one
  poller;
* a **heartbeat plus a reaper** turn "the worker died" from *stuck forever* into
  *FAILED, retry permitted*.

Classification: **EXISTING REUSABLE — pattern reused, table not shared.**

`tefca_ppef_ingest_jobs` is keyed on a CMS component and quarter and carries a
foreign key to a PPEF snapshot. An export has none of those. Sharing the row
space would mean columns meaning one thing for ingestion and another for
exports, which is how a shared table stops being shared and becomes ambiguous.

Also inspected and rejected: `process_jobs` + `enterprise_worker` (tenant-scoped,
`job_type` from a fixed non-TEFCA list, requires a `tenants` foreign key, and no
poller is started for it), `execution_queue` (same module, unused),
Celery/RQ/Service Bus (**not present, and not introduced**).

### What was added

`report_export_jobs`, one table, with the same discipline: state, phase,
`active_marker`, heartbeat, attempt count, error reason, and the artifact
identifiers on success. Plus a poller and a reaper following the same
`AsyncIOScheduler` shape the other three domains already use.

`ppef_jobs`'s own docstring records why FastAPI `BackgroundTasks` was abandoned:
the task vanished with a recycled worker and the job sat pending forever with no
error. That lesson is not re-learned here.

---

## 3. Lifecycle

```
  request  ──▶  QUEUED  ──▶  RUNNING  ──▶  SUCCEEDED   (names its artifact)
                              │
                              └────────▶  FAILED      (names no artifact)
```

Four states, because a fifth would have to mean something a caller could act on
differently and none does. QUEUED and RUNNING both mean *wait*; SUCCEEDED means
*download*; FAILED means *read the reason*.

Within RUNNING the job records a **phase** — `Preparing data`, `Building
workbook`, `Registering artifact`, `Ready` — each written at a real transition.
**There is no percentage anywhere**, because nothing measures one. A bar that
invents 64% is a decoration that lies.

**The order of the last two steps is the whole design.** Bytes are registered
*before* the job is marked SUCCEEDED, and the job is marked SUCCEEDED only with
the artifact's identifiers in hand. A job that said SUCCEEDED first would, for
the width of one failure, be a receipt for a file that does not exist.

---

## 4. Idempotency

Identity is a SHA-256 over everything that could change the bytes:

```
export_type | intake_id | workbook_version | engine_version | classification
```

Not the requester and not the moment: two people asking for this delivery's
workbook a minute apart want the same file, and giving them two would put two
"official" artifacts of identical content into the registry. The classification
*is* in the identity, because the same data exported under a different label is
a different artifact with different handling.

* a repeated request returns the in-flight job, and the response says
  `reused_existing_job: true`;
* a refresh or a double-click lands on the same job;
* **polling is a pure read** — a status endpoint that claimed or re-queued would
  turn a browser left open on the page into a generator of exports all
  afternoon;
* a **SUCCEEDED** job does not block a new one. Whether the *artifact* should be
  reused is a question about artifacts, and the registry already answers it:
  finalising byte-identical content returns the existing registration. Answering
  it again in the job table would be a second cache with its own opinion.

---

## 5. Concurrency

The guard is `uq_export_job_active_identity` — a **partial unique index** over
`identity` where `active_marker IS TRUE`.

```sql
CREATE UNIQUE INDEX uq_export_job_active_identity
    ON report_export_jobs (identity, active_marker)
 WHERE (active_marker IS TRUE);
```

Two callers racing produce one insert and one `IntegrityError`; the loser
re-reads the winner's row, so both get a job back and there is still only one.
`active_marker` is NULL rather than FALSE when terminal, because PostgreSQL's
partial index excludes NULL rows — any number of finished jobs may share an
identity while at most one live job may.

**Not the frontend button.** A disabled button has no bearing on a second
browser tab, a curl, or a second worker process.

---

## 6. RBAC, ownership and IDOR

| Action | Floor |
|---|---|
| produce a controlled export | `qalead` |
| read an export job | `qalead` **and** ownership |
| download the artifact | `viewer` (unchanged from Step #17) |

Adding a background endpoint must not become a way to gain export authority, so
the floor is the Step #17 floor, unchanged and asserted by test.

A job is readable by the person who requested it and by `program_manager` or
above — someone has to be able to see a failure that is not their own. Everyone
else gets **404, not 403**: "not yours" still confirms the job exists, which is
how a job id becomes an enumeration oracle. A malformed id is also a clean 404
rather than a 500.

---

## 7. Government authorization — recorded, not solved

**Engineering may determine that a dataset IS the Government delivery.
Engineering may not manufacture the authorization that makes it official.**

The delivered snapshot is the Government delivery by every visible measure — the
delivered filename, the clean Area-1 SHA-256, 23,566 records, the expected schema
fingerprint, parsed. It carries **no authorization marker**, so
`resolve_data_state` reports `MOCK_TEST / NO_AUTHORISED_GOVERNMENT_INTAKE` and
the classification is `DEVELOPMENT_TEST`.

That gap is the control working. It was not closed, worked around, inferred from
the hash, or defaulted. `tests/test_classification_matrix.py` fails if reporting
or registry code so much as references the marker key.

The governance question is recorded as item 7 of the Open Government decisions
register in the master status document. **ONC / PROGRAM GOVERNANCE DECISION
REQUIRED.**

---

## 8. Environment, identity and authorization are three things

| | Question |
|---|---|
| **Environment** | where this deployment runs — DEV or PROD |
| **Data identity** | what the dataset IS — mock, Government-delivered, nothing |
| **Authorization** | has an authority approved it for official use |

Every way of collapsing them is a different untruth, and each has its own test:

* *identity → authorization*: "the SHA matches, so this is official." The one
  this gate exists to make impossible.
* *environment → identity*: "we are in DEV, so this must be test data." A
  development deployment can hold a genuine Government delivery, and calling it
  test data licenses treating it carelessly.
* *environment → authorization*: "we are in PROD, so this is official."

The current deployment is legitimately **DEV + Government-identical data + no
authorization**, and that must not become an official Government deliverable.

---

## 9. The classification defect

**Classified as: A — ACTUAL DEFECT.** Latent, and corrected minimally.

`authoritative_source_provenance(db)` holds a database session and was calling
`_classification()`, which holds none. That helper reads `data_state_sync()`,
whose own docstring says it "never claims GOVERNMENT" — so **no report on any
deployment could ever be classified GOVERNMENT**, however properly authorised its
intake.

In this development environment the answer happened to be right, which is why it
survived: `DEVELOPMENT_TEST` is correct here, but it was correct by accident
rather than by reasoning. In a production deployment holding an authorised
delivery it would have been wrong, and wrong in the direction that strips the
handling the label exists to require.

**The fix, in full:** a new `resolve_classification(db)` asks
`resolve_data_state(db)`; `_classification()` stays exactly as it was and remains
the honest fallback for callers with no session (`Tefca/connectors.py` is one).
Both share one identity→classification mapping so they cannot answer differently.
A failure to ask falls back to the session-free answer, which can only understate.

**It does not make the current snapshot authorised.** `resolve_data_state` still
requires the marker, which is still absent.

**A second, smaller correction fell out of it.** Step #17 had given the export
route its own private resolver; that copy is gone, so a workbook and a report of
the same population cannot disagree about what the population is.

### Consequences found by regression, and kept

An **empty** database now classifies as `NO_DATASET_LOADED` rather than
`DEVELOPMENT_TEST`. That is the truthful answer and the report template already
carries a distinct banner for it — "development test data" asserts that
development evidence exists, which is the same class of untruth as claiming
Government data, pointed the other way. Six tests across `test_phase7_provenance`
moved to follow the new seam; two unit fakes had to answer a query shape they
previously did not, and one assertion became a materially stronger statement:
a fixture that looks exactly like the Government delivery classifies as
development.

---

## 10. Artifact download security

### Route inventory, in scope

| Route | Auth | Min role | IDOR | Hash verified | Locator exposed | Disposition | Type | nosniff | Cache |
|---|---|---|---|---|---|---|---|---|---|
| `GET /api/reports/{id}/html` | yes | viewer | n/a — id is the report | n/a (DB column) | no | **attachment** (was: none) | text/html | **added** | **no-store** |
| `GET /api/reports/{id}/pdf` | yes | viewer | n/a | n/a (rendered) | no | attachment | application/pdf | **added** | **no-store** |
| `GET /api/reports/{id}/csv` | yes | viewer | n/a | n/a (regenerated) | no | attachment | text/csv | **added** | **no-store** |
| `POST /api/reports/generate` (csv/html) | yes | contributor | n/a | n/a | no | **attachment** (html was: none) | per format | **added** | **no-store** |
| `GET /api/reports/artifacts/{id}` | yes | viewer | n/a | n/a — metadata | **was YES → fixed** | n/a | JSON | n/a | n/a |
| `GET /api/reports/artifacts/{id}/download` | yes | viewer | n/a | **yes, re-hashed** | no | attachment | **stored type** (was: caller's) | **added** | **no-store** |
| `POST .../onc-review-workbook?preview` | yes | qalead | n/a | n/a — not an artifact | no | attachment | xlsx | **added** | **no-store** |
| `GET /api/reports/exports/jobs/{id}` | yes | qalead + owner | **yes, 404** | n/a | no | n/a | JSON | n/a | n/a |

### Field classification

| Field | Classification |
|---|---|
| `report_id`, `artifact_version`, `job_id` | PUBLIC SAFE IDENTIFIER |
| `artifact_id`, `identity` | INTERNAL OPAQUE IDENTIFIER — safe, names nothing external |
| `rendered_sha256`, `report_data_hash`, `source_artifact_sha256` | PUBLIC SAFE — content identity, and the point of the download header |
| `storage_locator`, `storage_backend` | **SENSITIVE STORAGE LOCATOR — removed at the API boundary** |
| Azure keys, SAS URLs, connection strings | NOT EXPOSED — never reach a response |

### What changed

* **one helper builds every download response.** There were four, and they
  disagreed: the HTML route set no disposition at all, only the artifact route
  said anything about caching, and none set `nosniff`. A test parses the router
  and fails on any `Response(headers={...})` literal.
* **`Content-Disposition: attachment`** everywhere, including stored HTML.
  Rendering a stored report inline would execute its markup on this origin with
  the application's own privileges.
* **`X-Content-Type-Options: nosniff`** — without it a file whose bytes look
  like HTML can be rendered whatever the Content-Type says, which is the
  attachment problem again by another route.
* **`Cache-Control: no-store, private`** for controlled content. A copy left in
  a shared or proxy cache outlives the authorisation that produced it. This is
  **per-response and deliberately not a global policy change**; a `sensitive=False`
  parameter exists and nothing in this router uses it.
* **the served type is the STORED type.** `content_type` is a query parameter
  that selects *which* artifact to fetch; echoing it back as the response type
  would let a caller name the type their browser sees.
* **filenames are sanitised.** A report identifier reaches a header the browser
  parses; a quote ends the filename and whatever follows is read as another
  directive, and a newline ends the header entirely.
* **`storage_locator` and `storage_backend` are stripped** from every API
  response through `public_artifact()`.

### Path traversal

No caller supplies a locator — they come from the registry row. The store
refuses an escaping one regardless, and a test proves it: a control that depends
on nobody ever passing the wrong thing is not a control.

### Adjacent findings, recorded for a later security gate

Download routes exist outside this scope in `bulletin_intelligence`,
`routers/ai_analysis`, `routers/export`, `routers/invoices` and `api/export`.
They were **inspected for storage-locator exposure and none was found** — they
stream generated bytes rather than serving stored artifacts. Several set
`Content-Disposition` without `nosniff`. Not changed here: out of scope, no
proven exposure, and a security gate is the right place for a policy applied
across every module at once.

---

## 11. Failure behaviour

| Failure | Behaviour |
|---|---|
| generator raises | FAILED, controlled sentence, no artifact |
| workbook refuses itself | FAILED, the refusal's own wording — it is safe |
| artifact registration fails | FAILED, no artifact, nothing downloadable |
| worker interrupted / process dies | reaper marks FAILED after a stale heartbeat and releases the slot |
| duplicate request | the existing job, flagged `reused_existing_job` |
| invalid intake | FAILED, "No delivery …" |
| unauthorized request | 403 before a job exists |

**No traceback reaches a user.** The exception goes to the application log where
an administrator can read it; the job records `The workbook could not be produced
(MemoryError). Nothing was registered. Administrator diagnostics are in the
application log against job <id>.` A test asserts a hostile exception message
carrying a filesystem path and a password does not survive into the job record.

**A FAILED job names no artifact**, because a run that stopped part-way has
produced either nothing or something incomplete, and an incomplete workbook that
can be downloaded is worse than a failure that cannot.

---

## 12. Audit

Written through the existing `audit_logs` table — the one the Audit Trail UI
already reads, with `event_type`, `outcome`, `resource_type` and
`correlation_id` as first-class indexed columns. **No second audit framework.**

Recorded: `EXPORT_JOB_REQUESTED`, `EXPORT_JOB_REUSED`, `EXPORT_JOB_SUCCEEDED`,
`EXPORT_JOB_FAILED`, `EXPORT_JOB_REAPED` — each with `event_type=reporting`, an
outcome, the job as `resource_id` and `correlation_id`, and the actor, delivery,
intake and generator version in `details`.

It carries **no Government row values, no secrets and no exception text**. An
audit trail that copied the data it describes would be a second, uncontrolled
copy of the population in a table with different retention and different access
rules from the export itself.

An audit write can never fail the operation it describes: a system where the
safest path is the one that records least would be worse than no trail.

---

## 13. Execution model, and what it does not buy

| | |
|---|---|
| Mechanism | `AsyncIOScheduler` poller (5 s) + reaper (120 s), in the API process |
| State | PostgreSQL. The scheduler stores nothing |
| Render | worker thread via `run_in_threadpool` |
| Concurrency | **one export at a time per process**, deliberately |
| Memory | one full-scale export was measured at ~690 MB peak; two concurrently would double that for no gain |
| Restart | in-flight jobs are reaped after a stale heartbeat and may be requested again |

**Measured (§27), synthetic delivery with the render deliberately slowed by 6 s:**

| | |
|---|---|
| request acceptance | **21 ms** |
| status poll, mean of 10 | **0.1 ms** |
| worker execution | 6.6 s |
| event-loop ticks during the render | **115** (zero would mean the loop was blocked) |
| outcome | SUCCEEDED, artifact registered, 26,009 bytes |

The acceptance condition is met: **the request returns in 21 ms while the export
continues independently**, and the process stays responsive throughout.

**What is NOT claimed.** This is process-local isolation, not a distributed
worker. One gunicorn worker runs today and nothing enforces that. If the
deployment scales out, every worker starts its own scheduler and polls the same
queue — duplicate execution is prevented by the **database** rather than by the
topology, so it stays correct, but the topology must still be reviewed. No
production throughput SLA is asserted, and none was measured.

---

## 14. Deployment

**PARTIAL — and only because nothing has been deployed, not because anything is
missing.**

| Requirement | Status |
|---|---|
| new infrastructure | **none.** No queue service, no broker, no container |
| new dependency | **none.** APScheduler is already installed and already used by three schedulers |
| migration | `20260831_export_jobs` — creates one empty table; applied on DEV |
| startup | one call in `app/main.py`, beside the PPEF scheduler |
| Azure provisioning | **not required and not performed** |

Before production: confirm worker count (the scheduler assumes one; the database
is correct regardless), and review the reaper threshold against production
render times.

---

## 15. Frontend

The existing Reports → Data Review Workbook panel only. **No Step #16 redesign.**

*Produce workbook* → the job is accepted → the panel shows the phase the job
actually reports (`Preparing data`, `Building workbook`, `Registering artifact`)
under `aria-live="polite"` → *Ready for download* or *Export failed* with the
job's reason. Polling stops on a terminal state and on unmount.

**No fabricated progress.** No 37%, no 64%. The phases shown are the phases the
job records; nothing else is displayed.

A caller below `qalead` sees the panel explain that producing an export is
restricted, rather than being allowed to provoke a 403 — a 403 raises the
page-level permission boundary, which would replace a page they are entitled to
read. The check is presentation only; the server enforces the floor.

---

## 16. Tests

| Suite | Tests |
|---|---|
| `test_export_jobs.py` | 25 |
| `test_classification_matrix.py` | 15 |
| `test_download_security.py` | 18 |
| Step #17 workbook suite, still green | 38 |
| Proportional regression across reporting, provenance, artifacts, RBAC | 401 passed |

**Mutation testing: 17/17 detected**, each naming the test that must catch it.
Four were missed on the first pass and every one exposed a real gap — see the
Step #17C report §X. In each case the test was strengthened; no mutation was
weakened.

---

## 17. Government integrity

Verified before and after: all eight anchored counts match and the Area-1 digest
is unchanged. **Government export jobs created: ZERO. Government XLSX artifacts:
ZERO. Government authorization: UNCHANGED (still absent). Government data
writes: ZERO.** No Government workbook was generated.

---

## 18. Remaining issues

1. **Multi-worker topology is unreviewed.** Correct by database construction;
   unproven at scale.
2. **No retry control.** A FAILED job may be requested again, which creates a
   new job. There is no automatic retry and no backoff, deliberately — a
   seven-minute job that retries itself unattended is a way to spend an
   afternoon of CPU on a permanent failure.
3. **No job list endpoint.** A supervisor can read a job by id but cannot yet
   enumerate the queue.
4. **Adjacent download routes** in other modules set `Content-Disposition`
   without `nosniff`. No proven exposure; a security gate should apply one
   policy across all modules.
5. **The governance question** (§7) blocks any official Government export, and
   correctly so.
