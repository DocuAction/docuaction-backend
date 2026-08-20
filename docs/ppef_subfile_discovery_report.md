# PPEF Sub-File Discovery and Transport Architecture — Implementation Report

Run date: 2026-08-19/20 · Branch `feature/tefca-cms-pecos-evidence` · **DEV only**
**Not merged to main. Not deployed to production.**

---

## 1. Discovery findings

### The correction that drove everything

An earlier pass reported that CMS does not publish the four PPEF relational
sub-files. **That was wrong.** All four exist. The reasoning that produced the
error was sound but incomplete, and is recorded so it is not repeated:

* The DCAT catalogue (`data.cms.gov/data.json`, 159 datasets) carries ONE PPEF
  entry. All 159 titles were listed and read — no Reassignment, Practice
  Location, Address, Additional NPIs or Secondary Specialty dataset exists there
  under any name.
* Its five distributions are three API versions and two CSV versions of the
  **same Enrollment extract**.
* A catalogue-wide search across every distribution of all 159 datasets matched
  only the **Revalidation** products — a different dataset, and prohibited as a
  substitute.

All true. The catalogue is simply not where the sub-files live. They are
**ancillary resources of the parent dataset**, listed by an endpoint the
catalogue never mentions:

```
GET https://data.cms.gov/data-api/v1/dataset/2457ea29-fc82-48b0-86ec-3b0755de7515/resources
→ 11 resources, four of which are the sub-files
```

Approaches B (product-page HTML) and C (`?_format=json`) returned a 2,974-byte
SPA shell with zero UUIDs — the product page is client-rendered, so scraping it
yields nothing. The metastore path (`/api/1/metastore/...`) also returned the
SPA shell. `/resources` was the only mechanism that worked.

### PPEF discovery table

| Component | Resource exists | Parent UUID | Resource UUID (media id) | API works | Download | Transport | Fields discovered | ENRLMT_ID | Dataset date |
|---|---|---|---|---|---|---|---|---|---|
| **Enrollment** | ✅ | `2457ea29-fc82-48b0-86ec-3b0755de7515` | `faa3796b-01c7-46b7-b9de-fcf981d39922` | ✅ 200 | ✅ 320.5 MB | **BOTH** | NPI, MULTIPLE_NPI_FLAG, PECOS_ASCT_CNTL_ID, ENRLMT_ID, PROVIDER_TYPE_CD, PROVIDER_TYPE_DESC, STATE_CD, FIRST_NAME, MDL_NAME, LAST_NAME, ORG_NAME | ✅ | 2026.07.17 (Q3 2026) |
| **Reassignment** | ✅ | same | `1c29f9d9-0022-401f-8ac4-80142869c2a3` | ❌ 404 | ✅ 128.7 MB | **DOWNLOAD** | REASGN_BNFT_ENRLMT_ID, RCV_BNFT_ENRLMT_ID | ✅ both keys | 2026.07.17 |
| **Practice Location** | ✅ | same | `676f9bbe-072e-4194-9c9f-cd6e310210e4` | ❌ 404 | ✅ 43.2 MB | **DOWNLOAD** | ENRLMT_ID, CITY_NAME, STATE_CD, ZIP_CD | ✅ | 2026.07.17 |
| **Additional NPIs** | ✅ | same | `43e5bf24-ccce-4154-8e0a-fe0949dc19cd` | ❌ 404 | ✅ 3.6 MB | **DOWNLOAD** | ENRLMT_ID, NPI | ✅ | 2026.07.17 |
| **Secondary Specialty** | ✅ | same | `857f4823-6064-4ccc-a269-744e2170e5fb` | ❌ 404 | ✅ 27.2 MB | **DOWNLOAD** | ENRLMT_ID, PROVIDER_TYPE_CD, PROVIDER_TYPE_DESC | ✅ | 2026.07.17 |
| **CMS Revocation** (separate product) | ✅ | `a6496a7d-4e19-479a-a9ad-d4c0a49e07c3` | n/a | ✅ 200 | — | **DATA_API** | 12 fields incl. REVOCATION_RSN, REVOCATION_EFCTV_DT, REENROLLMENT_BAR_EXPRTN_DT | ✅ | quarterly |

**Statuses:** Enrollment `API_AND_DOWNLOAD_AVAILABLE`; the four sub-files
`DOWNLOAD_AVAILABLE`; Revocation `API_AVAILABLE`.

The sub-file `file_uuid`s are **media identifiers, not dataset identifiers**.
Each was individually tested against `/data-api/v1/dataset/{uuid}/data` and all
four returned **404**. Discovery through an API endpoint is not evidence of API
transport, and they are never classified `API_AVAILABLE`.

### Naming normalisation applied

CMS titles the practice-location resource **"Address Sub-File Q3 2026"** while
naming the file **`PPEF_Practice_Location_Extract_2026.07.17.csv`**. One
capability, two CMS names.

* Internal key: `PPEF_PRACTICE_LOCATION` (single capability — no separate
  "Address" capability was created).
* Component identification keys on the **file name**, which carries the CMS
  structural name; the display title varies.
* The exact CMS title is preserved in provenance on every snapshot and every
  evidence item.

### Transport decision and rationale, per component

| Component | Transport | Rationale |
|---|---|---|
| Enrollment | **BOTH** | The API supports exact `filter[NPI]` against 2,978,925 rows — the efficient path for one entity. The quarterly CSV is retained for snapshot ingestion so a determination can cite the extract it was made against. |
| Reassignment | **DOWNLOAD** | No API exists. Also the component that most needs bulk treatment: 128 MB, relational, joined on ENRLMT_ID in both directions. |
| Practice Location | **DOWNLOAD** | No API exists. One enrolment may hold many locations; reconciliation needs all of them together, not a first row. |
| Additional NPIs | **DOWNLOAD** | No API exists. Small, but must come from the SAME snapshot as the enrolment it resolves or it could contradict the record it explains. |
| Secondary Specialty | **DOWNLOAD** | No API exists. Corroborates provider type inside **D1 Identity only** — never a separate dimension, never an independent vote. |

---

## 2. Architecture implemented

```
CMS PPEF parent dataset
        │  /resources  (live discovery — no hard-coded identifiers)
        ▼
Discovery → transport classification → schema expectation
        │
        ▼  streaming download + SHA-256 over the bytes received
Schema validation (missing column ⇒ abort, never load nulls)
        │
        ▼
tefca_ppef_snapshots (pending → complete | failed)
        │
        ▼
tefca_ppef_records — enrollment_id, related_enrollment_id, npi, payload
        │
        ▼  latest COMPLETE snapshot per component, one quarter per determination
Local evidence store → PPEFRelationalConnector → six evidence dimensions
```

Resolution order per component: **data-api if CMS ever exposes one → ingested
snapshot → UNAVAILABLE with a reason.** An absent snapshot is an operational
fact, never a finding against an entity.

---

## 3. Files changed

**New (5)**

| File | Purpose |
|---|---|
| `app/Tefca/ppef_resources.py` | Live resource discovery, transport classification, expected schemas, join keys |
| `app/Tefca/ppef_ingest.py` | Streaming download, SHA-256, schema validation, row normalisation, truncation |
| `app/Tefca/ppef_store.py` | Snapshot-backed reads, provenance shaping, complete-only reads |
| `alembic/versions/20260819_ppef_snapshots.py` | Migration |
| `tests/test_ppef_ingestion.py` | 30 tests |

**Modified (5)** — additive

`app/Tefca/cms_ppef.py` (docstring correction, snapshot path, truncation guard,
capability health) · `app/Tefca/evidence_assembly.py` (partial-snapshot
semantics) · `app/Tefca/evidence_service.py` (accepts a local store) ·
`app/Tefca/models.py` (2 models) · `app/Tefca/routes.py` (3 endpoints,
background ingest)

**Frontend (1)** `src/app/tefca-arc/connectors/page.js` — per-capability
transport + snapshot detail.

**Untouched, verified:** `app/tefca_registry/ai/`, `app/bulletin_intelligence/`.

---

## 4. Database migration

`20260819_ppef_snapshots` (down-revision `20260819_dim_evidence`). Single head,
linear chain. Creates `tefca_ppef_snapshots` and `tefca_ppef_records` plus 12
indexes. **Nothing existing altered, dropped or backfilled.**

---

## 5. Endpoints

| Endpoint | Gate | Purpose |
|---|---|---|
| `GET /api/tefca/ppef/resources` | viewer | Live discovery + transport rationale |
| `GET /api/tefca/ppef/snapshots` | viewer | Ingested snapshots with full provenance |
| `POST /api/tefca/ppef/snapshots/ingest` | **admin** | Background download + ingest (202) |
| `GET /api/tefca/connectors/cms-systems` | viewer | Now snapshot-aware capability health |

---

## 6. Test changes and justification

**One existing test corrected** (approved in advance).
`test_unpublished_component_is_unavailable_not_fabricated` asserted the reason
string `not_published_via_cms_data_api`, encoding the claim that CMS does not
publish these components — disproven by live discovery. Renamed to
`test_download_only_component_without_snapshot_is_unavailable_not_fabricated`,
assertion updated to the true reason. **Every behavioural safeguard preserved**:
`success is False`, `data is None`, no fabrication, no prohibited substitution.
Four further tests were added around it.

No other existing test was modified, skipped, weakened or deleted.

**38 tests added** (30 ingestion + 8 across correctness areas), all network-free.

---

## 7. Regression

**1126 passed · 29 skipped · 0 failed · exit 0** (665.58s)

Progression: 1088 (branch baseline) → 1118 (discovery + ingestion) → **1126**
(after the truncation correctness fix).

The 29 skips are pre-existing and environmental: no local Postgres,
`BULLETIN_AUTH_ENABLED` off, one live-run test needing demo credentials.
Warnings: 480 on the new suites, all `datetime.utcnow()` deprecations matching
existing codebase convention — no new categories.

**Note on suite timing.** `tests/test_bulletin.py` and
`tests/test_bulletin_sources.py` (module untouched by this work) hang on live
feed fetches when run in isolation — they exceeded 90s each. They complete
inside the full run. The rest of the suite runs in ~72s (873 + 239 passed across
the other 57 files). Not caused by these changes; worth a network-independent
fixture in that module.

---

## 8. DEV deployment

Backend deployed 5 times — the last four because verification found real bugs
(section 9). Each deploy used `--clean true --restart true` **plus an explicit
`az webapp restart`**, since `--restart` on the deploy has been proven
insufficient on this app service.

Frontend deployed to the dev SWA; 0 prod-URL references in the artifact.

---

## 9. Bugs found by DEV verification

All three were invisible to offline tests and are now covered by tests that
would have caught them.

1. **Foreign-key ordering.** Records were inserted before the snapshot row
   existed → `ForeignKeyViolationError`, ingest 502. The snapshot row is now
   created first as `pending`; a failed load leaves a `failed` row documenting
   the attempt and its partial rows are deleted.
2. **60-second gateway timeout.** A real ingest is minutes of work; the request
   died at 60.4s with **no response at all** while the work continued
   server-side — the caller could not distinguish a timeout from a crash.
   Ingestion moved to a background task returning `202`.
3. **`TypeError: 'AsyncSession' object is not callable`.**
   `async_session_maker()` returns a *session*, not a factory. This killed every
   background ingest **silently**: the endpoint returned 202 and snapshots sat
   at `pending` forever. Corrected to match existing usage at `routes.py:713`.

### And one correctness gap in the evidence layer

A **truncated** snapshot returned no rows, and the address dimension reported
`NO_PRACTICE_LOCATION` — a claim about CMS data, when the truth was only that
our snapshot is partial. That is a manufactured clean absence, precisely what
this design exists to prevent.

Fixed: a truncated snapshot with no rows now yields `inconclusive` with reason
`snapshot_truncated_no_rows`; D4 reports `UNAVAILABLE` and D6 reports
`INSUFFICIENT_EVIDENCE` rather than a negative finding. Verified live on dev
after redeploy: the false claim is **absent**, the honest reason is **present**.

---

## 10. DEV verification evidence

| # | Check | Result |
|---|---|---|
| 1 | Parent dataset discovery | ✅ `2457ea29-fc82-48b0-86ec-3b0755de7515` |
| 2 | `/resources` discovery | ✅ 5 components, 11 resources |
| 3 | Enrollment | ✅ `API_AND_DOWNLOAD_AVAILABLE` / `BOTH` |
| 4 | Reassignment | ✅ `DOWNLOAD_AVAILABLE` |
| 5 | Practice Location | ✅ `DOWNLOAD_AVAILABLE` |
| 6 | Additional NPIs | ✅ `DOWNLOAD_AVAILABLE` |
| 7 | Secondary Specialty | ✅ `DOWNLOAD_AVAILABLE` |
| 8 | CMS Revoked | ✅ `AVAILABLE`, separate system |
| 9 | Snapshot ingest | ✅ 4/4 complete |
| 10 | SHA-256 | ✅ `0ae087f442c9e55b…` reproduced identically across two independent runs |
| 11 | Schema validation | ✅ live headers matched expectations |
| 12 | Schema-drift abort | ⚠️ **test-verified only** — cannot make CMS publish a broken file |
| 13 | Truncation detection | ✅ 3 truncated flagged; Additional NPIs complete at 128,435 rows, `truncated=False` |
| 14 | Snapshot-backed reads | ✅ components resolve with no API |
| 15 | Quarter consistency | ✅ all four on `v2026.07.17` |
| 16 | One-to-many | ✅ live CMS (`I20031103000001` → 2 receiving entities) + unit tests; ⚠️ not end-to-end via dev API — no endpoint exposes raw snapshot rows, and one was not added merely to satisfy a check |
| 17 | Connector Hub | ✅ one source, five capabilities, transport + snapshot + `TRUNCATED` shown |
| 18 | Admin authorization | ✅ no token 401 · viewer 403 · reviewer 403 · admin 202 · unknown component 400 |

Ingested on dev: Additional NPIs **128,435 rows (complete)**; Practice Location
60,000, Reassignment 60,000, Secondary Specialty 40,000 (all truncated, flagged).

---

## 11. Security / authorization verification

* Ingestion is **admin-only**; viewer and reviewer both receive 403, anonymous 401.
* Discovery and snapshot listing are viewer-gated and expose no credentials.
* No secret, token or credential is logged, stored or returned by any new endpoint.
* Only public-domain CMS data is fetched; no API key, no BAA, no PHI transmitted.
* Unknown component names are rejected 400 — including
  `REVALIDATION_REASSIGNMENT`, so the prohibited substitution cannot be requested.

---

## 12. Known limitations

1. **Dev snapshots are partial** for three components (60k/60k/40k rows). With
   the truncation guard, they correctly refuse to support negative findings —
   so Practice Location and Reassignment read `UNAVAILABLE` /
   `INSUFFICIENT_EVIDENCE` on dev rather than producing positive evidence.
2. **Full ingestion needs a batch path.** ~5.5M rows across the four sub-files
   via ORM inserts is too slow for an HTTP-triggered task. A Postgres `COPY`
   loader run on a schedule is the right mechanism.
3. **Orphan `pending` snapshots.** A background task killed mid-flight (app
   recycle) leaves a `pending` row. Harmless — only `complete` rows are read —
   but they should be reaped.
4. **Schema-drift abort is test-verified only** (see #12 above).
5. **Enrollment is still API-first per entity.** Snapshot-based enrolment
   evidence is possible but not yet wired.
6. **`allowed_modules` remains unenforced** (separate defect, unchanged here).

---

## 13. DEV `allowed_modules` audit (read-only, nothing changed)

52 users. **Only 5 hold the full 15-module set.**

| State | Count | Notable |
|---|---|---|
| FULL (15) | 5 | admin@, meerab.rahil@, manager@, pm@, senioranalyst@ |
| PARTIAL | 7 | `testadmin@` — an **admin with 1 module**; reviewer@, qalead@, viewer@, analyst@, muskan.zehra@ hold only `['tefca_review']` |
| EMPTY | 40 | 33 DAST test accounts, plus bilal.naveed@, ghania.ashraf@, izaan@, **imran@agtbi.com (viewer, EMPTY)** |
| NULL | 0 | — |

Modules observed: action_center, analytics, audit_logs, bulletin_intelligence,
case_management, compliance, decision_bank, healthcare_claims, meetings,
opportunities, risk_detection, security, tefca_review, trust_center,
validation_queue.

**Enabling `allowed_modules` enforcement today would lock out ~47 of 52 dev
users, including the account owner.** Any enforcement requires a data backfill
first. PROD to be checked separately by the operator.

---

## 14. Remaining PROD actions

1. Apply migrations `20260819_dim_evidence` and `20260819_ppef_snapshots`.
2. Provide a batch ingestion path before relying on sub-file evidence.
3. Decide the legacy `pecos` connector key (still queries **NPPES** under that name).
4. Decide `allowed_modules` enforcement after a prod data check.
5. Merge the feature branch (not done — prohibited this run).

---

## 15. Rollback considerations

* **Backend:** redeploy the previous artifact; the two new tables are additive
  and unread by prior code, so a code-only rollback is safe and needs no
  database change.
* **Migrations:** both have working `downgrade()`, but dropping
  `tefca_ppef_snapshots` / `tefca_ppef_records` destroys the snapshots
  determinations cite. Prefer leaving the tables in place.
* **Frontend:** redeploy the previous SWA build; the Connector Hub degrades to
  the prior display if `/connectors/cms-systems` is absent.
* **Data:** snapshots are append-only; rollback loses no prior evidence.
* **No prod change has been made**, so no prod rollback is pending.

---

**STOPPED. Not merged. Not deployed to production. Awaiting approval.**
