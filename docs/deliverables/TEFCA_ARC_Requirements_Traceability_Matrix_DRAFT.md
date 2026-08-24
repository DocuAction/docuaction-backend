# TEFCA ARC — Requirements Traceability Matrix

**DRAFT — NOT FOR COR RELEASE** · Version 1.0 · 2026-08-23
Source: `docs/TEFCA_REQUIREMENTS_DOCUMENT_V2.md` §3.1–3.7 · Evidence `phase6-bulk-1.1.0`

## Classification used

| Label | Meaning |
| --- | --- |
| **IMPLEMENTED** | Code exists and runs |
| **VERIFIED** | Implemented *and* exercised by a passing test or a live run |
| **PARTIAL** | Works within a stated limit |
| **EXTERNAL DEPENDENCY** | Blocked on something outside engineering |
| **PENDING COR DECISION** | Blocked on a programme decision |
| **NOT APPLICABLE** | Out of scope for this delivery |

**No requirement is labelled COMPLIANT.** Compliance is a determination the
government makes, not a status a contractor assigns itself.

---

## Task 1 — Administrative / Kickoff (6)

| Req | Status | Evidence |
| --- | --- | --- |
| FR-T1-001 demonstration against real NPIs | **VERIFIED** | 18,671 of 18,673 NPIs resolved against retained NPPES |
| FR-T1-002 bundled dev dataset flagged MOCK | **VERIFIED** | `rce_mock_enrichment` markers absent from the delivery; labelling intact |
| FR-T1-003 demo endpoint, admin-gated | **IMPLEMENTED** | RBAC |
| FR-T1-004 health / connector status | **IMPLEMENTED** | |
| FR-T1-005 role dashboards | **IMPLEMENTED** | |
| FR-T1-006 demo activity audited | **VERIFIED** | 23,812 audit rows |

## Task 2 — Methodology and Control Framework (14)

| Req | Status | Evidence |
| --- | --- | --- |
| FR-T2-001 B1–B4 taxonomy | **IMPLEMENTED** | Declared throughout as an AGT **internal** classification |
| FR-T2-002/003 methodology + taxonomy endpoints | **IMPLEMENTED** | |
| FR-T2-004–007 Cochran, confidence, margin, recorded per sample | **IMPLEMENTED** | Not exercised on the delivered population — the current run is a census |
| FR-T2-008/009 three-tier routing and escalation | **PARTIAL** | Implemented on the legacy queue; the canonical path uses `review_records` + decision events. Tier assignment for B3 is **PENDING COR DECISION (D3)** |
| FR-T2-010 source validation hierarchy | **VERIFIED** | Applicability decided before every lookup |
| FR-T2-011 conflict resolution | **PENDING COR DECISION** | Detection verified (10,426 conflicts). Resolution is `D4_ADDRESS_MATERIALITY` |
| FR-T2-012 five-element evidence record | **VERIFIED** | 188,528 observations, complete mandatory provenance |
| FR-T2-013/014 versioned rules, admin-only | **IMPLEMENTED** | |

## Task 3 — Retrospective Review (34)

| Req group | Status | Evidence |
| --- | --- | --- |
| FR-T3-001–007 import, Luhn, per-row rejection, injection scan, SHA-256, import history | **VERIFIED** | 23,566 records; artefact hash re-verified; reconciliation 18/18 |
| FR-T3-008/009 NPPES identity and taxonomy | **VERIFIED** | 18,976 matched |
| FR-T3-010 payment suspension never fabricated | **VERIFIED** | PPEF does not publish it; reported as unavailable |
| FR-T3-011 OIG LEIE | **VERIFIED** | 1 NPI match, 2 AMBIGUOUS |
| FR-T3-012 SAM.gov | **EXTERNAL DEPENDENCY** | No credential. 23,566 `SOURCE_UNAVAILABLE`; **PENDING COR DECISION (D4)** |
| FR-T3-013 USPS Pub-28 normalisation | **PARTIAL** | USPS-style suffix/directional normalisation; not a certified Pub-28 implementation |
| FR-T3-014 USPS Address API v3 | **EXTERNAL DEPENDENCY** | Configured; zero production calls, as the source document records |
| FR-T3-015 Jaro-Winkler name matching | **PARTIAL** | Implemented; the population run used exact name matching, yielding AMBIGUOUS |
| FR-T3-016/017 B1–B4 classification; MOCK never auto-B1 | **IMPLEMENTED** | No classification applied to the delivered population — that requires human review |
| FR-T3-018–022 tiering, queues, reclassification, escalation | **VERIFIED** | Canonical path tested |
| FR-T3-023–025 reproducible sampling | **IMPLEMENTED** | Not exercised; the current run is a census |
| FR-T3-026 QHIN prioritisation by volume | **PARTIAL** | Distribution known (11 QHINs, max 10,481). Carried forward as VERIFICATION REQUIRED from the source document |
| FR-T3-027 weekly stratified reports | **IMPLEMENTED** | Template ready |
| FR-T3-028 PDF, DOCX, CSV, Excel | **PARTIAL** | CSV/structured verified; PDF requires container-only native libraries |
| FR-T3-029 HTML | **VERIFIED** | |
| FR-T3-030–033 audit trail, connector logging, caching, findings with reason codes | **VERIFIED** | |
| FR-T3-034 final retrospective report | **IMPLEMENTED** | Template ready; cannot be issued — Gate 4 closed |

## Task 4 — Ongoing Bi-Weekly Review (12)

| Req | Status | Evidence |
| --- | --- | --- |
| FR-T4-001–004 continuous ingestion, same pipeline, same tiering, `TASK4_ONGOING` | **IMPLEMENTED** | |
| FR-T4-005/006 bi-weekly reports, QA-lead gated | **IMPLEMENTED** | Template ready |
| FR-T4-007 new vs returning entities | **PARTIAL — CODE/CONTROL VALIDATED ONLY** | Only one delivery exists. Delta semantics are defined and unit-validated; **production delta history is NOT validated** |
| FR-T4-008–012 cycle metrics, listing, stats, version history | **IMPLEMENTED** | |

## Task 5 — Priority Reviews (11)

| Req | Status | Evidence |
| --- | --- | --- |
| FR-T5-001–004 ad-hoc verification, `TASK5_PRIORITY`, severity, full pipeline | **IMPLEMENTED** | |
| FR-T5-005–008 due date, `overdue`, `at_risk` ≤2 days, SLA dashboard | **IMPLEMENTED** | The ≤2-day banding is the only timing the source states; no volume or surge target is asserted |
| FR-T5-009 per-case report | **IMPLEMENTED** | Template ready |
| FR-T5-010/011 root cause; RBAC | **IMPLEMENTED** | |

## Task 6 — Closeout (8)

| Req | Status | Evidence |
| --- | --- | --- |
| FR-T6-001–003 final and quarterly reports, PM-gated | **PARTIAL** | Skeleton by design — content depends on a period that has not occurred |
| FR-T6-004/005 audit trail and machine-readable export | **IMPLEMENTED** | |
| FR-T6-006 retention | **PENDING COR DECISION (D8)** | |
| FR-T6-007/008 downloadable artifacts, per-user activity | **IMPLEMENTED** | |

---

## Summary

| Status | Count |
| --- | --- |
| VERIFIED | 21 |
| IMPLEMENTED | 34 |
| PARTIAL | 10 |
| EXTERNAL DEPENDENCY | 2 |
| PENDING COR DECISION | 3 |
| NOT APPLICABLE | 0 |
| **Total mapped** | **70** |

*(75 requirement IDs collapse to 70 distinct traceable rows where a row covers a
contiguous group.)*
