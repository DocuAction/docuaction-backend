# Methodology Decisions Requested

**TEFCA ARC · Contract 7571MN26F80064 · Alliance Global Tech, Inc.**
Prepared for COR decision · 2026-08-24

---

## Why this document exists

The Task 2 protocol requires a discrepancy taxonomy and an approach to
evaluating accuracy. In building both, AGT reached questions the contract does
not answer and that AGT should not answer alone — because each determines
whether a given condition is reported to the Government as a discrepancy.

**None of these has been resolved in software.** Where a decision is open, the
affected condition is counted, disclosed and held. It is not assigned to a
category by default, and it is not suppressed. Both would be AGT deciding a
question that belongs to the Government.

Ten decisions are open. **D4-ADDR is the one with the largest operational
effect** and is presented first.

---

## D4-ADDR — Address difference materiality

| | |
| --- | --- |
| **Decision** | When is a difference between the address in the entity record and an address published by an authoritative source *material* enough to report as a discrepancy? |
| **Why COR input is needed** | The entity record carries a **registered** address. NPPES and PECOS publish **practice locations**. These are different kinds of address and can legitimately differ for a fully compliant organisation. Deciding where the line falls sets the discrepancy rate. |
| **Contract reference** | Task 2 requires AGT to establish a discrepancy taxonomy. Tasks 3 and 4 require entities to be stratified across the four categories. Neither defines address materiality. |
| **AGT recommendation** | Treat a **street-line** difference between a registered address and a published practice location as **informational** — reported, not counted as a discrepancy. Treat a **state or ZIP code** difference as a **minor or administrative discrepancy**, because it affects reachability and jurisdiction. |
| **Alternatives** | (a) Any normalised difference is a discrepancy. (b) No address difference is ever a discrepancy. (c) Only a difference the entity fails to explain on enquiry is a discrepancy. |
| **Operational effect** | **Substantial.** Development-data validation demonstrated that address comparison rules can materially affect review volume — the difference between the strictest and most permissive readings changes the number of entities requiring analyst adjudication by an order of magnitude. AGT therefore requests confirmation of the address materiality methodology **before** applying it to Government-authorised data, so that review capacity and the reported discrepancy rate both rest on an agreed rule. |
| **Default if no decision** | **None is safe.** AGT will not adopt a default. Choosing (a) manufactures a threshold of "any difference at all"; choosing (b) manufactures "never". Affected entities will continue to be reported as awaiting methodology, with counts shown. |
| **COR response** | ☐ AGT recommendation ☐ Alternative (a) ☐ (b) ☐ (c) ☐ Other: ____________ |

---

## D1 — Uncorroborated provider identifier

| | |
| --- | --- |
| **Decision** | How is an entity classified when its identifier is well formed and present, but no authoritative source corroborates the organisation associated with it? |
| **Why COR input is needed** | This is neither a match nor a mismatch. It is an absence of corroboration, and whether that is a discrepancy is a methodology judgement. |
| **Contract reference** | Task 2 discrepancy taxonomy. |
| **AGT recommendation** | Report as **minor or administrative** — a data-quality condition in the submission rather than evidence about the entity. |
| **Alternatives** | (a) Inexplicable. (b) Not a discrepancy; report as informational. |
| **Operational effect** | Determines the category of affected records and whether they require analyst adjudication. |
| **Default if no decision** | None. Held and disclosed. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ (b) ☐ Other: ____________ |

---

## D2 — No classification rule matches

| | |
| --- | --- |
| **Decision** | What is the outcome when an entity's evidence matches no rule in the taxonomy? |
| **Why COR input is needed** | The path is reachable and the outcome is currently undefined. A taxonomy that can be silently exited is not a complete taxonomy. |
| **Contract reference** | Task 2 discrepancy taxonomy. |
| **AGT recommendation** | Route to an analyst for manual classification, and record the gap as a proposed methodology change in the next progress report — which the contract already requires AGT to report. |
| **Alternatives** | (a) Default to "no discrepancies identified". (b) Default to "inexplicable". |
| **Operational effect** | Small in volume; important in principle. A default of (a) would let an unclassifiable entity be reported as clean. |
| **Default if no decision** | AGT will route to an analyst rather than default either way. This is the conservative choice and AGT will apply it in the absence of direction. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ (b) ☐ Other: ____________ |

---

## D3 — Adjudication tier for inexplicable discrepancies

| | |
| --- | --- |
| **Decision** | Should category 3 (inexplicable) determinations be made by a reviewer, or reserved to a senior analyst? |
| **Why COR input is needed** | It is a staffing and control question with a cost implication, not a finding question. |
| **Contract reference** | None directly. Task 2 requires an approach to prioritising review. |
| **AGT recommendation** | Reviewer determination with mandatory independent QA — which already applies to every determination. |
| **Alternatives** | (a) Senior analyst determination required. |
| **Operational effect** | Affects throughput and staffing mix. No effect on what is reported. |
| **Default if no decision** | AGT's recommendation applies; every determination already receives independent QA. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ Other: ____________ |

---

## D4 — Unavailable verification source

| | |
| --- | --- |
| **Decision** | When an applicable source cannot be queried, is that a classification matter or a readiness matter? |
| **Why COR input is needed** | It affects the whole population, not a subset. SAM.gov cannot currently be queried because AGT holds no credential. |
| **Contract reference** | Task 3 permits information from publicly available data, contractor-owned data, COR-provided data and other relevant sources. It does not state what follows when a source is silent. |
| **AGT recommendation** | A **readiness matter**. Record the limitation, disclose the affected count in every report, and place no entity in a category on account of it. An entity is never deficient because a Federal system was unreachable. |
| **Alternatives** | (a) Treat the entity as unclassifiable until the source is available. (b) Complete the review on the remaining sources and note the gap. |
| **Operational effect** | Under AGT's recommendation, reviews proceed and the gap is disclosed. Under (a), a large share of reviews would be held pending SAM.gov access. |
| **Default if no decision** | AGT applies its recommendation: disclose, never infer. This is the only reading that avoids an adverse inference from an outage. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ (b) ☐ Other: ____________ |

---

## D5 — Name difference reportability

| | |
| --- | --- |
| **Decision** | Which differences between the submitted organisation name and an authoritative source's name are reportable? |
| **Why COR input is needed** | Legal names, trade names and abbreviations differ routinely and legitimately. |
| **Contract reference** | Task 2 discrepancy taxonomy. |
| **AGT recommendation** | A difference that survives normalisation of punctuation, legal suffixes and common abbreviations is **minor or administrative**. A name resolving to a demonstrably different organisation is **inexplicable**. |
| **Alternatives** | (a) Any name difference is a discrepancy. (b) Only a different organisation is a discrepancy. |
| **Operational effect** | Moderate. Affects the category of a common condition. |
| **Default if no decision** | None. Held and disclosed. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ (b) ☐ Other: ____________ |

---

## D6 — Malformed versus invalid identifier

| | |
| --- | --- |
| **Decision** | Should an identifier that fails a format check be described as *flagged* or *invalid*? |
| **Why COR input is needed** | The two words carry different weight in a Government report. "Invalid" asserts a conclusion about the identifier; "flagged" reports a condition. |
| **Contract reference** | Task 2 discrepancy taxonomy. |
| **AGT recommendation** | **Flagged**, with the specific defect stated. AGT observes the format; only the issuing authority can declare an identifier invalid. |
| **Alternatives** | (a) Invalid. |
| **Operational effect** | Wording only. No change in volume. |
| **Default if no decision** | AGT applies the conservative wording. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ Other: ____________ |

---

## D7 — Potential exclusion match

| | |
| --- | --- |
| **Decision** | May a name-only match against an exclusion or debarment list become a reported discrepancy without a decisive identifier match? |
| **Why COR input is needed** | This is the highest-consequence question in the taxonomy. A false positive attributes an exclusion to an organisation that does not have one. |
| **Contract reference** | Task 2 discrepancy taxonomy; Task 3 requires thorough, high-quality review. |
| **AGT recommendation** | **No.** A name-only match is routed to an analyst as a potential match and is never reported as non-compliance without a decisive identifier match plus analyst determination and independent QA approval. |
| **Alternatives** | (a) Report name-only matches as potential non-compliance for the COR to adjudicate. |
| **Operational effect** | Under the recommendation, only corroborated matches reach category 4. Under (a), the COR receives a larger set including probable false positives. |
| **Default if no decision** | AGT applies its recommendation. Reporting an uncorroborated exclusion match would be the most damaging error available. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ Other: ____________ |

---

## D8 — Records retention

| | |
| --- | --- |
| **Decision** | How long must evidence, determinations, QA decisions and issued reports be retained, and what happens at the end of that period? |
| **Why COR input is needed** | It is a records-management and Federal records question, and the contract's data-return clause makes the disposition the Government's to direct. |
| **Contract reference** | Return of HHS data clause; Task 6 unlimited-rights clause. |
| **AGT recommendation** | Retain all evidence and issued reports for the life of the contract plus the period the COR specifies, then return or dispose as the Contracting Officer directs. AGT does not propose a specific period. |
| **Alternatives** | A specified number of years; or retention until the Government confirms receipt of the closeout deliverable. |
| **Operational effect** | Governs storage configuration. AGT has deliberately **not** applied any irreversible retention lock, because that decision cannot be reversed once made. |
| **Default if no decision** | Retain everything. Nothing is deleted absent written direction. |
| **COR response** | Period: __________ Disposition: __________ |

---

## D9 — Deliverable format and accessibility checklist

| | |
| --- | --- |
| **Decision** | In what format should each recurring deliverable be provided, and should each be accompanied by the corresponding HHS Section 508 checklist? |
| **Why COR input is needed** | The solicitation specifies no file format for any deliverable. It does require delivered electronic content to meet HHS acceptance criteria, and states that final items for delivery should be accompanied by the appropriate checklist. |
| **Contract reference** | HHS ICT accessibility clauses. |
| **AGT recommendation** | Provide each recurring deliverable as an accessible HTML document with a PDF companion, accompanied by the HHS checklist for the delivered format. The Task 2 protocol was provided in Microsoft Word at ONC's direction; AGT will follow the same direction for other deliverables on request. |
| **Alternatives** | (a) PDF only. (b) Microsoft Word for all deliverables. (c) A format specified per deliverable. |
| **Operational effect** | Determines the accessibility validation AGT performs before each delivery. |
| **Default if no decision** | AGT applies its recommendation and provides the checklist. |
| **COR response** | ☐ Recommendation ☐ (a) ☐ (b) ☐ (c) ☐ Other: ____________ |

---

## Summary

| ID | Topic | Operational effect | Safe default exists |
| --- | --- | --- | --- |
| **D4-ADDR** | Address materiality | **Substantial — affects review volume** | **No** |
| D1 | Uncorroborated identifier | Category of affected records | No |
| D2 | No rule matches | Principle; small volume | Yes — route to analyst |
| D3 | Adjudication tier | Staffing only | Yes |
| D4 | Unavailable source | Whole population | Yes — disclose, never infer |
| D5 | Name differences | Category of a common condition | No |
| D6 | Identifier wording | Wording only | Yes — conservative wording |
| D7 | Exclusion match | **Highest consequence per case** | Yes — require corroboration |
| D8 | Retention | Storage configuration | Yes — retain everything |
| D9 | Format and checklist | Delivery preparation | Yes — HTML plus PDF |

**Four decisions have no safe default: D4-ADDR, D1, D5 and D7's alternative.**
Affected conditions remain held and disclosed until the COR directs otherwise.

AGT will record each decision, its date and its authority, and will state in
every subsequent deliverable which methodology version was applied.
