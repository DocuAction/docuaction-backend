# TEFCA ARC — Contract Acceptance Matrix

**Classification:** INTERNAL ENGINEERING / MANAGEMENT
**Date:** 2026-08-23 · **Branch:** `fix/tefca-stabilization` · **Commit:** `934c696`
**Evidence version:** `phase6-bulk-1.1.0`

Requirements rebuilt **directly from the authoritative contract documents**, then
compared against the existing 70-row Requirements Traceability Matrix.

---

## Part A — Authoritative document inventory

| Authority level | Document | Located | Path |
| --- | --- | --- | --- |
| 1. Executed award | **7571MN26F80064** | **NO** | **CONTRACT_SOURCE_MISSING** |
| 2. Incorporated PWS / SOW | — | **NO** | **CONTRACT_SOURCE_MISSING** (the only SOW on disk is for an unrelated project) |
| 3. Solicitation | RFQ 7571MN26Q00038, 06/16/2026 | **YES** | `C:\ONS HHS\06222026\Solicitation7571MN26Q00038 for TEFCA ARC_06162026.docx` (+ `.pdf`) |
| 3. Amendment | 3rd solicitation update | **YES** | `C:\ONS HHS\3rd_Solicitation…_update.docx` |
| 4. Government Q&A | 3rd Q&A, 06/18/2026 | **YES** | `C:\ONS HHS\06222026\3rd_Q&A_7571MN26Q00038_06182026 (2).docx` |
| 5. COR written direction | — | **NO** | None on file |
| 6. Approved methodology | — | **NO** | Draft only; not COR-approved |
| 7. AGT internal methodology | `TEFCA_REQUIREMENTS_DOCUMENT_V2.md` | YES | Repository |
| 8. System implementation | This codebase | YES | Repository |

### The finding that matters most

**The existing 70-row matrix was built from level 7 — an AGT internal
requirements document — not from the contract.** Section C of the solicitation
was never read into it. The consequences are set out in Part B: several mappings
are wrong, and an entire contractual requirement domain is missing.

**No executed award and no PWS are on this filesystem.** Everything below is
therefore traced to the solicitation and the Government Q&A. If the award
incorporated a modified PWS, this matrix must be re-verified against it.

---

## Part B — Independent requirement inventory vs the existing matrix

### B.1 EXISTING_REQUIREMENT_INCORRECT (5)

| # | Existing matrix says | Contract actually says | Source |
| --- | --- | --- | --- |
| 1 | Task 3 retrospective covers the **"First 90 Days"** | **"In the first one hundred and twenty (120) days of the contract award"** | §C Task 3 |
| 2 | Deliverable 6.2 is a **"data/system transition package"** | **"Closeout Educational Presentation"** — deliver to the COR to communicate the closeout report | §C Task 6; §F Item 6.2 |
| 3 | Priority-review volume **not stated**; templates deliberately assert none | **"an anticipated average of twenty (20) reviews per month"**, plus a duty to maintain surge capability beyond that average | §C Task 5; confirmed in Q&A |
| 4 | Confidence level **"configurable"** with documented z-values | Contract **mandates** a statistically representative sample **"at or above a 95% confidence level"** | §C Task 3 |
| 5 | B1–B4 is **"AGT internal, no federal basis"** | The **four categories are government-defined** in Section C — see B.4 | §C Tasks 3 and 4 |

### B.2 REQUIREMENT_MISSING_FROM_MATRIX (23)

**Deliverables and cadence (6)**

| # | Requirement | Source |
| --- | --- | --- |
| M1 | Orientation meeting within **5 business days** of award, with the COR | §C Task 1 |
| M2 | Meeting schedule: **60-minute weekly for the first 90 days, then 30-minute bi-weekly** | §C Task 1; §F Item 1 |
| M3 | **Deliverable 4.2 — Quarterly Reports**, every calendar quarter | §C Task 4; §F Item 4.2 |
| M4 | **Deliverable 5.2 — Quarterly Reports**, every calendar quarter | §C Task 5; §F Item 5.2 |
| M5 | Task 3 final report due **30 days following completion** of the retrospective review | §C Task 3; §F Item 3.2 |
| M6 | Closeout deliverables due on a date agreed with the COR, **within 90 days prior to contract expiration** | §F Items 6.1, 6.2 |

**Methodology (4)**

| # | Requirement | Source |
| --- | --- | --- |
| M7 | Methodology must **align to the Common Agreement, the QHIN Technical Framework (QTF), all SOPs and any other documents shared by the COR** | §C Task 2 |
| M8 | Methodology must include approaches for **stratifying and prioritising** Participants and Subparticipants for review | §C Task 2 |
| M9 | **Submit sampling methodology and confidence interval calculations** as part of Deliverable 2 | §C Task 2 |
| M10 | Reports must include **suggested and implemented changes to the methodology** | §C Tasks 3, 4 |

**Security, privacy and personnel (13) — the entire domain was absent**

| # | Requirement | Source |
| --- | --- | --- |
| M11 | **FIPS-199 categorisation: Confidentiality MODERATE, Integrity MODERATE, Availability MODERATE — Overall Impact Level MODERATE** | §C Baseline Security |
| M12 | NIST SP 800-53 controls at the applicable **Moderate** baseline | §C |
| M13 | **CUI** handling per EO 13556 / 32 CFR 2002 — marked, need-to-know, protected, returned or destroyed | §C |
| M14 | Privacy Act compliance; support agency **PTA**, and **PIA** if the PTA requires it; review every 3 years | §C |
| M15 | Encryption in transit and at rest, **FIPS-validated under CMVP** | §C Standard for Encryption |
| M16 | **NDA** for each employee with access to non-public government information | §C |
| M17 | **Annual** HHS Information Security Awareness, Privacy and Records Management training for all staff | §C Training |
| M18 | **Role-based training** for staff with significant security responsibilities | §C Training |
| M19 | HHS **Rules of Behavior** read and adhered to before data access | §C |
| M20 | **Incident reporting** to OpDiv IRT, COR, CO per US-CERT guidelines; **must NOT notify affected individuals unless instructed**; no sensitive data in email subject or body | §C Incident Response |
| M21 | **HSPD-12 PIV credentialing**; minimum **Tier 1 (NACI)** background investigation; position sensitivity designations | §C |
| M22 | **Staff roster** — name, position, email, phone, responsibility | §C |
| M23 | **Sanitisation** of government files at closeout; records retention per EO 13556 | §C |

### B.3 EXISTING_REQUIREMENT_CONFIRMED

The substance of Tasks 1–6 as functional capability is confirmed: intake, NPPES,
PECOS/PPEF, OIG LEIE, SAM.gov, address normalisation, tiering, sampling
machinery, reporting formats, audit trail. **56 of the 70 rows describe real
system capability that the contract does require**, even though they were
derived from the wrong document.

### B.4 The four-bucket taxonomy — a correction I owe

Earlier phases stated B1–B4 has no federal basis. **That was wrong**, and it was
wrong because the internal document was treated as the requirement source.

The contract states, three times (Task 3 weekly, Task 3 final, Task 4 bi-weekly):

> *"a stratified list of Participants and Subparticipants: 1) no discrepancies
> identified 2) minor or administrative discrepancies; 3) inexplicable
> discrepancies; and 4) non-compliant discrepancies"*

| Element | Correct classification |
| --- | --- |
| The **four categories** | **CONTRACT_DEFINED** (§C Tasks 3, 4) |
| The **labels "B1/B2/B3/B4"** | **AGT_INTERNAL_METHODOLOGY** — shorthand for the four contractual strata |
| Rules that assign an entity to a category | **AGT_INTERNAL_METHODOLOGY**, pending COR acceptance |
| Which tier reviews category 3 | **COR_DECISION_REQUIRED (D3)** |

"Inexplicable" in the decision brief matches the contract's category 3 exactly.
The categories are the government's; only the naming and the assignment rules are
AGT's. **Documents stating B1–B4 has no federal basis must be corrected.**

### B.5 NOT_CONTRACTUAL

Nothing in the existing matrix is fabricated. Its FR-T*-* rows are legitimate
system requirements — they are simply not traceable to a contract clause, because
they were written as internal engineering requirements.

---

## Part C/D — Contract operating modes

| # | Mode | CONTRACT SAYS | SYSTEM DOES | TEST PROVES | STATUS | ACTION |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Review Methodology | COR-accepted protocol within **2 weeks of award**, aligned to Common Agreement, QTF, SOPs | 26-section draft exists | Content tests | **PARTIAL** — QTF/Common Agreement/SOP alignment not evidenced; **deliverable is overdue** if award ≈ 25 Jun 2026 | Obtain QTF/CA/SOPs from COR; submit |
| 2 | Retrospective Review | **120 days**; statistically representative sample **per QHIN** | Full-population census of 23,566 | Reconciliation 18/18 | **PARTIAL** | Produce the per-QHIN sample and its confidence calculation for the deliverable |
| 3 | Statistical sampling | Representative sample **from each QHIN** | Cochran implemented, **not exercised** | Unit tests only | **GAP** | Draw and persist the per-QHIN sample |
| 4 | Confidence level | **At or above 95%** | Configurable | Unit tests | **PARTIAL** | Pin the floor to 95% and record it per run |
| 5 | Ongoing / net-new | Bi-weekly, within 30 days of each period | Implemented; one delivery only | Fixtures | **PARTIAL** — code validated, no production delta history | Second delivery |
| 6 | Priority Reviews | COR-identified issues | Implemented | Tests | **PASS** | — |
| 7 | Priority turnaround | Deadline **communicated by the COR** per review | Due date, `at_risk` ≤2 days, `overdue` | Tests | **PASS** | — |
| 8 | Priority volume | **Average 20/month** | No volume assumption; queue unbounded | — | **PARTIAL** | Capacity-plan for 20/month |
| 9 | Surge | Capability **beyond** the average, to the agreed deadline | Not capacity-tested | — | **GAP** | Load-test the priority path |
| 10 | RCE/QHIN verification | Verify Participant/Subparticipant accuracy | 23,566 records, 11 QHINs | Reconciliation | **PASS** | — |
| 11 | NPPES | Source (contract permits public data) | 18,671/18,673 resolved | Traceability | **PASS** | — |
| 12 | PECOS/PPEF | Source | 5 components ingested | Traceability | **PASS** | — |
| 13 | OIG LEIE | Source | 1 match, 2 AMBIGUOUS | Traceability | **PASS** | — |
| 14 | SAM.gov | Source | **Unevaluated — no credential** | — | **EXTERNAL_DEPENDENCY** | Obtain credential |
| 15 | CMS Revoked | Source | 22 matches | Traceability | **PASS** | — |
| 16 | Address verification | Accuracy of submitted information | 47,132 comparisons | Reconciliation | **PASS** (observation) / **COR_DECISION_REQUIRED** (materiality) | D4_ADDRESS_MATERIALITY |
| 17 | Entity relationships | Participant/Subparticipant structure | Hierarchy proven; 116,218 hops | Tests | **PASS** | — |
| 18 | Analyst review | Implicit in "thorough and high-quality review" | Implemented | Fixtures | **PASS** (capability); **GAP** (no human has acted) | Staff and run the pilot |
| 19 | Independent QA | Not explicitly required by §C | Implemented, SoD enforced | 43 tests | **PASS** — exceeds | — |
| 20 | APPROVE/RETURN/ESCALATE | Not contract language | Implemented | Tests | **PASS** — AGT control | — |
| 21 | Reporting | Weekly, bi-weekly, quarterly, final, closeout | Templates for all | Content tests | **PARTIAL** | Build 4.2 / 5.2 quarterly templates |
| 22 | COR submission | All deliverables to the COR, citing the contract number | Not implemented | — | **GAP** | Add contract-number citation to report headers |
| 23 | Auditability | Implicit | Full provenance | 6/6 reconstruction | **PASS** | — |
| 24 | Evidence reconstruction | Implicit | Verified | 6/6 artefacts re-hashed | **PASS** | — |
| 25 | Closeout | Report + **educational presentation**, within 90 days prior to expiration | Report skeleton only | — | **PARTIAL** | Add presentation deliverable |
| 26 | Security | **FIPS-199 MODERATE**; NIST 800-53 Moderate | Not assessed against 800-53 | Security tests are application-level | **GAP** | Control assessment against the Moderate baseline |
| 27 | Privacy / CUI | Privacy Act, PTA/PIA, CUI marking, FIPS-validated encryption | Not implemented as a programme | — | **GAP** | PTA; CUI marking; encryption attestation |
| 28 | Section 508 | HHS standards; **conformance must be demonstrated before deliverable acceptance** | Automated checks implemented | 15 tests | **PARTIAL** | Produce a VPAT/ACR per deliverable |
| 29 | Incident handling | Report to OpDiv IRT/COR/CO; **do not notify individuals unless instructed** | Playbook covers technical incidents only | — | **GAP** | Add the federal reporting path and the do-not-notify rule |
| 30 | Independence / OCI | OCI affirmation submitted with the quote | Not a system function | — | **NOT_APPLICABLE** to software; contract-administration item | Confirm OCI affirmation is on file |

---

## Part E — Methodology boundary

| Decision | Classification | Affects |
| --- | --- | --- |
| Four discrepancy categories | **CONTRACT_DEFINED** | Report structure, all stratified lists |
| 95% confidence floor | **CONTRACT_DEFINED** | Sampling, denominator |
| 20/month priority average + surge | **CONTRACT_DEFINED** | Capacity |
| 120-day retrospective window | **CONTRACT_DEFINED** | Schedule |
| B1–B4 labels | **AGT_INTERNAL_METHODOLOGY** | Naming only |
| Category assignment rules | **AGT_INTERNAL_METHODOLOGY** | Classification |
| **D1** uncorroborated NPI | **COR_DECISION_REQUIRED** | Classification, analyst eligibility |
| **D2** no rule matches | **COR_DECISION_REQUIRED** | Classification, reported statistics |
| **D3** category-3 review tier | **COR_DECISION_REQUIRED** | Analyst queue eligibility, staffing |
| **D4** source unavailable | **COR_DECISION_REQUIRED** | Source-limitation handling, reportability |
| **D4_ADDRESS_MATERIALITY** | **COR_DECISION_REQUIRED** | Discrepancy classification, 9,032 records |
| **D5** name discrepancy severity | **COR_DECISION_REQUIRED** | Classification, finding severity |
| **D6** identifier quality states | **COR_DECISION_REQUIRED** | Classification |
| **D7** automated exclusion finding | **COR_DECISION_REQUIRED** | Reportability, finding severity |
| **D8** retention | **COR_DECISION_REQUIRED** | Storage, closeout |
| **D9** deliverable format | **COR_DECISION_REQUIRED** | Reporting |
| Evidence versioning, triage, gates | **SYSTEM_IMPLEMENTATION_DETAIL** | Not COR business |

### COR decision table — those that change a reported number

| Decision | What moves |
| --- | --- |
| D4_ADDRESS_MATERIALITY | 10,426 observations / **9,032 records** enter or leave the discrepancy count |
| D4 (source unavailable) | **23,566 records** — whether an unevaluated SAM affects classification |
| D3 | Which queue works category 3, and therefore staffing |
| D5 | Whether name differences enter the discrepancy count at all |
| D7 | Whether an exclusion match can be reported without a human |
| D2 | Whether an unmatched combination defaults to category 1 |

---

## Summary

| | Count |
| --- | --- |
| Requirements independently identified from the contract | **58** |
| Existing 70-row matrix rows confirmed as real capability | 56 |
| EXISTING_REQUIREMENT_INCORRECT | **5** |
| REQUIREMENT_MISSING_FROM_MATRIX | **23** |
| DUPLICATE_REQUIREMENT | 0 |
| NOT_CONTRACTUAL (internal engineering requirements) | 14 |
| CONTRACT_SOURCE_MISSING | **2** (executed award, incorporated PWS/SOW) |

**Operating-mode coverage:** PASS 12 · PARTIAL 8 · GAP 7 · COR_DECISION_REQUIRED 1 ·
EXTERNAL_DEPENDENCY 1 · NOT_APPLICABLE 1

No code was changed on account of any discrepancy above. These go to management
and the COR first.
