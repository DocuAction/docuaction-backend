# Deliverable Templates — for COR Review

**TEFCA ARC · Contract 7571MN26F80064 · Alliance Global Tech, Inc.**
2026-08-24

---

> ## SAMPLE / DEVELOPMENT DATA
> ## FOR METHODOLOGY REVIEW ONLY
> ## NOT AN ONC FINDING
>
> Every template and every rendered example referenced here contains
> development or placeholder values. The Government population has not been
> received. **Entities reviewed: 0 of 383.** These exist so the COR can assess
> structure, content and level of detail — not numbers.

---

## What the COR is asked

For each template:

1. **Is this the information you expect?**
2. **Is the level of detail correct** — too much, too little, or right?
3. **Is the format acceptable?**
4. **What should be added or removed?**

Answers to these are more valuable than approval. A template corrected now costs
nothing; the same correction after the first delivery costs a redelivery.

---

## Templates

| # | Deliverable | Template | Contract requirement it satisfies |
| --- | --- | --- | --- |
| 1 | **Methodology** | `../deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md` | D2 — the protocol itself |
| 2 | **Retrospective status** | `../deliverables/templates/02_Retrospective_Review.md` (weekly form) | D3.1 — weekly progress, four-category stratified list |
| 3 | **Retrospective final** | `../deliverables/templates/02_Retrospective_Review.md` (final form) | D3.2 — aggregated 120-day data, all suggested and implemented methodology changes |
| 4 | **Ongoing review** | `../deliverables/templates/03_Ongoing_Review.md` | D4.1 — bi-weekly progress on new submissions |
| 5 | **Quarterly** | `../deliverables/templates/09_Executive_Briefing.md` | D4.2 / D5.2 — ninety-day aggregation, synthesised succinctly |
| 6 | **Priority review** | `../deliverables/templates/04_Priority_Review.md` | D5.1 — the five required elements of ¶147 |
| 7 | **Closeout** | `../deliverables/templates/08_Closeout_Report.md` | D6.1 — methodologies, framework, tools, files and data produced |
| 8 | **Presentation** | Outline in `08_Closeout_Report.md` | D6.2 — closeout educational presentation |

Supporting templates, used as appendices rather than delivered alone:

| Template | Purpose |
| --- | --- |
| `05_QA_Review_Checklist.md` | What the QA reviewer verifies before approving |
| `06_Exception_Detail.md` | One entity's evidence, determination and QA trail |
| `07_Evidence_Appendix.md` | The source record behind a reported figure |

---

## Rendered examples

Development renderings produced by the reporting engine are in
`../development_examples/`. Each carries the development classification banner
as the first element on the page, and a provenance table showing the source file
hash, the evidence version, the review cycle and the content hash.

They demonstrate three things worth checking:

- **The banner is unmissable.** A development report that reaches the wrong
  inbox announces itself before anyone reads a number.
- **The provenance table is complete.** Every figure can be traced from it.
- **Nothing claims a finding.** They present observations, the evidence behind
  them, and what remains blocked on methodology.

---

## What every delivered report will carry

Confirmed against the contract:

| Element | Source of the requirement |
| --- | --- |
| **The contract number** | Required on all reports by the delivery schedule |
| The four Government discrepancy categories, in the contract's wording and order | Stated three times in the tasks |
| Suggested methodology changes, as needed | D3.1 |
| All suggested **and implemented** methodology changes | D3.2, D4.1 |
| The reporting period and the population version it covers | Traceability |
| Source limitations affecting the results | Methodology |
| Items awaiting a methodology decision, with counts | Methodology |
| Which methodology version was applied | Traceability |

---

## Points AGT specifically asks the COR to consider

**Level of detail in the weekly report.** D3.1 is weekly, which is frequent. AGT
has drafted it as a short stratified summary with exceptions called out, rather
than a full listing. If the COR wants the complete entity list weekly, that is a
straightforward change — but it is a different document.

**The four categories in every report.** They appear even when a category is
empty, so a zero reads as "none found" rather than "not measured". AGT
recommends keeping empty categories visible.

**Methodology-pending disclosure.** Items that cannot yet be categorised are
counted and shown. AGT recommends keeping this visible in the deliverable rather
than in a footnote: a suppressed open question becomes an embedded assumption.

**Evidence appendices.** Full evidence for every entity would make a weekly
report unwieldy. AGT proposes evidence on request and for exceptions, with the
full record always retained and reproducible.

**Format.** No file format is contractually specified. AGT proposes accessible
HTML with a PDF companion, accompanied by the HHS Section 508 checklist for the
delivered format. See decision **D9**.
