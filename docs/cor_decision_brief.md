# DOCUACTION TEFCA ARC — METHODOLOGY DECISION BRIEF

**Date:** 2026-08-22 · **Contract:** 7571MN26F80064 (HHS/ONC ASTP)
**Prepared for:** COR / Program Methodology Authority · **Prepared by:** Alliance Global Tech, Inc.

---

## PURPOSE

During verification of **43 TEFCA entities** from the ONC/RCE delivery of 2026-07-20 (23,566 records), **seven methodology questions** were identified that require program-level decisions before DocuAction can establish an authoritative classification path.

**These are methodology and policy decisions, not engineering defects.** DocuAction gathers the evidence correctly in each case; what is undecided is what the evidence should *mean*.

**Sourcing.** Where a decision is governed by approved methodology, this brief cites it. Where it is not, the brief says so and offers no recommendation. No AGT implementation choice is presented here as an ONC requirement. The approved methodology in AGT's possession (TEFCA Requirements Document V2, Appendix C.4) gives a **structural** classification tree and states that the precise B1–B4 thresholds *"are governed by the COR-approved methodology (Deliverable 2)"*, marking each **VERIFICATION REQUIRED**. Deliverable 2 is not in AGT's possession.

**Each decision is independently answerable** and may be returned separately.

| # | Decision | Entities affected today |
|---|---|---|
| D1 | Uncorroborated NPI — how classified? | 0 observed; 20 adjacent cases at risk |
| D2 | No rule matches — what result? | 0 today; path reachable and undefined |
| D3 | B3 — Reviewer or Senior Analyst? | 20 of 43 |
| D4 | Source unavailable — classification or readiness? | 43 of 43 |
| D5 | Which name differences are reportable? | 22 of 43 |
| D6 | "Flagged" vs "invalid" identifier | 0 today; latent |
| D7 | Potential exclusion match — automatic finding? | 0 today; blocks all non-compliance findings |

---

### DECISION D1: UNCORROBORATED NPI

**DECISION:** When an entity for which an NPI is expected provides an NPI that cannot be corroborated in NPPES, how should ARC classify the result?

**WHY NEEDED:** CMS requires HIPAA-covered providers to obtain an NPI, but NPPES states that **issuance of an NPI does not validate licensing or credentialing**. Non-corroboration is an identity observation; whether it is also a compliance finding is a program judgement. This applies **only** where an NPI was expected and supplied — not where none was supplied, which is legitimate for payers, public health agencies and health information networks (19.45% of the delivered population carries no NPI).

**REAL EXAMPLE:** No entity of the 43 presents this case — all 23 that supplied an NPI were corroborated; the other 20 supplied none. **AGT must flag a related observation:** DocuAction currently records "no NPI supplied" and "NPI supplied, not found" identically at the point of classification. AGT recommends separating them regardless of how D1 is answered. *(AGT implementation recommendation, not an ONC requirement.)*

**OPTIONS:**
- **A. Analyst investigation** — an observation warranting review, consistent with the NPPES caveat.
- **B. Non-compliant** — if the methodology requires a verifiable NPI, failure to corroborate is a material verification failure.
- **C. Conditional** — depends on Medicare relevance or on the reason for non-corroboration.

**OPERATIONAL IMPACT:** A routes to human review; B produces a reportable finding that may trigger QHIN notification; C requires a rule per condition.

**RECOMMENDED OPTION:** Program guidance requested — the current ARC methodology does not explicitly address this question.

**COR DECISION:** __________________

---

### DECISION D2: NO CLASSIFICATION RULE MATCHES

**DECISION:** When evidence gathering completes successfully but no B1–B4 rule matches, what should DocuAction record?

**WHY NEEDED:** This is not a source failing to answer — every applicable source answered, and the rules simply do not describe the combination observed. DocuAction must record something, and the two obvious answers are opposites.

**REAL EXAMPLE:** CHIA GRANDA MD LLC previously reached this path. After a correction to how name evidence reaches classification, an existing rule now matches it. **No entity currently takes this path** — but it remains reachable and its outcome undefined.

**OPTIONS:**
- **A. No discrepancy** — nothing in the rules flagged a problem.
- **B. Manual examination** — the rules not describing a case is itself reason for a person to look.
- **C. A separate UNDETERMINED / INSUFFICIENT_EVIDENCE state** outside B1–B4 until resolved.

**Constraint applying to all three:** "no match was observed" must never be recorded as evidence of compliance. A search that found nothing and a search that could not run are different facts, and neither is a clean result.

**OPERATIONAL IMPACT:** A closes the entity with no human step; B adds a review item; C requires a reporting category outside B1–B4 and a rule for counting it in deliverables.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this. AGT notes only that Option A would have DocuAction assert a compliance position with no evidentiary basis.

**COR DECISION:** __________________

---

### DECISION D3: B3 REVIEW TIER

**DECISION:** Should entities classified B3 (Inexplicable) route to Tier 2 (Reviewer) or Tier 3 (Senior Analyst)?

**WHY NEEDED:** The approved methodology requires three-tier routing and a Tier-3 queue restricted to senior analysts and above (Requirements Document V2, FR-T3-018, FR-T3-020). It does **not** state which bucket routes to which tier.

**REAL EXAMPLE:** All 20 current B3 entities — including Kauai Medical Clinic, CHILDRENS CLINIC and HAWAII COALITION FOR HEALTH — are small provider organisations that supplied no NPI, so identity could not be corroborated and Medicare enrolment could neither be established nor refuted. **None carries an adverse finding.** The common feature is an absent identifier.

**OPTIONS:**
- **A. Tier 2 (Reviewer)** — B3 reflects incomplete corroboration, not suspected violation; preserves senior capacity for adverse findings.
- **B. Tier 3 (Senior Analyst)** — the rules could not account for the evidence, warranting senior judgement.

**OPERATIONAL IMPACT:** Determines who works 47% of the current population, at which privilege level. Option B places 20 of 43 in the senior escalation queue. **The review queues cannot be placed in service until this is answered.**

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this. AGT notes it directly determines analyst staffing.

**COR DECISION:** __________________

---

### DECISION D4: SOURCE UNAVAILABLE

**DECISION:** When a source applicable to a verification dimension is technically unavailable, does that affect the B1–B4 classification, or only review readiness?

**WHY NEEDED:** A source that did not answer has said nothing about the entity. **DocuAction must never record an unavailable source as "no match found"** — that converts a third party's outage into a finding. DocuAction keeps these states strictly separate today and that is not in question. What is undecided is whether an unanswered source should change the classification or only whether the review can close.

**REAL EXAMPLE:** SAM.gov has been unreachable for **all 43 entities**. AGT has established, reproducing from three independent networks against both the production and alpha environments, that the SAM.gov API is not routing requests at all — every request returns an empty error with a valid credential, an invalid credential, and no credential. **This is not a credential problem; no key will resolve it.** Consequently the Exclusion / Debarment / Revocation dimension is UNAVAILABLE for every entity, even though OIG LEIE answered for 23 and CMS Revocation for all 43.

**OPTIONS:**
- **A. Classification proceeds** on evidence obtained, unavailability recorded per dimension, not counted against the entity.
- **B. Route to manual review** regardless of other evidence.
- **C. Remain UNDETERMINED** until the source is restored.
- **D. Source-specific rules** — some required, others corroborative.

**OPERATIONAL IMPACT:** A allows deliverables to proceed with a disclosed gap. B and C prevent any entity closing while SAM.gov is down, which on current evidence is indefinite.

**A related question the program may wish to answer together with this one:** is an ARC determination deliverable while this dimension is permanently unavailable?

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this.

**COR DECISION:** __________________

---

### DECISION D5: NAME DISCREPANCY SEVERITY

**DECISION:** Which differences between an RCE-provided organisation name and the NPPES legal name constitute a reportable discrepancy?

**WHY NEEDED:** DocuAction correctly detects name differences. **The question is which differences constitute a discrepancy.** Organisations routinely trade under a name differing from their registered legal name, so treating every difference as a discrepancy generates findings against entities that have done nothing wrong — while treating none as a discrepancy misses genuine identity questions. **22 of 43 entities have an observed name difference but are currently recorded without a name-based discrepancy, because the methodology has not established which differences are reportable.**

**REAL EXAMPLES** *(the scale below is an AGT implementation recommendation, not an ONC requirement):*

| Category | Real example from this population |
|---|---|
| **NAME_EXACT** | 21 of 43 entities |
| **NAME_NORMALIZED_EQUIVALENT** — case, punctuation, whitespace | *MALAMA KINO PRIMARY CARE INC* / *MALAMA KINO PRIMARY CARE INC.* |
| **NAME_MINOR_VARIATION** — corporate suffix, abbreviation | *PACIFIC PULMONARY CONSULTANTS* / *…CONSULTANTS LLC*; *Buffalo Medical Group* / *BUFFALO MEDICAL GROUP, P.C.* |
| **NAME_MATERIAL_MISMATCH** — substantively different identity | *Hawaii Pacific Health* / *KAPIOLANI MEDICAL SPECIALISTS*; *KUHIO MEDICAL CENTER* / *HAWAII FAMILY MEDICAL CENTERS* |
| **NAME_AMBIGUOUS** — undeterminable without investigation | *UTMB - Health* / *THE UNIVERSITY OF TEXAS MEDICAL BRANCH* |

**OPTIONS:**
- **A. None reportable** — trading-name variance is ordinary.
- **B. Only MATERIAL_MISMATCH and AMBIGUOUS reportable** — formatting and suffix differences are administrative.
- **C. All differences beyond NAME_EXACT reportable.**
- **D. A different grading than the scale above.**

**OPERATIONAL IMPACT:** Under B, roughly 6 of the 22 become reportable and the rest close without review. Under C, all 22 require review. The scale itself also requires approval.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this. AGT recommends *some* graduated scale be adopted, because the present binary treatment cannot distinguish a missing full stop from two entirely different organisation names.

**COR DECISION:** __________________

---

### DECISION D6: IDENTIFIER "FLAGGED" VERSUS "INVALID"

**DECISION:** What is the approved distinction between a *flagged* and an *invalid* identifier, and does an invalid identifier automatically produce a non-compliance finding?

**WHY NEEDED:** The classification rules use both words. One set treats a flagged identifier as grounds to withhold a clean pass; another treats an invalid identifier as grounds for a non-compliance finding. Neither term is defined, so DocuAction cannot determine which defects fall into which category. NPI defects vary in kind — wrong length, non-numeric characters, and a well-formed NPI failing its check digit are different situations. The delivered population contains 4 malformed NPIs and 2 check-digit failures.

**REAL EXAMPLE:** No entity among the 43 carries an NPI defect. The question is latent and will arise on the next sample from the full 23,566-record population.

**OPTIONS:**
- **A. Two-level model** — *flagged* means "suspect, withhold a clean pass"; *invalid* means "definitively unusable, non-compliant". Requires the program to state which defects are definitive.
- **B. Single level** — the terms mean the same thing and one rule set should be aligned to the other.

**OPERATIONAL IMPACT:** Under A with check-digit failure treated as definitive, such entities become reportable findings without human review. Under B they are withheld from a clean pass and routed to review.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this.

**COR DECISION:** __________________

---

### DECISION D7: EXCLUSION EVIDENCE AND NON-COMPLIANCE

**DECISION:** When exclusion screening produces a **potential** match rather than a confirmed one, should that automatically produce a non-compliance determination?

**WHY NEEDED:** Exclusion screening produces two materially different results:

- **CONFIRMED match** — exact match on a unique identifier (NPI) against OIG LEIE or SAM.gov. Identity is established.
- **CANDIDATE match** — match on organisation or individual name where no unique identifier was available. **Name-based matching produces false positives**; common personal names and similar organisation names collide routinely.

DocuAction currently records both as requiring analyst confirmation of identity before any determination. The classification rules treat an exclusion as disqualifying regardless of other evidence. **Both positions are defensible; they cannot both be implemented.** Until this is decided, DocuAction cannot issue a non-compliance determination through its primary path.

The architecture principle at issue is the sequence **source observation → match quality → applicability → evidence → verification → human review where required.** The decision is where in that sequence a determination becomes automatic, and for which match qualities.

**REAL EXAMPLE:** No entity among the 43 produced an exclusion match of either kind. OIG LEIE returned no exclusion for the 23 it could screen by NPI; SAM.gov was unreachable for all 43 (see D4).

**OPTIONS:**
- **A. Confirmed identifier match determines automatically; candidate name match routes to identity confirmation first.**
- **B. Both route to analyst identity confirmation before any determination.**
- **C. Both determine automatically.**
- **D. Different treatment per source** — e.g. OIG NPI matches differently from SAM.gov name matches.

**OPERATIONAL IMPACT:** Option C would allow a name collision to generate a federal non-compliance finding against an entity that is not the excluded party. Option B introduces a mandatory human step before any exclusion finding, with an associated turnaround time.

**RECOMMENDED OPTION:** Program guidance requested — the methodology does not address this. AGT notes only that a name-based match is materially less reliable than an identifier match, and the decision may reasonably differ between the two.

**COR DECISION:** __________________

---

## AFTER THESE DECISIONS

AGT will implement the approved methodology as a **new versioned rule set**. Determinations already recorded will be preserved exactly as issued and remain explainable; any reclassification will be recorded as a new review, never as an edit. **Until these decisions are received, AGT will not designate an authoritative classification path.**

*Supporting engineering detail is retained in `docs/methodology_decision_package.md` — an engineering appendix, not part of this submission.*
