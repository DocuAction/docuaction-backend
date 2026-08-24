# ONC/ASTP TEFCA ARC — contract deliverable crosswalk

**Classification:** INTERNAL ENGINEERING
**Branch:** `fix/tefca-stabilization` · **Evidence version:** `phase6-bulk-1.1.0`
**Date:** 2026-08-23

**Source material.** Every task, deliverable and requirement below is taken from
`docs/TEFCA_REQUIREMENTS_DOCUMENT_V2.md` §3.1–3.7 (FR-T1 … FR-T6) and
`docs/cor_decision_brief_v2.md` (D1–D9). **Nothing in this crosswalk is invented.**
Where the source material is silent — notably on priority-review volume, surge
thresholds and numeric SLA targets — the row says so rather than supplying a number.

---

## Task 1 — Administrative / Kickoff (Deliverable 1)

*Trigger: schedule within 3 business days; kickoff within 5 business days of award.*

| Requirement | Data required | Workflow | Satisfying output | Readiness | Gap |
| --- | --- | --- | --- | --- | --- |
| FR-T1-001 live demonstration against real NPIs | NPPES bulk index | Phase-6 enrichment | Executive briefing | **READY** — 18,671 of 18,673 NPIs resolved | — |
| FR-T1-002 bundled dev dataset flagged `data_source="MOCK"` | `rce_mock_enrichment` | — | — | **READY** | Labelling must not be removed |
| FR-T1-003 demo cycle endpoint, admin-gated | — | RBAC | — | READY (dev) | — |
| FR-T1-004 health / connector status | connector logs | — | — | READY | — |
| FR-T1-005 role-appropriate dashboards | — | RBAC | — | READY | — |
| FR-T1-006 demo activity in audit trail | `tefca_reg_audit_log` | — | — | READY | — |

## Task 2 — Review Methodology and Control Framework (Deliverable 2)

*Trigger: COR-reviewed methodology within 2 weeks of award.*

| Requirement | Satisfying output | Readiness | Gap |
| --- | --- | --- | --- |
| FR-T2-001 B1–B4 taxonomy | Methodology §12 | READY | **B1–B4 is AGT's internal operational classification.** No source material establishes it as an ONC, ASTP, RCE, Sequoia or federal taxonomy, and the methodology says so explicitly. |
| FR-T2-002/003 methodology + taxonomy as reference endpoints | existing endpoints | READY | — |
| FR-T2-004–007 Cochran sample size, confidence, margin of error, recorded per sample | Methodology §20 | READY | Not yet exercised on the delivered population |
| FR-T2-008/009 three-tier routing + escalation | Methodology §14–17 | READY | Tiering is implemented on the legacy queue; the canonical path uses `review_records` + decision events |
| FR-T2-010/011 source hierarchy + conflict resolution | Methodology §5, §12 | **PARTIAL** | Conflict *detection* is implemented and evidenced. Conflict *resolution* where sources disagree on address is **PENDING COR DECISION (D4_ADDRESS_MATERIALITY)** |
| FR-T2-012 five-element evidence record | `tefca_dimension_evidence` | READY | — |
| FR-T2-013/014 versioned review rules, admin-only | `review_rules` | READY | — |

## Task 3 — Retrospective Review, first 90 days (Deliverables 3.1, 3.2)

| Requirement group | Satisfying output | Readiness | Gap |
| --- | --- | --- | --- |
| FR-T3-001–007 import, NPI Luhn, per-row rejection, injection scan, SHA-256, import history | Area-1 intake | **READY** — 23,566 records, artefact hash recorded, reconciliation 18/18 | — |
| FR-T3-008/009 NPPES identity and taxonomy | Phase-6 evidence | READY | — |
| FR-T3-010 PECOS payment suspension reported as `None`, never fabricated | — | READY | The PPEF extract does not publish it |
| FR-T3-011 OIG LEIE exclusion | Phase-6 evidence | READY | — |
| FR-T3-012 SAM.gov registration | — | **BLOCKED** | No `SAM_GOV_API_KEY`. All 23,566 records carry `SOURCE_UNAVAILABLE` under **PENDING COR DECISION (D4)** |
| FR-T3-013 USPS Publication 28 normalisation | `address_comparison` | **PARTIAL** | USPS-style suffix/directional normalisation implemented; not a certified Pub-28 implementation |
| FR-T3-014 USPS Address API v3 | — | **CONFIGURED, UNUSED** | Zero production calls to date, as the source document already records |
| FR-T3-015 Jaro-Winkler name matching | existing | READY | Not used in the population run; exclusion name matching is exact and yields AMBIGUOUS |
| FR-T3-016/017 B1–B4 classification; refuse to auto-classify MOCK as B1 | Methodology §12 | READY | No classification has been applied to the delivered population — that requires human review |
| FR-T3-018–022 tiering, queues, reclassification, escalation | canonical review path | READY | — |
| FR-T3-023–025 reproducible sampling, persisted membership, completion stats | Methodology §20 | READY | Not yet exercised |
| FR-T3-026 prioritise QHIN by volume | QHIN distribution known (11 QHINs, 10,481 max) | **VERIFICATION REQUIRED** — unchanged from source | — |
| FR-T3-027 weekly stratified progress reports | Retrospective template | **TEMPLATE READY** | — |
| FR-T3-028/029 PDF, DOCX, CSV, Excel, HTML | report engine | **PARTIAL** | HTML and CSV verified locally. PDF depends on WeasyPrint native libraries, absent on this Windows workstation and present in the Linux container image |
| FR-T3-030–033 audit trail, connector logging, caching, findings with reason codes | existing | READY | — |
| FR-T3-034 final retrospective report | Retrospective template | **TEMPLATE READY** | Cannot be issued: Gate 4 closed |

## Task 4 — Ongoing Bi-Weekly Review (Deliverable 4.1)

| Requirement | Readiness | Gap |
| --- | --- | --- |
| FR-T4-001–004 continuous ingestion through the same pipeline and tiering, cycle type `TASK4_ONGOING` | READY | — |
| FR-T4-005/006 bi-weekly reports, QA-lead gated | **TEMPLATE READY** | — |
| FR-T4-007 distinguish new from returning entities | **VERIFICATION REQUIRED** | Only one delivery has been ingested, so add/change/remove has never been exercised. The Ongoing template defines the comparison; it is untested against a second delivery |
| FR-T4-008–012 cycle metrics, listing, per-cycle stats, version history | READY | — |

## Task 5 — Priority Reviews (Deliverable 5.1)

| Requirement | Readiness | Gap |
| --- | --- | --- |
| FR-T5-001–004 ad-hoc priority verification, cycle type `TASK5_PRIORITY`, severity, full pipeline | READY | — |
| FR-T5-005–008 due date, `overdue`, `at_risk` at ≤2 days, SLA dashboard | READY | **The source material states the `at_risk` threshold (2 days) but no monthly volume and no surge threshold.** No number is assumed here |
| FR-T5-009 per-case report | **TEMPLATE READY** | — |
| FR-T5-010/011 root cause analysis; RBAC | READY | — |

## Task 6 — Contract Closeout (Deliverables 6.1, 6.2)

| Requirement | Readiness | Gap |
| --- | --- | --- |
| FR-T6-001–003 final and quarterly reports, PM-gated | **SKELETON ONLY** | Deliberately a skeleton — closeout content depends on what the period actually contained |
| FR-T6-004/005 audit trail and machine-readable export | READY | — |
| FR-T6-006 retention | READY | Retention period is **PENDING COR DECISION (D8)** |
| FR-T6-007/008 downloadable artifacts, per-user activity trail | READY | — |

---

## Summary

| | Count |
| --- | --- |
| Requirements mapped | **75** (FR-T1 ×6, T2 ×14, T3 ×34, T4 ×12, T5 ×11, T6 ×8 — less overlap) |
| READY | 60 |
| PARTIAL / TEMPLATE-ONLY | 11 |
| BLOCKED or VERIFICATION REQUIRED | 4 |

### The four that are not merely partial

1. **FR-T3-012 SAM.gov** — no API key. Not an engineering gap; a credential decision. Recorded as `SOURCE_UNAVAILABLE`, never as an entity finding.
2. **FR-T4-007 new vs returning entities** — one delivery ingested, so delta logic is unexercised.
3. **FR-T3-026 QHIN prioritisation by volume** — carried forward from the source document unchanged.
4. **FR-T2-010/011 conflict resolution** — detection works; resolution of address disagreement is PENDING COR DECISION.

### Requirements the source material does not state

Priority-review monthly volume, surge thresholds, and numeric turnaround targets
beyond the `at_risk` ≤2-day banding. These are **not** assumed. The Priority
Review template carries an SLA clock and computes turnaround, but no target is
hard-coded.
