# Phase 7 — Contract Reporting Matrix

**Contract 7571MN26F80064** (RFQ 7571MN26Q00038) · Alliance Global Tech · COR JaWanna Henry
**Prepared:** 2026-08-23 · **Branch:** `fix/tefca-stabilization`

> ## DATA IDENTITY — READ BEFORE ANY NUMBER IN THIS DOCUMENT
>
> **The Government entity CSV has not been delivered or imported.**
> `is_running_mock()` is **TRUE**. Every count produced by the reporting system
> today is a **DEVELOPMENT / TEST** validation result, **NOT an ONC finding**.
> The contract population is **94,231 unique connections** sampled to **383
> entities**, of which **0 have been reviewed**. Nothing in this document
> reports a Government result; it describes what the reporting machinery must
> produce and whether it can.

---

## 0. How to read this document

Four labels are used throughout, and they are not interchangeable. The whole
point of the matrix is that the boundary between them stays visible.

| Label | Meaning | Authority |
| --- | --- | --- |
| **CONTRACT REQUIREMENT** | Stated in the solicitation. Quoted or cited to a paragraph. | RFQ 7571MN26Q00038, §Tasks |
| **AGT METHODOLOGY** | AGT's chosen approach, submitted in D2. Binding on AGT once the COR accepts it; not a Government requirement before then. | D2, submitted 9 Jul 2026 |
| **AGT IMPLEMENTATION** | A decision made in this codebase. Changeable without contract action. | This repository |
| **PROGRAM GUIDANCE PENDING** | Genuinely unanswered. Not an AGT choice, and must not be presented as one. | Open |

**Source of the contract text.** All quotations are from
`C:\ONS HHS\06222026\Solicitation7571MN26Q00038 for TEFCA ARC_06162026.docx`,
paragraph numbers as extracted from that file. Nothing in this matrix is drawn
from an architecture document; where earlier internal documents disagree with
the solicitation, the solicitation governs.

---

## 1. The deliverable set

Ten deliverables across six tasks. This is the complete list; the solicitation
names no others.

| ID | Deliverable | Task | ¶ |
| --- | --- | --- | --- |
| D1 | Agreed upon Meeting Schedule | 1 — Administrative | 118 |
| D2 | COR reviewed and accepted Review Methodology and Control Framework protocol | 2 | 126 |
| D3.1 | Weekly Progress Reports | 3 — Retrospective Review | 138 |
| D3.2 | Final Report | 3 | 139 |
| D4.1 | Bi-Weekly Progress Reports | 4 — Ongoing Review | 144 |
| D4.2 | Quarterly Reports | 4 | 144 |
| D5.1 | Status Reports | 5 — Priority Reviews | 149 |
| D5.2 | Quarterly Reports | 5 | 150 |
| D6.1 | Contract Closeout Report | 6 | 154 |
| D6.2 | Closeout Educational Presentation | 6 | 155 |

---

## 2. The stratification is the Government's, not AGT's

This matters more than any other single fact in the matrix, because getting it
backwards in either direction is a contract problem.

The four categories appear **verbatim in the solicitation**, three times — ¶136
(Task 3 weekly), ¶137 (Task 3 final) and ¶142 (Task 4 bi-weekly):

> "…a stratified list of Participants and Subparticipants: 1) no discrepancies
> identified 2) minor or administrative discrepancies; 3) inexplicable
> discrepancies; and 4) non-compliant discrepancies."

**CONTRACT REQUIREMENT.** The four categories are Government-defined and are
mandatory content of D3.1, D3.2 and D4.1.

**What is AGT's:** the shorthand labels **B1–B4**, and the rules that decide
which category a given entity falls into. The category *names* are the
Government's; the *mapping logic* is AGT methodology submitted under D2 (¶124
requires AGT to "establish a discrepancy taxonomy", so the Government asked for
it — it just has not accepted a specific one yet).

**Therefore:** a report may present the four categories as required contract
content. A report may **not** describe "B1–B4" as an ONC, ASTP, RCE, Sequoia or
TEFCA classification. In this codebase the categories are
`app/Tefca/reporting.py:22` — `no_discrepancy`, `minor_administrative`,
`inexplicable`, `non_compliant`.

---

## 3. The matrix

### D1 — Agreed upon Meeting Schedule

| | |
| --- | --- |
| **Contractual task** | Task 1, ¶110–118 |
| **Recipient** | COR |
| **Trigger** | Kick-off meeting, within 5 business days of award |
| **Frequency** | Once |
| **Required content** | Weekly 60-minute meetings for the first 90 days; bi-weekly 30-minute thereafter (¶116–117) |
| **QA requirement** | None stated |
| **Format required** | None stated |
| **Repository implementation** | **Not a report-engine deliverable.** Administrative. |
| **Gap** | None. Kick-off held; deck dated 29 Jun 2026. |

---

### D2 — Review Methodology and Control Framework protocol

| | |
| --- | --- |
| **Contractual task** | Task 2, ¶119–126 |
| **Recipient** | COR |
| **Trigger** | Award |
| **Due timing** | **Within two weeks of contract award** (¶120) |
| **Required content** | Alignment to Common Agreement, QTF and SOPs (¶121); approaches for evaluating accuracy of QHIN submissions (¶122); methodologies for stratifying and prioritising entities for review (¶123); **a discrepancy taxonomy** (¶124); **sampling methodology and confidence interval calculations** (¶125) |
| **QA requirement** | **COR review and acceptance is part of the deliverable itself** — the deliverable is defined as the "COR reviewed and accepted" protocol (¶126) |
| **Format required** | None stated. Resubmitted in Word at ONC's direction — **AGT IMPLEMENTATION responding to COR direction**, not a standing format requirement |
| **Approval condition** | Written COR acceptance |
| **Repository implementation** | `docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md` |
| **Status** | Submitted 9 Jul 2026; resubmitted in Word 27 Jul 2026 |
| **Gap** | **Awaiting written COR acceptance.** Not an AGT gap. Until it arrives, every downstream report rests on an unaccepted methodology, which is why the methodology release gate exists. |

---

### D3.1 — Weekly Progress Reports (Retrospective Review)

| | |
| --- | --- |
| **Contractual task** | Task 3, ¶136, ¶138 |
| **Recipient** | COR |
| **Trigger** | Task 3 in progress |
| **Frequency** | **Weekly** |
| **Due timing** | Not specified beyond "weekly" |
| **Required content** | Stratified list of Participants and Subparticipants across the **four Government categories**; **as needed**, suggested changes to the Task 2 methodology or control framework |
| **QA requirement** | None stated in the contract. **AGT METHODOLOGY** adds independent QA before any determination is reported. |
| **Format required** | **None.** ICT accessibility applies (§4). |
| **Approval condition** | See §6 — reportability gate |
| **Repository implementation** | `app/Tefca/reporting.py::generate_weekly_report` → `POST /api/tefca/reports/weekly` (**legacy path**) |
| **Gap** | **Report family lives on the legacy path, not the canonical one.** See §5. |

---

### D3.2 — Final Report (Retrospective Review)

| | |
| --- | --- |
| **Contractual task** | Task 3, ¶137, ¶139 |
| **Recipient** | COR |
| **Trigger** | Completion of the retrospective review |
| **Due timing** | **Within thirty (30) days following completion of the retrospective review** (¶137) |
| **Required content** | Aggregated data over the **120-day** period; the four-category stratified list; **all suggested and implemented changes to the methodology and control framework** |
| **Underlying review requirement** | A statistically representative sample **at or above 95% confidence** of Participants and Subparticipants **from each QHIN** (¶128); sample size determined by the confidence level (¶134) |
| **Sampling parameters** | **AGT METHODOLOGY** (D2 §5.1): population 94,231 connections, 95% confidence, ±5%, **383 entities**, stratified random across 11 QHINs with finite population correction. The **95% confidence floor is the CONTRACT REQUIREMENT**; the ±5% margin and the resulting 383 are AGT's proposal awaiting COR confirmation. |
| **QA requirement** | AGT METHODOLOGY — analyst determination plus independent QA |
| **Format required** | None |
| **Repository implementation** | `app/Tefca/reporting.py::generate_final_report` → `POST /api/tefca/reports/final`; population figures via `app/reports/data/arc_population_report.py` |
| **Gap** | Legacy path (§5). **Per-QHIN sample draw is not implemented** — `CODE_CHANGE_REQUIRED`, awaiting approved sampling parameters. Drawing a sample before the COR confirms the parameters would produce an unusable sample. |

---

### D4.1 — Bi-Weekly Progress Reports (Ongoing Review)

| | |
| --- | --- |
| **Contractual task** | Task 4, ¶140, ¶142, ¶144 |
| **Recipient** | COR |
| **Frequency** | **Bi-weekly (every two weeks)** |
| **Due timing** | "Within thirty (30) days of each period of performance, on a bi-weekly basis" (¶140) |
| **Scope** | **New submissions from each QHIN** — statistically representative sample at or above 95% confidence. Per Q&A Q2/Q8, Task 4 covers **new entrants only**, not changes to existing entities. |
| **Required content** | Four-category stratified list; **all suggested and implemented changes** to methodology and control framework |
| **Format required** | None |
| **Repository implementation** | `app/Tefca/reporting.py::generate_biweekly_report` → `POST /api/tefca/reports/biweekly`; net-new detection via `get_new_submissions` / `get_last_biweekly_date` |
| **Gap** | Legacy path (§5). Cannot run until entity data is delivered. |

---

### D4.2 — Quarterly Reports (Ongoing Review)

| | |
| --- | --- |
| **Contractual task** | Task 4, ¶143, ¶144 |
| **Frequency** | **Quarterly** |
| **Required content** | "Aggregated data synthesized into a succinct report that provides an overview of the activities of the previous ninety (90) days" (¶143) |
| **Format required** | None |
| **Repository implementation** | `app/Tefca/reporting.py::generate_quarterly_report` → `POST /api/tefca/reports/quarterly` |
| **Gap** | Legacy path (§5). Scheduled generation is not automated — **AGT IMPLEMENTATION gap**, listed as `CODE_CHANGE_REQUIRED`. |

---

### D5.1 — Status Reports (Priority Reviews)

| | |
| --- | --- |
| **Contractual task** | Task 5, ¶146, ¶147, ¶149 |
| **Recipient** | COR |
| **Trigger** | **At the direction of the COR** (¶147). Entities and the deadline are named by the COR (¶146). |
| **Frequency** | Anticipated average **20 reviews per month**, with capability to exceed |
| **Due timing** | **"within the agreed upon deadline"** — set per request by the COR (¶146). There is **no fixed contractual turnaround**; the deadline is per-request. |
| **Required content** | The **identified issue**; **root cause if determined**; the **severity or impact**; **recommendations to prevent reoccurrence**; and **resolution**. Plus all suggested and implemented changes to methodology and control framework (¶147). |
| **QA requirement** | AGT METHODOLOGY |
| **Format required** | None |
| **Repository implementation** | `app/Tefca/reporting.py::generate_priority_status_report` → `GET /api/tefca/priority/{case_id}/report` |
| **Gap** | Legacy path (§5). **Turnaround measurement:** see §7 — the machinery must measure against a **per-request COR deadline**, not a fixed SLA constant. |

---

### D5.2 — Quarterly Reports (Priority Reviews)

| | |
| --- | --- |
| **Contractual task** | Task 5, ¶148, ¶150 |
| **Frequency** | Quarterly |
| **Required content** | Aggregated 90-day overview |
| **Repository implementation** | `generate_priority_quarterly_report` → `POST /api/tefca/priority/quarterly-report` |
| **Gap** | Legacy path (§5); scheduling not automated. |

---

### D6.1 — Contract Closeout Report

| | |
| --- | --- |
| **Contractual task** | Task 6, ¶151, ¶152, ¶154 |
| **Due timing** | **Within 90 days of contract expiration** |
| **Required content** | "A complete report of methodologies and framework **as well as all tools developed** as a result of this contract, **including files and data produced**" (¶152) |
| **Delivery** | "electronically deliver these documents to the COR **in an orderly manner ensuring accuracy and completeness**" |
| **Rights** | "The government obtains **unlimited rights** to the methodologies created and any adaptations of pre-existing methodologies and deliverables created under this contract." |
| **Format required** | None stated beyond "electronically" |
| **Repository implementation** | `docs/deliverables/templates/08_Closeout_Report.md` (template only) |
| **Gap** | **Framework only, correctly.** Populating closeout findings before the work exists would be fabrication. The unlimited-rights clause is a records-management obligation on the whole toolchain, not only the report — flagged for the retention decision in §8. |

---

### D6.2 — Closeout Educational Presentation

| | |
| --- | --- |
| **Contractual task** | Task 6, ¶153, ¶155 |
| **Required content** | Communicate components of the closeout report and all materials provided |
| **Format required** | A *presentation* is required by name. **This is the only deliverable whose medium the contract fixes**, and it does not name a file format. |
| **Repository implementation** | None |
| **Gap** | Not due until closeout. No action this phase. |

---

## 4. Format scope — resolved (Step 2)

**Question:** does the contract require DOCX, PDF, HTML, Excel/CSV, or a
presentation format?

**Answer, from the solicitation:** **no file format is specified for any
deliverable.** The word "format" appears only in the accessibility clauses. What
the contract requires instead is *accessibility of whatever format is used*:

> ¶786 — "Items delivered as electronic content must be accessible to HHS
> acceptance criteria. Checklist for various formats are available at
> http://508.hhs.gov/. Materials, other than items incidental to contract
> management, that are final items for delivery **should be accompanied by the
> appropriate checklist**."

> ¶291 — "**Prior to acceptance of deliverables, the contractor must demonstrate
> conformance** to the HHS digital accessibility conformance standards. The
> government reserves the right to perform testing…"

| Format | Contractually required? | Position |
| --- | --- | --- |
| **HTML** | No | **AGT IMPLEMENTATION** — the canonical render. Justified: it is the format the accessibility machinery can actually verify. |
| **PDF** | No | **AGT IMPLEMENTATION** — operationally justified; it is what a COR forwards and archives. §7 records its real accessibility limits. |
| **CSV / Excel** | No | **AGT IMPLEMENTATION** — supports the "stratified list" content requirement. |
| **DOCX** | **No** | Implemented already at `GET /api/tefca/reports/{report_id}/docx`. D2 was resubmitted in Word **at ONC's direction for that document**, which is COR direction on one deliverable, **not** a standing format requirement. |
| **Presentation** | **Yes, for D6.2 only** — by medium, not file format | Not due until closeout. |

**Conclusion on DOCX (Step 15).** DOCX is **not** a contract requirement. It
already exists on the legacy path, so no engineering effort is warranted to
build it out further. The open question is narrower than "should we support
DOCX": it is **which format the COR wants each recurring deliverable in**, given
that D2 was requested in Word.

> **PROGRAM GUIDANCE REQUESTED — F1.** For the recurring deliverables (D3.1,
> D3.2, D4.1, D4.2, D5.1, D5.2), which delivery format does the COR require, and
> should each be accompanied by the corresponding HHS 508 checklist per ¶786?
> AGT's position: HTML and PDF, with the HHS PDF checklist attached. No format
> work will be done beyond what exists until this is answered.

---

## 5. The consolidation problem (Step 3 inventory, Step 4 target)

The inventory found **four** live report API families and **five** generator
implementations. This is the single largest structural finding of Phase 7.

### Live API surfaces

| Path | Module | Endpoints | Classification |
| --- | --- | --- | --- |
| `/api/reports/*` | `app/reports/routes.py` | 7 | **CANONICAL CANDIDATE** |
| `/api/tefca/reports/*` | `app/Tefca/routes.py` | 11 | **ACTIVE — holds the SOW report families** |
| `/api/tefca/arc/reports/*` | `app/tefca_registry/review_routes.py` | 5 | **DUPLICATE** |
| `/api/v1/tefca/reports/*` | `app/Tefca/routes.py` (v1 router) | 3 | **LEGACY** |

### Generators

| Module | Produces | Classification |
| --- | --- | --- |
| `app/reports/generator.py` | verification, verification_brief, executive, data_quality, intake | **CANONICAL CANDIDATE** — has snapshots, provenance, accessibility validation, canonical evidence selector |
| `app/Tefca/reporting.py` | **weekly, final, bi-weekly, quarterly, priority status, priority quarterly** | **ACTIVE — the only implementation of the SOW deliverable families** |
| `app/Tefca/report_renderer.py` | PDF/DOCX rendering for the above | ACTIVE |
| `app/tefca_registry/report_generator.py` | registry-side reports | **DUPLICATE** |
| `app/tefca_registry/report_excel.py` | Excel export | DEVELOPMENT ONLY |

### The finding

**The canonical path and the contract-aligned path are not the same path.**

`app/reports/*` has the machinery that matters for a Government deliverable —
immutable snapshots, real source provenance, the canonical current-evidence
selector, accessibility validation, a content hash. But its report *types* are
engineering artefacts (verification, data_quality, intake). None of them is a
contract deliverable.

`app/Tefca/reporting.py` has the actual contract deliverables — the weekly,
final, bi-weekly, quarterly and priority reports, correctly built on the four
Government categories — but it does **not** go through the Report Data Service,
does **not** produce a snapshot, and does **not** use the canonical evidence
version selector.

So consolidation is **not** "deprecate the legacy path". It is: **move the SOW
report families onto the canonical machinery.** Deprecating `app/Tefca/reporting.py`
before that migration would delete the only implementation of the contract's
deliverables.

**Nothing has been deleted or deprecated in this phase.** Per Step 22, legacy
paths stay until equivalence is proven against identical development fixtures,
and per the approved B4 plan, preference is archive over destructive removal.

> **AGT IMPLEMENTATION — carried forward.** The SOW-family migration onto the
> canonical service is scoped but **not executed in Phase 7**, because the
> migration must be equivalence-tested against real deliverable output, and
> there is no Government data to test the deliverables against. Doing it against
> development fixtures alone would prove only that two code paths agree, not
> that the deliverable is right.

---

## 6. Reportability — what makes a result deliverable

**CONTRACT REQUIREMENT.** The contract does not describe an internal review
workflow, so the workflow is AGT methodology. What the contract *does* fix is
that D2 is defined as the **"COR reviewed and accepted"** protocol (¶126), and
¶291 requires conformance to be **demonstrated prior to acceptance of
deliverables**. Both point the same way: nothing is a deliverable result until a
human outside the machine has said so.

The enforced separation, in order:

| Boundary | Enforced where |
| --- | --- |
| An observation is **not** an analyst determination | `app/Tefca/exception_triage.py` — triage sorts; it never determines. `NEVER_AUTOMATIC = {FAIL}` |
| An analyst determination is **not** a QA approval | `app/tefca_registry/qa_gate.py` — segregation of duties; analyst ≠ QA reviewer |
| A QA approval is **not** COR acceptance | `docs/OFFICIAL_FINDING_RELEASE_GATE.md` — 12 conditions, 6 outside AGT's control |

**Current state, verified against the live development database:**

- `review_records`: **43**, of which **reportable: 0**
- `review_decision_events`: **0**
- Automatic PASS/FAIL at either Phase-6 evidence version: **0**

The 43 historical development review records **have not been made reportable**
and must not be. They predate the decision-event architecture; retroactively
declaring them approved would fabricate the exact human judgement the gate
exists to require.

---

## 7. Accessibility (Step 13) and turnaround (Step 20) — honest positions

**Accessibility.** ¶291 and ¶786 make conformance an acceptance condition. What
can be demonstrated today:

| Output | Position |
| --- | --- |
| **HTML** | Semantic headings, `<caption>` and `<th scope>` on every table, alt text enforced by the chart engine (a chart with empty alt text is refused), meaning never carried by colour alone — status indicators are colour **+ shape + text**. Automated checks run at generation. |
| **PDF** | WeasyPrint, asked for `pdf_variant="pdf/ua-1"`, which emits a **tagged structure tree**. A tagged tree is a **precondition** for an accessible PDF, **not proof of one**, and the engine says so in its own metadata. If the variant is rejected it emits an untagged PDF and logs that loudly rather than reporting a false pass. **Full PDF/UA conformance has not been independently validated.** Using USWDS styling in the HTML does not make the PDF conformant, and no such claim is made. **Not testable on the Windows development host** — WeasyPrint's Pango/Cairo/GObject libraries are present only in the Linux container image, so 2 PDF tests skip locally. |
| **DOCX** | Not assessed. Not contractually required. |

> **Remediation needed.** A tagged-PDF path, or COR agreement that HTML is the
> conformant delivery format with PDF as a convenience copy. Tracked as an open
> item, not as a solved one.

**Priority-review turnaround.** ¶146 sets the deadline **per request, by the
COR** — there is no fixed contractual SLA. The machinery therefore measures
elapsed time against a supplied deadline; it must not assert compliance with a
turnaround target the contract never set. Timing computed from synthetic
development timestamps proves the **calculation**, and is not a performance
result.

---

## 8. Open items

### Program guidance requested

| ID | Question |
| --- | --- |
| **F1** | Delivery format for each recurring deliverable, and whether the HHS 508 checklist must accompany each (¶786). |
| **F2** | Cycle labelling for recurring reports. Until answered, cycles are `DEV-CYCLE-<evidence version>-<source hash>` — deterministic and unmistakably not a contract label. |
| **F3** | Records retention period for finalised reports and the underlying evidence, noting the ¶152 unlimited-rights clause. Durable storage is verified; **WORM retention is deliberately not locked** until the period is approved. |

### Blocked on the Government

Assignment; entity CSV delivery via Box; written COR acceptance of D2;
confirmation of the D2 §5.1 sampling parameters; the four open methodology
decisions (D1–D9 register).

### AGT implementation, carried forward

Migration of the SOW report families onto the canonical service; per-QHIN
sample draw; scheduled generation of D4.2 and D5.2; contract-number citation in
report headers.

---

## 9. What this matrix does not do

It does not turn an AGT preference into an ONC requirement. Where the
solicitation is silent — every file format except D6.2's medium — it is recorded
as silent, and AGT's choice is labelled as AGT's. Where the solicitation speaks
— the four discrepancy categories, the 95% confidence floor, the 30-day and
90-day clocks, the per-request priority deadline, the accessibility acceptance
condition — it is quoted.
