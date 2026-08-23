# TEFCA ARC deliverables — what each document is, and who may see it

**Nothing in this directory is FINAL-RELEASE ELIGIBLE.** The dataset contractual
provenance gate is closed: the delivery's schema, lineage and content are all
verified, but its sender, transmittal and ONC-issued control total are not
documented. That is a contracts question, and no amount of engineering closes it.

## Classification

| Class | Meaning | Audience |
| --- | --- | --- |
| **INTERNAL ENGINEERING** | How the system works. Architecture, defects, corrections. | AGT engineering |
| **INTERNAL OPERATIONS** | How the work is performed. Checklists and procedures. | AGT analysts and QA |
| **COR-FACING DRAFT** | Written for the government, not yet releasable. Carries `DRAFT — NOT FOR COR RELEASE`. | AGT review, then COR when the gate opens |
| **FINAL-RELEASE ELIGIBLE** | All five release gates open. | COR |

## Contents

| Document | Class | Status |
| --- | --- | --- |
| `TEFCA_ARC_Review_Methodology_DRAFT.md` | COR-FACING DRAFT | Task 2 Deliverable 2. 26 sections. Contains 10 items marked PENDING COR DECISION. |
| `COR_Decision_Register.md` | COR-FACING DRAFT | D1–D9 plus `D4_ADDRESS_MATERIALITY`. **No decision recorded on any item.** |
| `templates/02_Retrospective_Review.md` | COR-FACING DRAFT | Task 3 Deliverable 3.2 |
| `templates/03_Ongoing_Review.md` | COR-FACING DRAFT | Task 4 Deliverable 4.1 |
| `templates/04_Priority_Review.md` | COR-FACING DRAFT | Task 5 Deliverable 5.1 |
| `templates/05_QA_Review_Checklist.md` | INTERNAL OPERATIONS | Maps to the implemented decision-event controls |
| `templates/06_Exception_Detail.md` | COR-FACING DRAFT | Appendix component |
| `templates/07_Evidence_Appendix.md` | COR-FACING DRAFT | Traceability component |
| `templates/08_Closeout_Report.md` | COR-FACING DRAFT | Task 6. Skeleton by design |
| `templates/09_Executive_Briefing.md` | COR-FACING DRAFT | Briefing format |
| `../onc_arc_deliverable_crosswalk.md` | INTERNAL ENGINEERING | Requirement-to-capability mapping |
| `../phase6_evidence_correction.md` | INTERNAL ENGINEERING | Why two evidence versions exist |

Generated data products live in `var/authoritative/` and are gitignored:
`arc_population_report.json` (the internal draft population report),
`phase65_triage_v11.json`, `phase65_correction_summary.json`.

## Core vs TEFCA-specific

Reusable across federal reporting, no TEFCA content:

- `app/reports/release_gates.py` — the five-gate model and the draft watermark
- `app/Tefca/evidence_version.py` — current-vs-historical evidence selection
- The `Metric` contract in `app/reports/data/arc_population_report.py` — no
  figure without a denominator, a calculation and a version
- `templates/07_Evidence_Appendix.md` — traceability structure

TEFCA/ARC-specific: the methodology document, the decision register, the
crosswalk, and the population figures in the other templates.

No agency-specific module has been built for an agency that has not asked for one.

## Rules these documents follow

1. **A number carries its denominator.** Observation counts and record counts are
   separate fields and are never interchanged. 10,426 address conflicts are 9,032
   records; both appear.
2. **An observation is not a finding.** A finding is a determination a human made
   and QA approved. There are currently **zero**.
3. **Pending methodology is stated, never resolved by default.** Address
   conflicts are not called failed, non-compliant, invalid, inaccurate or
   unverified, because no approved rule supports those words.
4. **An unavailable source is never an adverse result.** SAM.gov is unevaluated
   for all 23,566 records and is reported as unevaluated.
5. **B1–B4 is AGT's internal classification**, not a federal taxonomy.
6. **Reports read the current evidence version only.** Superseded evidence stays
   queryable and is never mixed in.

## Release status

| Gate | Status |
| --- | --- |
| Evidence version | **OPEN** — `phase6-bulk-1.1.0` |
| Human QA | **OPEN** — the population report asserts no findings |
| Methodology | **OPEN** — pending items disclosed, no conclusion drawn |
| Dataset contractual provenance | **CLOSED** |
| Report QA | **OPEN** |

One gate closed. Every document is therefore watermarked
`DRAFT — NOT FOR COR RELEASE`.

To open the last gate: record the dataset sender and transmittal, and reconcile
the delivery against an ONC-issued control total.
