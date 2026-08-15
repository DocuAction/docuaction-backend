# TEFCA Document Remediation Log

**System:** DocuAction TEFCA ARC Platform
**Contract:** 7571MN26F80064
**Contractor:** Alliance Global Tech, Inc. (AGT)
**Agency:** HHS/ONC (ASTP)
**Date:** 13 August 2026

Records the changes applied to the Requirements Document (RD) and System Design Document (SDD) in moving from **Version 1.0** to **Version 2.0 (HHS Client Ready)**.

---

## 1. Scope of this remediation — read this first

> **The 33-section remediation specification was never received.** Two messages requested "all 33 sections"; both arrived containing only the wrapper (system context, output paths, formatting, diagram rules, accuracy rules) and ended with the literal, unfilled placeholder `[PASTE YOUR FULL 33-SECTION PROMPT HERE]`. Sections 1–33 have not been provided.

Accordingly, **this remediation applies only the corrections that were stated concretely and unambiguously.** No remediation item was inferred, invented, or guessed at. A remediation log that recorded findings its author had imagined would be worse than an incomplete one.

**Applied:** the USPS status correction, the figure-numbering correction, the Version 2.0 formatting and header, and the specified output artifacts.

**Not applied:** whatever Sections 1–33 require. Until that specification is supplied, Version 2.0 should be treated as *v1.0 plus two corrections*, **not** as a completed client-ready remediation.

---

## 2. Source and output artifacts

| Role | File |
|---|---|
| Source (v1.0, committed `bc5a827`) | `docs/TEFCA_REQUIREMENTS_DOCUMENT.md`, `docs/TEFCA_SYSTEM_DESIGN_DOCUMENT.md` |
| Source (v2.0, regenerable) | `docs/TEFCA_REQUIREMENTS_DOCUMENT_V2.md`, `docs/TEFCA_SYSTEM_DESIGN_DOCUMENT_V2.md` |
| **Deliverable** | `docs/TEFCA_Requirements_Document_HHS_Client_Ready.docx` |
| **Deliverable** | `docs/TEFCA_System_Design_Document_HHS_Client_Ready.docx` |
| **Deliverable** | `docs/TEFCA_Document_Remediation_Log.md` (this file) |
| Copies | `C:\mnt\user-data\outputs\` (both `.docx`) |

The v2.0 Markdown sources are retained so the `.docx` can be regenerated deterministically rather than hand-edited. The v1.0 files are unchanged, so v2.0 diffs cleanly against them.

---

## 3. Correction R-01 — USPS integration status

**Severity:** High (accuracy defect in a Government deliverable)
**Trigger:** Client-supplied accuracy rule — *"USPS = configured, zero production calls to date."*

### Finding

Version 1.0 represented USPS address verification as an active, current integration. That was derived from code inspection: `USPSConnector` implements Address APIs v3 with OAuth 2.0, and nine `USPS_*` environment variables are wired. **Implementation presence is not operational use.** The same distinction was correctly applied to IQVIA (`PLANNED/DISABLED`) and SAM.gov (`DEGRADED`) in v1.0 but was missed for USPS.

### Changes applied (4 locations)

| # | Document | Location | v1.0 | v2.0 |
|---|---|---|---|---|
| 1 | RD | §3.3 `FR-T3-014` status | `CURRENT` | `CONFIGURED — zero production calls to date` |
| 2 | RD | §4.5 `NFR-IR-004` status | `CURRENT` | `CONFIGURED — zero production calls to date` |
| 3 | SDD | §3.3.3 Verification Pipeline, *Behaviour* | "USPS Pub 28 address normalization" | "USPS Pub 28 address normalization (USPS API **configured; zero production calls to date**)" |
| 4 | SDD | §3.7 Communication Architecture, USPS row | "Address standardization" | "Address standardization — **configured; zero production calls to date**" |

### Note on scope

USPS Publication 28 **address normalization** is a local algorithm and remains accurate as described; it is unaffected by this correction. Only the **USPS API verification call** is restated. The two are distinct capabilities and v2.0 keeps them distinct.

---

## 4. Correction R-02 — Figure numbering, titles and captions

**Severity:** Medium (Section 508 and federal document-convention compliance)
**Trigger:** Client-supplied diagram rule — figure number and title above, descriptive paragraph below, alt text for 508.

### Finding

Version 1.0 contained 9 diagrams with Section 508 alt text on every one, but **zero numbered figures** and no captions. Diagrams could not be cross-referenced from body text, and the document did not meet federal figure-labelling convention.

### Changes applied (9 figures)

| Figure | Document | Section | Title |
|---|---|---|---|
| 1 | RD | §2.1 | DocuAction in the TEFCA Ecosystem |
| 2 | RD | App. C.1 | Entity Verification Workflow |
| 3 | RD | App. C.2 | Review Lifecycle State Transitions |
| 4 | RD | App. C.3 | Entity Lifecycle State Machine |
| 5 | RD | App. C.4 | B1–B4 Classification Decision Tree |
| 6 | RD | App. D | Logical Data Model — Entity-Relationship Overview |
| 1 | SDD | §3.1 | System Context |
| 2 | SDD | §3.2 | Logical Architecture |
| 3 | SDD | §4.5 | Primary User Flow |

Each now carries: **bold numbered title above** the diagram → the diagram (Word-compatible monospaced text with `→` arrows) → **descriptive caption below** → **retained alt text** for Section 508.

Existing alt text was preserved verbatim, not rewritten — it had already been drafted to describe structure rather than appearance.

---

## 5. Formatting applied (both v2.0 documents)

| Attribute | Setting | Verified |
|---|---|---|
| Body font | Arial 12 pt | ✔ |
| Heading font | Arial, navy `#003087`, bold | ✔ |
| Margins | 1 inch all sides | ✔ |
| Line spacing | 1.15 | ✔ |
| Header | "[Title] — **Version 2.0**" | ✔ |
| Footer | "Alliance Global Tech, Inc. \| Contract 7571MN26F80064 \| CONFIDENTIAL \| Page X of Y" (`PAGE`/`NUMPAGES` fields) | ✔ |
| Heading styles | True Word Heading 1–4 (508 navigable heading tree) | ✔ |
| Table headers | Navy fill, white bold text, repeat-on-page-break | ✔ |
| Page breaks | Between top-level sections | ✔ |
| TOC | `TOC` field (F9 to populate) | ✔ |

**Contrast:** `#003087` on white measures 12.4:1, well above the 4.5:1 WCAG AA minimum.

### Output verification

| Document | Size | Headings | Tables | Figures | Header |
|---|---|---|---|---|---|
| RD v2.0 | 65 KB | 46 | 32 | 6 | Version 2.0 |
| SDD v2.0 | 74 KB | 129 | 93 | 3 | Version 2.0 |

---

## 6. Version history rows added

A Version 2.0 row was added to the Document Control table of both documents, describing the USPS correction and figure numbering and pointing to this log.

---

## 7. Accuracy rules — verification status in v2.0

All twelve client-supplied accuracy rules were re-checked against the v2.0 text.

| Rule | v2.0 status |
|---|---|
| PECOS = NPPES-derived, not direct integration | ✔ Already correct in v1.0 — retained |
| Audit = append-only, not immutable | ✔ Retained, with explicit warning callouts |
| FIPS 199 = assumed Moderate | ✔ Retained |
| NIST 800-53 = self-assessed | ✔ Retained |
| Section 508 = in progress, no VPAT | ✔ Retained |
| FedRAMP = not pursued | ✔ Retained |
| AI = advisory, disabled by default | ✔ Retained |
| System = pre-production demonstration mode | ✔ Retained |
| RCE data = from ONC, not queried directly | ✔ Retained |
| SAM.gov = configured but upstream 404 | ✔ Already `DEGRADED` in v1.0 — retained |
| **USPS = configured, zero production calls to date** | **✔ CORRECTED in v2.0 — see §3** |
| All entity data from ONC per contract direction | ✔ Retained |

---

## 8. SOW traceability — unchanged and re-verified

| SOW Task | FRs | Deliverables |
|---|---|---|
| Task 1 — Administrative / Kickoff | 6 | D1 |
| Task 2 — Review Methodology & Control Framework | 14 | D2 |
| Task 3 — Retrospective Review (90 days) | 34 | D3.1, D3.2 |
| Task 4 — Ongoing Bi-Weekly Review | 12 | D4.1 |
| Task 5 — Priority Reviews | 11 | D5.1 |
| Task 6 — Contract Closeout | 8 | D6.1, D6.2 |
| Cross-cutting | 33 | All |
| **Total** | **118** | |

Non-functional requirements: **48**. The RTM at RD Appendix E retains bidirectional traceability (SOW Task → Requirement → Design Element → Verification). Neither correction altered any requirement identifier, so all traceability links from v1.0 remain valid.

---

## 9. Application code

**No application code was modified.** This remediation touched `docs/` only. The test baseline is unchanged at **804 passed, 24 skipped, 0 failures**.

---

## 10. Items still open (carried forward from v1.0)

These were flagged in v1.0 and remain unresolved. They are **not** defects introduced by this remediation.

| # | Item | Location |
|---|---|---|
| 1 | `FR-T3-026` (QHIN prioritization by entity volume) — **VERIFICATION REQUIRED** | RD §3.3 |
| 2 | `FR-T4-007` (new vs returning entity distinction) — **VERIFICATION REQUIRED** | RD §3.4 |
| 3 | Performance figures are design targets, not measurements — no load test on record | RD §4.2, SDD §3.8 |
| 4 | OpenAI BAA status — **VERIFICATION REQUIRED** | SDD §3.6.6 |
| 5 | `RCE_DIRECTORY_API_KEY` confirmed vestigial — recommend removal | SDD §3.3.4, §6.3 |
| 6 | No RTO/RPO targets documented; no failover environment | SDD §3.4 |
| 7 | Production runs an earlier build; TEFCA reads still floored at `reviewer` in prod | RD App. F, SDD §6.4 |
| 8 | No formal 508 conformance testing; no VPAT | RD §4.7, SDD §4.7 |

---

## 11. Recommended next action

Supply the 33-section remediation specification. On receipt, this log will be extended with a section per remediation item, and v2.0 regenerated from the retained Markdown sources.

Until then, **do not represent Version 2.0 to the Government as a completed client-ready remediation** — it is Version 1.0 with two corrections applied, and this log is the evidence of exactly which two.

---

*End of Remediation Log.*
