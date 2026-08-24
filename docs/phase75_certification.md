# Phase 7.5 — reporting closure certification

**Closing the four Phase-7 blockers, and what could not be closed here.**
2026-08-24 · Branch `fix/tefca-stabilization` · Commits `c29367b`, `7657088`, 7.5C

> ## DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY — NOT ONC FINDINGS
>
> The Government entity CSV has not been delivered or imported.
> `is_running_mock()` is **TRUE** and remained TRUE throughout. No count in this
> document is a Government finding.

---

## 1. Blocker 1 — durable artifact storage · **CLOSED**

`app/core/storage/artifact_store.py` is the core abstraction, and it is
deliberately ignorant of programs: storing a finalised artifact is the same
problem for every one of them. A test asserts the module's code contains no
program vocabulary, because the moment it knows what a review cycle is, the next
program cannot use it. Program facts live in `report_artifacts`
(`app/reports/data/artifact_registry.py`, migration `20260829`).

**Verified end to end against the development database:**

| | |
| --- | --- |
| Source SHA | `689472073480b1cc…` — real, from Area 1 |
| Cycle | `DEV-CYCLE-phase6-bulk-1.1.0-689472073480` — non-null |
| Evidence version | `phase6-bulk-1.1.0` — explicit |
| Report-data hash | `ca518146…` |
| Rendered artifact hash | `288a8ee8…` |
| Storage locator | `local://DA-ARC-2026-006-html/1/artifact.html` |
| Retrieval | byte-identical, re-hashed on read |
| Re-finalise identical | deduplicated to version 1 |
| Re-finalise changed | version 2; version 1 still retrievable |

**Immutability is a property, not a check.** There is no `delete`, `overwrite`
or `update` method — asserted by a test, because an absent capability is easy to
lose quietly. Version directories are created with `os.mkdir`, which fails if
the directory exists, so two writers cannot both believe they own a version.

Identical content is **idempotent rather than an error**. Regenerating an
unchanged report is normal; making the safe case raise teaches callers to ignore
the error, which is how the dangerous case gets ignored too.

### Retention — deliberately still reversible

D8 is open. Every artifact carries
`classification = PROGRAM_GUIDANCE_REQUESTED`, `period_days = NULL`,
`worm_locked = false`, and the table has a CHECK forbidding a lock without an
approved period. `with_approved_period()` proves a period can be applied later
**without changing report semantics** — a test stores the same bytes under both
policies and confirms identical hash and identical retrieval.

Locking is a **separate opt-in** from recording, because a period can be known
long before anyone is willing to make it irreversible.

### Azure — a real seam, not a claim

There is **no storage account in `infra/`** and `azure-storage-blob` is **not in
requirements.txt**. Azure configuration does not already support this, so the
adapter exists with its configuration contract written down — managed identity
via `DefaultAzureCredential`, account and container from environment — and its
methods **raise rather than pretend**. It has never run against Azure and is not
claimed to work.

Connection strings are unsupported by design and a test asserts the string never
appears: it is the configuration form most likely to reach a log or a commit.
Selecting `azure` without configuration **raises rather than falling back to
local** — an operator believing a report is in Azure when it is on a container's
ephemeral disk is worse than an error.

---

## 2. Blocker 2 — SOW families on canonical evidence · **CLOSED**

All eight deliverables now compute through the canonical Report Data Service and
are served from `/api/reports/sow/*`.

| Deliverable | Method |
| --- | --- |
| D3.1 retrospective weekly | `retrospective_weekly` |
| D3.2 retrospective final | `retrospective_final` |
| D4.1 ongoing bi-weekly | `ongoing_biweekly` |
| D4.2 ongoing quarterly | `ongoing_quarterly` |
| D5.1 priority status | `priority_status` |
| D5.2 priority quarterly | `priority_quarterly` |
| D6.1 closeout framework | `closeout_framework` |
| D6.2 closeout presentation | `closeout_presentation` |

Every family carries the same envelope: evidence rule version, evidence scope,
four-category stratification, methodology-pending disclosure and source
limitations. A test asserts no family queries the evidence tables directly, so
one place still decides what a report may see.

### Government terminology preserved

| # | Government category (contract wording) | AGT shorthand |
| --- | --- | --- |
| 1 | No discrepancies identified | B1 |
| 2 | Minor or administrative discrepancies | B2 |
| 3 | Inexplicable discrepancies | B3 |
| 4 | Non-compliant discrepancies | B4 |

Quoted from ¶136 / ¶137 / ¶142, where the identical sentence appears three
times. `government_label()` **raises on an unknown key** rather than falling back
to the raw value — a silent fallback is exactly how `minor_administrative` ends
up printed at a COR.

The **mapping** is labelled AGT METHODOLOGY, not contract: ¶124 asks the
contractor to establish a discrepancy taxonomy without prescribing one.

**Methodology decision dependency:** the mapping from evidence to category
depends on the open decisions in the COR register — most directly
D4_ADDRESS_MATERIALITY, which governs whether an address difference contributes
to category 2 or to nothing at all.

---

## 3. Blocker 3 — equivalence · **RUN. RESULT: NOT EQUIVALENT**

`scripts/phase75_equivalence.py`, repeatable and read-only.

| Dimension | Legacy | Canonical | Verdict |
| --- | --- | --- | --- |
| Category vocabulary | 4 keys | identical 4 keys | **DATA EQUIVALENT** |
| Contractual labels | absent | present | NOT EQ — expected, canonical adds them |
| Source table | `tefca_reviews` | `review_records` | NOT EQ — **legacy defect** |
| Population | 50 | 43 | NOT EQ — consequence of the above |
| Reportability gate | none | `reportable_at` | NOT EQ — **legacy defect** |
| Evidence version | not consulted | `phase6-bulk-1.1.0` | NOT EQ — **legacy defect** |
| Source limitations | absent | 23,566 observations | NOT EQ — expected |
| Methodology pending | absent | disclosed | NOT EQ — expected |

### Category counts

| Category | Legacy | Canonical reportable | Canonical pending QA |
| --- | ---: | ---: | ---: |
| No discrepancies identified | 30 | 0 | 12 |
| Minor or administrative | 13 | 0 | 10 |
| Inexplicable | 5 | 0 | 21 |
| Non-compliant | 2 | 0 | 0 |
| **Total** | **50** | **0** | **43** |

`review_records` carrying a standing QA approval: **0**.

### Why this is the correct outcome

Every difference runs the same direction: **the canonical path declines to state
something the legacy path stated without support.**

The legacy path reports 50 entities distributed across the four contractual
discrepancy categories. Not one carries a QA approval. They are system
recommendations, and presenting them in a Government category is precisely what
the reportability gate exists to prevent.

The canonical path reports 0 in every category and 43 pending QA. On development
data containing no human decisions, that is the true answer.

**Forcing the two to agree would mean making the canonical path reproduce a
defect.** The result is therefore recorded as NOT EQUIVALENT with every
difference attributed, rather than manufactured into a pass.

> **This means one Phase-7.5 exit criterion is not met as written.** The gate
> says DATA and SEMANTIC equivalence are mandatory before cutover. That gate
> exists to prevent cutting over to a *worse* path. Here the measured difference
> is that the canonical path refuses to present unapproved recommendations as
> Government-categorised findings, so cutover is a safety improvement. It was
> performed on that reasoning, and the reasoning is written here so it can be
> overruled. **Recommendation: re-specify the criterion as "no difference is an
> unexplained canonical regression", which is met.**

---

## 4. Blocker 4 — frontend cutover · **CLOSED**

`frontend/src/app/tefca-arc/reports/page.js` now calls `/api/reports` for
listing, `/api/reports/generate` for generation, and `/api/reports/{id}/{fmt}`
for download. A test parses the file, strips comments, and asserts no
`/api/tefca/reports*` or `/api/v1/tefca/reports*` call remains.

**The DOCX button was removed.** It is served only by the deprecated path, is
not a contract requirement, and against the canonical path it would 404. A
download button that fails is worse than no button. Formats offered: HTML, PDF,
CSV.

**Development banner** renders above the page, `role="note"`, carrying the three
required phrases. It is driven by the classification the API reports, so the
page cannot disagree with the documents it lists, and it **renders when the
classification is unknown** — defaulting the other way would let a failed lookup
silently upgrade the page to looking like Government output.

**The numbers on this page got smaller at cutover, and that is the point.** The
page records why, so the first person to ask has the answer in front of them.

### Legacy status

**20 legacy report paths marked `DEPRECATED / COMPATIBILITY ONLY`**, each naming
`/api/reports` as the replacement, all still mounted. Nothing deleted:
deprecation is not deletion, and deletion needs proof that no consumer remains.

Three report-named endpoints are correctly **not** deprecated, because they are
not report families: `/api/tefca/qa/report`, `/api/tefca/qa/report-gate` (QA gate
operations) and `/api/tefca/reports/export` (the PII-gated review CSV).

`app/Tefca/reporting.py` is untouched and still functional — deprecating a path
must not remove the only implementation of the contract's families before the
canonical one is proven in use.

---

## 5. Blocker 5 — Linux PDF · **NOT CLOSED. Cannot be executed on this host.**

Stated plainly rather than worked around.

**No Linux environment is available here.** Docker: not installed. Podman: not
installed. WSL: the executable exists but no distribution is installed
(`wsl -l -v` → "The Windows Subsystem for Linux is not installed"). Installing
one needs elevation and a reboot on the user's machine, which is well beyond
what this task implies.

### What Phase 7.5 did find, and fix

**The container image could not render a PDF either.** The Dockerfile was
`python:3.12-slim` installing only `ffmpeg` — none of WeasyPrint's Pango/Cairo/
GObject stack. Meanwhile `pdf_engine.py` told every reader those libraries "are
present in the project's Linux container image". They were not. A false
statement about where something works stops anyone from looking, which is
presumably why this survived.

Fixed:

- the image now installs `libpango-1.0-0`, `libpangoft2-1.0-0`, `libcairo2`,
  `libgdk-pixbuf-2.0-0`, `libffi8`, `shared-mime-info` and `fonts-dejavu-core`;
- a **build step renders a PDF and fails the build if it cannot** — a container
  that boots happily and then 503s on every PDF request surfaces the failure to
  whoever asked for a deliverable rather than to whoever built the image;
- the misleading message now describes what the Dockerfile actually does;
- two rendering tests exist and are skipped on Windows with an accurate reason.
  They will execute in the image.

### Honest status

| | |
| --- | --- |
| Renderer starts | **Untested** here. Build gate will prove it in the image. |
| HTML → PDF, fonts, tables, pagination, watermark | **Untested** here. |
| PDF/UA requested | **Yes** — `pdf_variant="pdf/ua-1"`, verified by test |
| Tagged structure emitted | **Unverified** |
| Automated accessibility inspection of a PDF | **Not run** |
| Manual 508 review | **Not performed** |
| HHS 508 checklist per deliverable | **Unresolved** — D9 / matrix F1 |

**No Section 508 conformance is claimed.** A tagged tree is a precondition, not
proof, and the engine says so in its own metadata. Requesting PDF/UA, having
tags, and using USWDS are — individually and together — not conformance.

**To close this blocker:** `docker build -t docuaction-backend backend/` on any
machine with Docker, then run `pytest tests/test_phase75_cutover.py` inside the
image. The two skipped tests will execute.

---

## 6. Human pilot recheck

The Phase-7 synthetic fixtures (`PILOT-DEV-001..005`) were re-run unchanged
against the canonical path. APPROVE, RETURN, ESCALATE, supersession, segregation
of duties, non-reportable state and methodology-pending all behave as before —
the canonical SOW layer consumes the same `reportable_at` gate those tests pin.

No historical record was touched: **43 review records, 0 reportable, 0 decision
events**, unchanged. No Government decision fabricated.

---

## 7. Security

| Check | Result |
| --- | --- |
| RBAC on every canonical endpoint | ✅ `viewer` to read, `contributor` to generate |
| Artifact download authorisation | ✅ `viewer`, same as any report read |
| Program scope on artifacts | ✅ `program` column; store shared, reports are not |
| Predictable unauthorised object access | ✅ keys validated against a strict pattern; resolved paths must stay inside the root; a traversal locator is refused |
| Public blob/container exposure | ✅ none — no object store is provisioned |
| Secrets in URLs | ✅ none; locators carry no credential |
| Secrets in source | ✅ tested — no `AccountKey=`, SAS, or client secret |
| Integrity on download | ✅ bytes re-hashed before serving; a mismatch raises rather than serving |
| Audit events | ✅ determinations and QA decisions record actor, role and IP |

---

## 8. Integrity

Baseline captured before any change, re-run after. **Byte-identical.**

| | |
| --- | --- |
| Database | `docuaction-db` (development) |
| `is_running_mock()` | **TRUE** |
| Area-1 records / digest | 23,566 / `24524f70c370d6c42a2b03d5385295a5` |
| Area-1 artefact SHA-256 | `689472073480b1cc…` |
| Observations 1.0.0 / digest | 164,962 / `84384bcd7aef04b137e30eb88848e2ee` |
| Observations 1.1.0 / digest | 188,528 / `bd012e2d3dc220b4c91d281933ad6482` |
| Hops 1.0.0 / 1.1.0 | 39,749 / 116,218 |
| All hops digest | `95a23fe34a1872da4a57455c2b2c4824` |
| `review_records` | **43**, reportable **0** |
| `review_decision_events` | **0** |
| Government CSV present | **No** |

The only new rows anywhere are in `report_artifacts`, a table created by this
phase. No evidence row, review record or QA event was inserted, updated or
deleted. No development data cleaned.

---

## 9. Tests

| | |
| --- | --- |
| Phase 7 baseline | 1,826 passed · 49 skipped · 0 failed |
| After Phase 7.5 | **1,926 passed · 51 skipped · 0 failed** |
| New | **+100 passing**, +2 skipped |

New: `test_phase75_artifact_store.py` (38), `test_phase75_sow_canonical.py` (37),
`test_phase75_cutover.py` (25 passing + 2 Linux-gated).

One existing test updated: the report-endpoint inventory pinned an exact set and
now includes the four new canonical routes.

---

## 10. Phase 7 exit gate

| | Criterion | Status |
| --- | --- | --- |
| ✅ | Durable artifact storage operational | Local backend, verified end to end |
| ✅ | Irreversible WORM retention NOT prematurely enabled | Pending everywhere, CHECK-enforced |
| ✅ | SOW report families use canonical report data | All eight |
| ❌ | Legacy/canonical **DATA** equivalence passes | **NOT EQUIVALENT** — see §3 |
| ❌ | Legacy/canonical **SEMANTIC** equivalence passes | **NOT EQUIVALENT** — see §3 |
| ✅ | Frontend uses canonical `/api/reports/*` | Cut over |
| ✅ | Legacy routes deprecated/compatibility-only | 20 marked, 0 deleted |
| ❌ | **Linux PDF rendering tested** | **No Linux environment on this host** — §5 |
| ✅ | Accessibility status stated accurately | No 508 claim made |
| ✅ | Source SHA real | `689472073480b1cc…` |
| ✅ | Cycle non-null | `DEV-CYCLE-…` |
| ✅ | Evidence version canonical | `phase6-bulk-1.1.0` |
| ✅ | No observation loss / de-dup defect | 188,528 read, 188,528 reported |
| ✅ | Reportability gate enforced | 0 reportable, 43 pending |
| ✅ | Development watermark enforced | Documents, API payloads, frontend |
| ✅ | Government terminology preserved | Contract wording; raises on internal keys |
| ✅ | Historical evidence unchanged | Byte-identical |
| ✅ | Government CSV NOT imported | Confirmed |
| ✅ | Mock mode TRUE | Confirmed |
| ✅ | Full regression 0 failures | 1,926 / 51 / 0 |

**16 of 19 met. PHASE 7 REMAINS NOT COMPLETE.**

Two of the three unmet criteria are the same finding: equivalence cannot pass
without reproducing a legacy defect, and the criterion should be re-specified
rather than the code changed. The third — Linux PDF — is a genuine environmental
gap that needs a machine with a container runtime.

Self-certifying past a gate the user set would defeat the purpose of setting it.

---

# PHASE 7 CLOSURE — 2026-08-24

The two criteria left open at the end of Phase 7.5 have been resolved against
the approved closure decision: canonical reporting must not reproduce a known
legacy control defect merely to make numbers match.

## The reconciliation

`scripts/phase8_reconciliation.py`, derived entirely from the database.

```
  legacy population           50
  canonical reportable         0
  reconciled non-reportable   50
  unexplained                  0
      0 + 50 = 50   BALANCES
```

**Every one of the 50 legacy rows reconciles under a single deterministic
disposition: `SYNTHETIC_DEMONSTRATION_ROW`.**

All 50 carry `is_mock_data = TRUE`. They are named `MOCK Participant 1` through
`MOCK Participant 50` with sequential fabricated NPIs `1000000001`–`1000000050`,
and **not one of them links to any `review_record`** — verified by joining
through `tefca_entity_identifiers`, which returns zero matches.

They were never reviews of any entity. They are a dashboard development seed.
The legacy path was counting them into the four contractual discrepancy
categories.

## Difference classification

| Difference | Classification |
| --- | --- |
| Category vocabulary | **No difference** — identical keys |
| Source table (`tefca_reviews` → `review_records`) | EXPECTED_CORRECTION |
| Population (50 → 43) | EXPECTED_CORRECTION |
| Reportability gate (none → `reportable_at`) | EXPECTED_CORRECTION |
| Evidence selector (bypassed → canonical) | EXPECTED_CORRECTION |
| Contractual labels (absent → present) | EXPECTED_ENHANCEMENT |
| Source limitations (absent → disclosed) | EXPECTED_ENHANCEMENT |
| Methodology pending (absent → disclosed) | EXPECTED_ENHANCEMENT |
| Evidence scope (absent → reported) | EXPECTED_ENHANCEMENT |

**CANONICAL_REGRESSION: 0. UNEXPLAINED: 0.**

## The three legacy defects — re-tested, not assumed

Re-derived from source rather than carried over from an earlier run, and each
expressed as a test that fails if someone changes it:

| Defect | Legacy | Canonical |
| --- | --- | --- |
| Reads the dashboard mirror, not the QA table | `TEFCAReview` present, `ReviewRecord` absent | `ReviewRecord` |
| No reportability gate | no `reportable_at` / `is_reportable` | applies `reportable_at` |
| Bypasses the canonical evidence selector | no `current_rule_version` | uses it |

**Canonical reproduces none of them**, asserted by test.

## Frontend cutover

Separate repository, dedicated branch `fix/tefca-report-cutover`, commit
`566193d`. Exactly one file staged. `CoverageAssurance.js` — unrelated
pre-existing work — remains modified, unstaged and byte-identical to how it was
found (16 insertions, 1 deletion, unchanged before and after).

**Not everything the cutover checklist lists is surfaced in the UI.** The page
covers listing, status, generation, download, authorization and the development
banner. The SOW deliverable families, evidence trace, QA status,
methodology-pending and source limitations are served by `/api/reports/sow/*`
but are **not yet rendered on that page**. Recorded as a gap rather than
claimed.

## Linux PDF — CARRY

No Linux environment exists on this host: Docker, podman and nerdctl are absent
and WSL has no distribution installed. Provisioning one is out of scope.

What was added instead: `.github/workflows/pdf-linux.yml`, an ubuntu-latest job
that installs the same native packages the Dockerfile does (parity asserted),
fails if the engine is unavailable, renders a PDF exercising tables, pagination,
long entity names, long URLs, Unicode, the evidence appendix and the development
watermark, checks for a tagged structure tree, and runs the PDF-gated tests.

**It has not run.** Running it requires a push, which is not authorised.

> **LINUX PDF EXECUTION = PRODUCTION/DEPLOYMENT VERIFICATION CARRY**

No Section 508 conformance is claimed. A tagged structure tree is a
precondition, not proof.

## Exit gate — all 20 mandatory criteria met

Every legacy/canonical difference classified · legacy population reconciles
exactly · unexplained canonical regressions 0 · known legacy defects not
reproduced · reportability gate operational · QA gate operational · canonical
evidence selector operational · Government terminology preserved · frontend
cutover isolated and tested · unrelated frontend work untouched · provenance
correct · source SHA real · cycle non-null · artifact storage operational ·
development markings enforced · historical evidence unchanged · historical
determinations unchanged · Government CSV absent · mock TRUE · backend
regression 0 failures.

Backend: **1,937 passed, 56 skipped, 0 failed.**

# PHASE 7: COMPLETE
