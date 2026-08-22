# DOCUACTION TEFCA ARC — METHODOLOGY DECISION BRIEF

**Version 2** · **Date:** 2026-08-22 · **Contract:** 7571MN26F80064 (HHS/ONC ASTP)
**Prepared for:** COR / Program Methodology Authority · **Prepared by:** Alliance Global Tech, Inc.
*Supersedes v1 of 2026-08-22 (`docs/cor_decision_brief.md`), retained as the v1 record.*

---

## PURPOSE

During verification of **43 TEFCA entities** from the ONC/RCE delivery of 2026-07-20 (23,566 records), **seven methodology questions** were identified that require program-level decisions before DocuAction can establish an authoritative classification path. Two further **contract/operational clarifications** are requested at the end.

These questions concern methodology and policy rather than the engineering defects already identified through stabilization. The remaining issue in each case is how an observed evidence state should be interpreted under the approved ARC methodology.

**How statements in this brief are labelled:**

- **TEFCA/ONC REQUIREMENT** — cited to its source.
- **AGT RECOMMENDATION** — offered only where the contract or approved methodology supports it.
- **PROGRAM GUIDANCE REQUESTED** — where the methodology available to AGT is silent.

The approved methodology in AGT's possession (TEFCA Requirements Document V2, Appendix C.4) provides a **structural** classification tree and states that the precise B1–B4 thresholds *"are governed by the COR-approved methodology (Deliverable 2)"*, marking each **VERIFICATION REQUIRED**. Deliverable 2 is not in AGT's possession.

**Each decision is independently answerable** and may be returned separately.

| # | Decision | Entities affected in this sample |
|---|---|---|
| D1 | Uncorroborated NPI — how classified? | 0 observed; 20 adjacent cases at risk |
| D2 | No rule matches — what result? | 0 currently; path reachable and undefined |
| D3 | B3 — Reviewer or Senior Analyst? | 20 of 43 |
| D4 | Source unavailable — classification or readiness? | 43 of 43 |
| D5 | Which name differences are reportable? | 22 of 43 |
| D6 | "Flagged" vs "invalid" identifier | 0 currently; latent |
| D7 | Potential exclusion match — automated finding? | 0 currently; gates automated non-compliance findings |

---

### DECISION D1: UNCORROBORATED NPI

**DECISION:** When an entity for which an NPI is expected provides an NPI that cannot be corroborated in NPPES, how should ARC classify the result?

**WHY NEEDED:** CMS requires HIPAA-covered providers to obtain an NPI, but NPPES states that **issuance of an NPI does not validate licensing or credentialing**. Failure to corroborate the supplied NPI in NPPES is an identity-verification observation; the ARC methodology must determine whether and under what circumstances that observation affects classification. This applies **only** where an NPI was expected and supplied — not where none was supplied, which is legitimate for payers, public health agencies and health information networks (19.45% of the delivered population carries no NPI).

**REAL EXAMPLE:** No entity of the 43 presents this case — all 23 that supplied an NPI were corroborated; the other 20 supplied none.

**AGT RECOMMENDATION (related observation):** DocuAction presently records "no NPI supplied" and "NPI supplied, not corroborated" identically at the point of classification, so the two cannot be distinguished downstream. AGT recommends separating them regardless of how D1 is answered.

**OPTIONS:**
- **A. Analyst investigation** — treated as an observation warranting review.
- **B. Non-compliant** — treated as a material verification failure where the methodology requires a verifiable NPI.
- **C. Conditional** — treatment depends on Medicare relevance, or on the reason for non-corroboration.

**OPERATIONAL IMPACT:** A routes the entity to human review. B produces a reportable finding that may trigger QHIN notification. C requires a rule per condition.

**RECOMMENDED OPTION:** Program guidance requested — the current ARC methodology available to AGT does not explicitly address this question.

**COR DECISION:** __________________

---

### DECISION D2: NO CLASSIFICATION RULE MATCHES

**DECISION:** When evidence gathering completes successfully but no B1–B4 rule matches, what should DocuAction record?

**WHY NEEDED:** This is not a source failing to answer — every applicable source answered, and the rules do not describe the combination observed. DocuAction must record something, and the available answers differ materially.

**REAL EXAMPLE:** CHIA GRANDA MD LLC previously reached this path. Following a stabilization correction to how name evidence reaches classification, an existing rule now matches it. No entity currently takes this path, but it remains reachable and its outcome is undefined.

**OPTIONS:**
- **A. No discrepancy (B1)** — no discrepancy rule was triggered.
- **B. Manual examination (B3)** — the rules not describing a case is itself grounds for review.
- **C. A separate UNDETERMINED / INSUFFICIENT_EVIDENCE state** outside B1–B4 until additional evidence or analyst review resolves it.

**Constraint applying to all three:** "no match was observed" is a distinct evidence state from "the source could not be reached", and neither is evidence of compliance.

**OPERATIONAL IMPACT:** A closes the entity with no human step. B adds a review item. C requires a reporting category outside B1–B4 and a rule for counting it in deliverables.

**RECOMMENDED OPTION:** Program guidance requested — the methodology available to AGT does not address this. Option A would treat the absence of a triggered discrepancy rule as sufficient for B1; the current methodology available to AGT does not establish whether that inference is intended.

**COR DECISION:** __________________

---

### DECISION D3: B3 REVIEW TIER

**DECISION:** Should entities classified B3 (Inexplicable) route to Tier 2 (Reviewer) or Tier 3 (Senior Analyst)?

**WHY NEEDED:** **TEFCA/ONC REQUIREMENT** — the approved methodology requires three-tier routing and a Tier-3 queue restricted to senior analysts and above (Requirements Document V2, FR-T3-018 and FR-T3-020). It does **not** state which bucket routes to which tier.

**REAL EXAMPLE:** All 20 current B3 entities — including Kauai Medical Clinic, CHILDRENS CLINIC and HAWAII COALITION FOR HEALTH — are small provider organisations that supplied no NPI, so identity could not be corroborated and Medicare enrolment could neither be established nor refuted. None carries an adverse finding. The common feature is an absent identifier.

**OPTIONS:**
- **A. Tier 2 (Reviewer)** — B3 reflects incomplete corroboration rather than suspected violation.
- **B. Tier 3 (Senior Analyst)** — the rules could not account for the evidence, warranting senior judgement.

**OPERATIONAL IMPACT:** Determines who works 47% of the current sample, at which privilege level. Option B places 20 of 43 in the senior escalation queue. The review queues cannot be placed in service until this is answered.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this. AGT notes it directly determines analyst staffing.

**COR DECISION:** __________________

---

### DECISION D4: SOURCE UNAVAILABLE

**DECISION:** When an applicable authoritative source is temporarily unavailable, does that affect the B1–B4 classification, or only review readiness?

**WHY NEEDED:** A source that did not answer has said nothing about the entity. An unavailable source is a distinct evidence state from a source that answered and found no match; DocuAction keeps these separate today and that is not in question. What is undecided is whether an unanswered source should change the classification, or only whether the review can close.

**REAL EXAMPLE:** During the current verification run, DocuAction was unable to obtain SAM.gov exclusion responses for all 43 sampled entities. Other applicable exclusion sources (OIG LEIE, CMS Revoked) continued to return results. AGT is separately investigating the SAM.gov integration and current API service requirements. Regardless of the technical cause, the methodology question remains: when an applicable authoritative source is temporarily unavailable, how should ARC treat the incomplete evidence state?

**OPTIONS:**
- **A. Classification proceeds** on the evidence obtained, with the unavailability recorded per dimension and not counted against the entity.
- **B. Route to manual review** regardless of other evidence.
- **C. Remain UNDETERMINED** until the source is available and the check can be completed.
- **D. Source-specific rules** — some sources required, others corroborative.

**OPERATIONAL IMPACT:** A allows deliverables to proceed with a disclosed evidence gap. B and C hold affected entities open until the source responds, with the duration dependent on source availability.

**A related question the program may wish to answer together with this one:** may an ARC determination be issued while an applicable exclusion source remains unavailable, and if so, how should the gap be disclosed in the deliverable?

**RECOMMENDED OPTION:** Program guidance requested — the methodology available to AGT does not address this.

**COR DECISION:** __________________

---

### DECISION D5: NAME DISCREPANCY SEVERITY

**DECISION:** Which differences between an RCE-provided organisation name and the NPPES legal name constitute a reportable discrepancy?

**WHY NEEDED:** DocuAction correctly detects name differences. The question is which differences constitute a discrepancy under the ARC methodology. Organisations routinely trade under a name differing from their registered legal name, so treating every difference as a discrepancy generates findings against entities that have done nothing wrong — while treating none as a discrepancy misses genuine identity questions. **22 of 43 entities have an observed name difference but are currently recorded without a name-based discrepancy, because the methodology has not established which differences are reportable.**

**REAL EXAMPLES** — *the five-state scale below is an* **AGT RECOMMENDATION**, *not an ONC requirement:*

| Category | Real example from this sample |
|---|---|
| **NAME_EXACT** — identical | 21 of 43 entities |
| **NAME_NORMALIZED_EQUIVALENT** — case, punctuation, whitespace, embedded formatting | *MALAMA KINO PRIMARY CARE INC* / *MALAMA KINO PRIMARY CARE INC.* |
| **NAME_MINOR_VARIATION** — corporate suffix or abbreviation | *PACIFIC PULMONARY CONSULTANTS* / *PACIFIC PULMONARY CONSULTANTS LLC*; *Buffalo Medical Group* / *BUFFALO MEDICAL GROUP, P.C.* |
| **NAME_MATERIAL_MISMATCH** — substantively different identity | *Hawaii Pacific Health* / *KAPIOLANI MEDICAL SPECIALISTS*; *KUHIO MEDICAL CENTER* / *HAWAII FAMILY MEDICAL CENTERS* |
| **NAME_AMBIGUOUS** — cannot be determined without investigation | *UTMB - Health* / *THE UNIVERSITY OF TEXAS MEDICAL BRANCH* |

**OPTIONS:**
- **A. No name difference is reportable.**
- **B. Only NAME_MATERIAL_MISMATCH and NAME_AMBIGUOUS are reportable.**
- **C. All differences beyond NAME_EXACT are reportable.**
- **D. A different grading than the scale above.**

**OPERATIONAL IMPACT:** The selected threshold will determine how many of the 22 observed name differences require review or become reportable. AGT will quantify that impact after the program approves the grading methodology. The scale itself also requires approval.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this. **AGT RECOMMENDATION:** that a graduated scale be adopted in some form, because the present binary treatment cannot distinguish a punctuation difference from two entirely different organisation names.

**COR DECISION:** __________________

---

### DECISION D6: IDENTIFIER QUALITY STATES

**DECISION:** What is the approved distinction between an identifier that is *flagged* and one that is *invalid*, and what classification consequence, if any, attaches to each state?

**WHY NEEDED:** The classification rules use both words, and neither is defined in the methodology available to AGT, so DocuAction cannot determine which identifier defects belong to which state. NPI defects vary in kind — wrong length, non-numeric characters, and a structurally well-formed NPI that fails its check digit are different situations. The delivered population contains 4 malformed NPIs and 2 check-digit failures.

**The architecture keeps identifier quality separate from classification consequence:**

```
      Identifier quality
              |
   VALID  /  FLAGGED  /  INVALID
              |
   ARC methodology determines consequence
              |
      B1  /  B2  /  B3  /  B4
```

Identifier quality is an observation. What it means for classification is a program decision, and this brief does not presume any mapping.

**REAL EXAMPLE:** No entity among the 43 carries an NPI defect. The question is latent and will arise on the next sample drawn from the full 23,566-record population.

**OPTIONS:**
- **A. Two-level model** — FLAGGED means the identifier requires further validation; INVALID means the identifier definitively fails an approved structural or validation rule. The program separately determines the classification consequence of each state.
- **B. Single level** — the two terms describe the same state, and one rule set should be aligned to the other.

**OPERATIONAL IMPACT:** Option A requires the program to specify which defects are definitive and what consequence attaches to each state. Option B requires a single consequence for any identifier defect.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this.

**COR DECISION:** __________________

---

### DECISION D7: EXCLUSION EVIDENCE AND CLASSIFICATION

**DECISION:** When exclusion screening produces a match, does match quality affect whether classification may proceed without human identity confirmation?

**WHY NEEDED:** Exclusion screening produces two materially different results:

- **CONFIRMED match** — exact match on a unique identifier (NPI or EIN) against OIG LEIE or SAM.gov. Identity is established by the identifier.
- **CANDIDATE / name-only match** — match on organisation or individual name where no unique identifier was available.

**Federal exclusion authorities (SAM.gov and HHS-OIG) both advise users to verify identity when name-only matches are found, as individuals or firms may share the same or similar names.**

DocuAction currently records both kinds as requiring analyst confirmation of identity before any determination. The classification rules treat an exclusion as disqualifying regardless of match quality. The two positions produce different outcomes and the methodology does not state which governs.

The architecture principle at issue is the sequence **source observation → match quality → applicability → evidence → verification → human review where required.** The decision is where in that sequence classification may proceed, and for which match qualities.

**REAL EXAMPLE:** No entity among the 43 produced an exclusion match of either kind. OIG LEIE returned no exclusion for the 23 entities it could screen by NPI; SAM.gov responses were unavailable for all 43 (see D4).

**OPTIONS:**
- **A.** Confirmed unique-identifier match (NPI or EIN) may support an automated finding; candidate or name-only match requires human identity confirmation before classification.
- **B.** All exclusion matches require analyst confirmation before classification, regardless of match quality.
- **C.** Program-defined source and match-quality matrix determines treatment per source.

**OPERATIONAL IMPACT:** Option A permits automated findings on identifier matches while routing name-only matches to review. Option B introduces a mandatory human step before any exclusion finding, with an associated turnaround time. Option C requires the program to specify the matrix.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this.

**COR DECISION:** __________________

---

## ADDITIONAL PROGRAM CLARIFICATIONS

*These are contract/operational questions rather than ARC methodology decisions.*

### CLARIFICATION D8 — RECORDS RETENTION

Please identify the applicable records schedule and disposition authority for:

- Original RCE source deliveries
- Verification evidence
- Analyst and QA determinations
- Final ARC reports

AGT will not configure irreversible immutable-storage retention policies until the applicable retention requirement is established. If an approved HHS/NARA records schedule applies, please identify it. If no schedule currently applies, please direct AGT to the responsible records-management official.

**COR RESPONSE:** __________________

### CLARIFICATION D9 — OFFICIAL DELIVERABLE FORMAT

Please confirm whether the required official ARC report format is:

- **A.** PDF only
- **B.** PDF + DOCX
- **C.** Other specified format

If the contract, approved Deliverable 2 requirements, or CDRL already establishes this requirement, please identify that requirement rather than issuing new direction.

**COR RESPONSE:** __________________

---

## AFTER THESE DECISIONS

AGT will implement the approved methodology as a **new versioned rule set**. Determinations already recorded will be preserved exactly as issued and remain explainable; any reclassification will be recorded as a new review, never as an edit to an existing record. Until these decisions are received, AGT will not designate an authoritative classification path.

*Supporting engineering detail is retained in `docs/methodology_decision_package.md` — an engineering appendix, not part of this submission.*
