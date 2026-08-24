# Phase 7 — certification record

**What was verified, what was fixed, and what is honestly not done.**
2026-08-23 · Branch `fix/tefca-stabilization` · Commits `c9c41fc`, `24ae032`

> ## DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY — NOT ONC FINDINGS
>
> The Government entity CSV has not been delivered or imported.
> `is_running_mock()` is **TRUE** and remained TRUE throughout. No count in this
> document is a Government finding.

---

## 1. Defects found and fixed

Three, all silent, all in the path every report reads.

### 1.1 Every report ever generated cited a fake source hash

`latest_rce_source_sha256()` read `tefca_import_batches` ordered by `created_at
desc`. The newest row in that table is a July 2026 unit-test fixture whose
`file_checksum` is the four-character string **`cafe`**. All five stored reports
carry it. The authoritative Area-1 digest sat unread.

The failure mode is the dangerous one: the field was *populated*, so nothing that
checked for a non-empty value noticed, and the reports looked provenanced.

**Fixed.** Provenance now reads Area 1 — the only authoritative delivery record —
and refuses anything that is not a real 64-hex SHA-256, distinguishing "no
delivery recorded" from "recorded but the checksum is unusable". Verified live:
reports now carry `689472073480b1cc…`, reproducible by hashing the file on disk.

### 1.2 Every stored report had a null review cycle

`review_reports` has no `review_cycle_id` column, and the snapshot's field was
`None` on all five. A stored report could not be scoped after the fact.

**Fixed.** Cycles resolve to
`DEV-CYCLE-<evidence rule version>-<source hash prefix>` — deterministic,
reproducible, and prefixed so it can never be read as a contract cycle label. The
contract's own cycle labelling is program-defined and not yet issued; inventing
one would have manufactured a contractual artefact.

### 1.3 The report population silently lost 37.5% of the evidence

`_dimension_rows` de-duplicated on `(entity_id, evidence_dimension)`. That
discarded **70,698 of 188,528** observations, because every entity has an ADDRESS
observation from NPPES **and** one from PPEF, and three EXCLUSION_REVOCATION
observations from three different sources. Those are not duplicates — the
disagreement between them is the finding.

Worse than the loss was *which* row survived. `generation_timestamp` is NULL on
all 188,528 population rows, so the tie-break compared `""` to `""` and the
winner was whichever row the database happened to return first. The reported
address-conflict figure would move between runs with no visible cause.

The same function also admitted **unversioned** rows, 716 of which carry an
automatic `PASS` — a disposition the Phase 6 architecture forbids because no
source may assert a pass without a human, and none of which can be attributed to
a rule generation.

**Fixed.** Key is `(entity_id, evidence_dimension, source)` — exactly unique at
188,528 — with a deterministic tie-break that never depends on row order.
Unversioned rows are excluded and **counted** in `evidence_scope`, not silently
dropped.

**Verified live through the canonical service:**

| | Before | After |
| --- | --- | --- |
| Observations reaching a report | 117,830 | **188,528** |
| Rows discarded | 70,698 | **0** |
| NPPES address conflicts | non-deterministic | **8,584** |
| PPEF address conflicts | non-deterministic | **1,842** |
| Distinct conflicting entities | non-deterministic | **9,032** |

---

## 2. Reproducibility (Steps 11–12)

Measured by generating each report twice from the same frozen evidence, using
the approved three-tier definition.

| Report type | Data equivalence | Semantic equivalence | Byte equivalence |
| --- | --- | --- | --- |
| executive | ✅ | ✅ | ❌ |
| verification | ✅ | ✅ | ❌ |
| data_quality | ✅ | ✅ | ❌ |
| intake | ✅ | ✅ | ❌ |

**Data equivalence** — identical `data_payload_hash`. This is the one that
matters: the numbers are provably the same.

**Semantic equivalence** — identical after removing the report ID and generation
timestamp.

**Byte equivalence does not hold, and should not.** Each generation advances the
report ID (`DA-ARC-2026-NNN`) and stamps a new timestamp. Two reports issued at
different moments *are* different documents; making them byte-identical would
require lying about when they were produced.

**What a snapshot preserves:** report ID, type, generation timestamp, generated
by, cycle, source delivery (filename, record count, SHA-256, schema
fingerprint), evidence generation, rule version, data classification, report data
service version, template version, `data_payload_hash`, PDF engine info,
accessibility result. Snapshots are append-only; a report is never regenerated in
place.

---

## 3. Accessibility (Step 13)

**Contract basis.** ¶291 — "Prior to acceptance of deliverables, the contractor
must demonstrate conformance"; ¶786 — delivered electronic content "must be
accessible to HHS acceptance criteria" and "should be accompanied by the
appropriate checklist".

| | Tested | Result |
| --- | --- | --- |
| Semantic headings | ✅ | Pass — `h1`/`h2` hierarchy, `aria-labelledby` on sections |
| Table headers | ✅ | Pass — `<caption>`, `<th scope="col">`, `<th scope="row">` |
| Image alt text | ✅ | Pass — enforced; the chart engine **refuses** a chart with empty alt text |
| Meaning not carried by colour | ✅ | Pass — status indicators are colour **+ shape + text**; the glyph is `aria-hidden`, the text carries the meaning |
| Contrast | ✅ | Pass — token contrast computed, not assumed |
| Document language and title | ✅ | Pass — `lang="en"`, `<title>` on every report |
| Development banner announced | ✅ | Pass — `role="note"` with an `aria-label` |
| No remote assets | ✅ | Pass — fonts inlined; a document that renders differently per machine is not a reliable record |
| **PDF tagging** | ⚠️ | **Requested, not validated.** See below. |
| **Keyboard operability** | ❌ | **Not tested.** Static documents with no interactive controls, but this is asserted, not measured. |
| **Screen-reader walkthrough** | ❌ | **Not performed.** |
| **DOCX** | ❌ | Not assessed. Not contractually required. |

### The honest PDF position

The engine requests `pdf_variant="pdf/ua-1"`, which asks WeasyPrint to emit a
**tagged structure tree**. A tagged tree is a **precondition** for an accessible
PDF, **not proof of one**, and the engine's own metadata says so. If the variant
is rejected it emits an untagged PDF and logs that loudly rather than reporting
a false pass.

**No Section 508 conformance is claimed for the PDF output.** Full PDF/UA
conformance has not been independently validated.

**PDF could not be exercised on this host at all.** WeasyPrint's Pango/Cairo/
GObject libraries are present only in the Linux container image; on Windows two
PDF tests skip. The PDF path is therefore **implemented and unverified locally**.

> **Remediation required:** independent PDF/UA validation in the Linux image, a
> keyboard and screen-reader pass, and per-deliverable HHS 508 checklists. Until
> those exist, the automated result establishes that the structure is sound — not
> that the deliverable conforms. `conformance_claim()` is deliberately worded to
> avoid overstating this.

---

## 4. Durable storage (Step 16) — NOT IMPLEMENTED

Stated plainly rather than reported as verified.

| Requirement | Status |
| --- | --- |
| Immutable finalised output | **Partial** — snapshots are append-only by convention; nothing at the storage layer enforces it |
| Content hash | ✅ `data_payload_hash` on every snapshot |
| Report metadata | ✅ full snapshot persisted in `review_reports.report_data` |
| Retention metadata | ❌ **Not implemented** |
| Access control | ✅ at the API (§6); the database is not separately segmented for reports |
| No accidental public access | ✅ no object store exists to be misconfigured |
| Azure Blob-style durable storage | ❌ **Not implemented** |

Reports persist to Postgres (`review_reports`, holding both the snapshot JSON and
the rendered HTML). That is durable in the ordinary sense — it is backed up with
the database — but it is **not** the object storage the earlier design selected,
and no blob integration exists anywhere in `app/`.

**WORM retention is deliberately not locked**, because the retention period is an
open COR decision (D8). Locking an irreversible retention policy before the
period is approved would be the harder mistake to undo.

---

## 5. Frontend (Step 17) — deliberately NOT repointed

Step 17 asks that the frontend reporting experience be pointed at
`/api/reports/*`. **It was not, and doing so today would be a regression.**

The frontend currently calls `/api/tefca/reports/*`
(`frontend/src/app/tefca-arc/reports/page.js`). That is the **legacy** path by
architecture — and it is also the **only** path that serves the contract's
deliverable families: weekly (D3.1), final (D3.2), bi-weekly (D4.1), quarterly
(D4.2/D5.2) and priority status (D5.1).

`/api/reports/*` is canonical in *machinery* — snapshots, provenance, the current
evidence selector, accessibility validation — but its report types are
engineering artefacts: verification, data_quality, intake. **None of them is a
contract deliverable.**

Repointing the UI now would take a user from the contract's reports to reports
that are not the contract's. The correct order is:

1. Migrate the SOW report families onto the canonical service. *(Not done — see
   §7.)*
2. Prove equivalence against identical fixtures.
3. Then repoint the frontend.
4. Then deprecate — archive, not delete.

The frontend is therefore unchanged in this phase, and the reason is recorded
rather than the step being quietly marked complete.

---

## 6. Report security (Step 21)

| Check | Result |
| --- | --- |
| Every canonical report endpoint is role-gated | ✅ `require_role` on all 7 |
| Generation requires a higher role than reading | ✅ generate = `contributor`; read = `viewer` |
| Finalised report immutability | ✅ append-only; no update path |
| No unauthorised reportability | ✅ reportability derives only from a standing QA approval |
| Direct-object-reference leakage | ✅ unknown `report_id` → 404, no existence oracle beyond the role gate |
| Secrets in report output | ✅ none — templates and data service contain no credential-shaped fields |
| Audit events | ✅ determinations and QA decisions recorded with actor, role and IP |
| **PII in report exports** | ⚠️ the CSV export is role-gated and explicitly marked as containing PII. Correct, and worth naming: it is the one report surface where authorisation is doing load-bearing work. |

---

## 7. Not done in Phase 7, and why

| Item | Why not |
| --- | --- |
| **SOW report families migrated to the canonical service** | The migration must be equivalence-tested against real deliverable output. There is no Government data to test against, and proving two code paths agree on development fixtures would not prove the deliverable is right. Scoped in the matrix §5; not executed. |
| **Legacy generators deprecated** | Per Step 22, deprecation requires proven equivalence first. Nothing was deleted. |
| **Frontend repointed** | §5 above. |
| **Durable object storage** | §4 above. Not implemented; not claimed. |
| **Per-QHIN sample draw** | `CODE_CHANGE_REQUIRED`. Requires approved sampling parameters — drawing before the COR confirms them yields an unusable sample. |
| **Scheduled generation of D4.2 / D5.2** | `CODE_CHANGE_REQUIRED`, not authorised in this phase. |
| **DOCX build-out** | Not a contract requirement (matrix §4). Already exists on the legacy path. Effort would be spent on a format nobody has asked for. |
| **Independent PDF/UA validation** | Cannot run on this host. |

---

## 8. D1–D9 boundary (Step 9)

All nine remain **PENDING COR DECISION**. None was resolved in code.

| ID | Question | Blocks a report conclusion? | Required for the report engine? |
| --- | --- | --- | --- |
| D1 | Uncorroborated NPI — how classified? | **Yes** — the classification of affected records | No |
| D2 | No rule matches — what result? | **Yes** — reachable path with an undefined outcome | No |
| D3 | B3 — Reviewer or Senior Analyst tier? | No — staffing | No |
| D4 | Source unavailable — classification or readiness? | **Yes** — whole population (SAM.gov) | No |
| D4_ADDRESS_MATERIALITY | Which address differences are material? | **Yes** — 10,426 observations / 9,032 entities | No |
| D5 | Which name differences are reportable? | **Yes** | No |
| D6 | "Flagged" vs "invalid" identifier | **Yes** | No |
| D7 | Potential exclusion match — automated finding? | **Yes** — gates automated non-compliance findings | No |
| D8 | Records retention period | No | **Yes** — blocks WORM retention (§4) |
| D9 | Official deliverable format | No | **Yes** — blocks format commitment (matrix F1) |

**None of the nine blocks the report engine from operating.** Seven block
specific *conclusions*; two block *operational commitments*. The engine reports
affected items as awaiting methodology, with counts, rather than resolving them.

Reports distinguish four kinds of statement and never blur them: **factual
observation**, **AGT methodology result**, **human determination**, and
**program-guidance-pending**. Methodology-pending items are shown, never hidden —
a suppressed open question becomes an embedded assumption.

---

## 9. Integrity (Step 26)

Baseline captured before any change, re-run after. **Byte-identical.**

| | Value |
| --- | --- |
| Database | `docuaction-db` (development) |
| `is_running_mock()` | **TRUE** |
| Canonical evidence version | `phase6-bulk-1.1.0` |
| Area-1 records / digest | 23,566 / `24524f70c370d6c42a2b03d5385295a5` |
| Area-1 artefact SHA-256 | `689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d` |
| Observations 1.0.0 / digest | 164,962 / `84384bcd7aef04b137e30eb88848e2ee` |
| Observations 1.1.0 / digest | 188,528 / `bd012e2d3dc220b4c91d281933ad6482` |
| Relationship hops 1.0.0 / 1.1.0 | 39,749 / 116,218 |
| All hops digest | `95a23fe34a1872da4a57455c2b2c4824` |
| `review_records` | **43**, reportable **0** |
| `review_decision_events` | **0** |
| Government CSV present | **No** |

No evidence row was inserted, updated or deleted. No review record was made
reportable. No QA decision event was created. Every report generated during this
phase used `persist=False`.

---

## 10. Tests

| | |
| --- | --- |
| Baseline | 1,756 passed · 38 skipped · 0 failed |
| After Phase 7 | **1,826 passed · 49 skipped · 0 failed** |
| New | **+70 passing**, +11 skipped |

New files: `tests/test_phase7_provenance.py` (39),
`tests/test_phase7_report_data.py` (9 passing + 11 gated on a live development
database the harness does not provide), `tests/test_phase7_pilot.py` (22).

One existing test updated: `test_untracked_provenance_says_so_rather_than_omitting`
pinned the literal string "Not yet tracked (Area 1 pending)", which no longer
exists. It now asserts the invariant it was always about — the row is present and
names its reason.

**The 11 skips are worth naming rather than glossing.** They are the tests that
derive 8,584 from live persisted evidence. The harness points `DATABASE_URL` at a
test instance that does not exist, so they skip locally. The same figures were
verified directly against the development database through the canonical service,
and the de-dup regression they guard is additionally pinned by fixture-driven
tests that run everywhere.
