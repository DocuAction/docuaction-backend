# TEFCA ARC — How AGT Reviews Entity Data

**A plain-language explanation of the review methodology, for the Contracting Officer's Representative.**

Contract 7571MN26F80064 · Alliance Global Tech, Inc. · Prepared 2026-08-23

---

> **Status of this document.** It explains the methodology submitted under Task 2
> (D2, submitted 9 July 2026, resubmitted in Word 27 July at ONC's direction) in
> program language rather than technical language. It is a companion to D2, not a
> replacement for it, and it does not change anything D2 says.
>
> **No entity data has been reviewed under this contract.** The Government
> assignment has not been issued and the entity CSV has not been delivered.
> Entities reviewed: **0 of 383**. Every capability described below is
> demonstrated on development data.

---

## 1. What AGT receives

A list of Participants and Subparticipants, supplied by the COR, describing which
organisations are connected to which QHIN and what identifying information each
has registered — organisation name, address, and where available an NPI or other
identifier.

The list is the *subject* of the review. AGT does not treat it as authoritative;
the whole purpose of the work is to test whether what it says matches what
authoritative sources say.

**What AGT does when the list arrives.** The file is recorded exactly as
received — the original bytes, a cryptographic fingerprint of them, the time of
receipt and who sent it — and is never edited. Every later result points back to
that record. If a question arises months afterwards about what was reviewed, the
answer is the file itself, provably unchanged.

---

## 2. What AGT checks

For each entity, five questions:

| | Question | Why it matters |
| --- | --- | --- |
| 1 | **Does this organisation exist as it is described?** | An entity in the directory that cannot be found is a directory-accuracy problem. |
| 2 | **Is the registered address consistent with public records?** | Addresses drift. A stale address affects who can be reached. |
| 3 | **Is the identifier valid and does it belong to this organisation?** | An identifier that resolves to a different organisation is a more serious problem than one that is merely out of date. |
| 4 | **Is this organisation excluded, revoked, or debarred?** | The most consequential question, and the one requiring the most care before anything is said. |
| 5 | **Is the participation role consistent with what the organisation is?** | A Participant described as something it is not affects how data flows. |

Not every question applies to every entity. A question that does not apply is
recorded as *not applicable* with the reason, and is never counted as a pass or a
failure. The distinction matters: "we checked and it was fine", "we checked and
it was not fine", and "this check does not apply here" are three different
results, and collapsing them would overstate both coverage and problems.

---

## 3. Which authoritative sources are consulted

| Source | Authority for | Published by |
| --- | --- | --- |
| **NPPES** | Provider identity and registered address | CMS |
| **CMS PECOS / PPEF** | Medicare enrolment, practice locations, reassignments | CMS |
| **OIG LEIE** | Exclusion from Federal health care programs | HHS OIG |
| **CMS Revocation** | Medicare enrolment revocation | CMS |
| **SAM.gov** | Federal debarment and exclusion | GSA |
| **USPS** | Address standardisation | USPS |

Every source is consulted as of a stated date, and the answer is recorded with
that date. A source's answer today is not evidence about last quarter, and a
report that cited an undated lookup would be unreproducible.

**When a source cannot answer.** That is recorded as a fact about the *lookup*,
not about the *entity*. An entity is never marked deficient because a Federal
system was unavailable, rate-limited, or returned nothing. Reports state which
sources could not be reached and how many entities that affected.

> **Currently affecting the whole population:** SAM.gov requires a credential
> that has not yet been issued to AGT. Until it is, SAM.gov results are recorded
> as *unavailable*, and any conclusion that would depend on them is withheld.

---

## 4. How evidence is preserved

Every answer from every source is written once and never changed. If the same
question is asked again next quarter and the answer differs, that becomes a new
record; the old one stays exactly as it was.

This is what makes a report answerable later. A figure in a report issued in
October can be traced to the specific source answers behind it, as they were
when the report was issued — not as the sources read today.

Reports are produced only from this preserved evidence. Generating a report never
triggers a fresh lookup, so a report cannot quietly change because a source
updated between one reading and the next.

---

## 5. How discrepancies are identified and categorised

The four categories the contract requires:

1. **No discrepancies identified**
2. **Minor or administrative discrepancies**
3. **Inexplicable discrepancies**
4. **Non-compliant discrepancies**

**These four categories are the Government's**, stated in the solicitation. What
AGT proposes under D2 is the *rules* for deciding which category a given entity
falls into. Internally AGT abbreviates the four as B1–B4; **that shorthand is
AGT's own and is not a TEFCA, ONC, ASTP, RCE or Sequoia classification.**

A comparison produces a *factual* result — the two values agree, they agree after
normalisation, they disagree, or there was not enough information to compare.
Deciding what a disagreement *means* is a separate step, and where the
methodology does not yet settle it, the item is held rather than guessed.

> **Worked example — the address question.** The Government's list carries a
> *registered* address. NPPES and PECOS publish *practice locations*. Those are
> different kinds of address and can legitimately differ for a compliant
> organisation. On the development dataset 9,032 records show some address
> difference. Whether a street-line difference between a registered address and a
> practice location is *material* is not something AGT should decide alone — so
> those records are counted, reported, and marked as awaiting a methodology
> decision. They are **not** reported as failures. Asking the COR is the
> methodology; guessing would not be.

---

## 6. How analysts review

Nothing the system produces is a finding. The system sorts and presents; a person
decides.

Each item reaching an analyst carries the evidence behind it, the sources
consulted and their dates, what was compared, what the comparison found, and any
methodology question that is unresolved. The analyst records a determination and
a written rationale. Both are kept permanently.

An analyst can confirm the indicated category or assign a different one. Either
way the reasoning is recorded, because a determination without a rationale cannot
be reviewed by anyone else.

---

## 7. How independent quality assurance works

Every analyst determination is reviewed by **a different person**. The system
enforces this — an analyst cannot approve their own work.

The QA reviewer has three options:

- **Approve** — the determination stands and becomes eligible for reporting.
- **Return** — sent back to the analyst with a reason. The original determination
  is preserved, not erased; the record shows it was made, returned, and why.
- **Escalate** — referred to a named senior reviewer, with a reason.

Only an approval that still stands makes a determination reportable. If a
determination is approved and later returned, the approval is revoked and the
item is back in play.

If a determination has to be changed after approval, the new one *supersedes* the
old — it does not overwrite it. The full sequence stays visible, so the record
shows what was decided, when, by whom, and what changed it.

---

## 8. How reports are produced

Reports read only from preserved evidence and recorded human determinations.
Every report carries, on its face:

- which delivery of entity data it covers, identified by fingerprint
- which generation of evidence its numbers came from
- which analyst determinations and QA approvals it reflects
- when it was generated, and by whom
- an integrity fingerprint of the data it rendered

The same report regenerated from the same evidence produces the same numbers. If
it did not, the numbers would not mean anything.

**Reports distinguish four kinds of statement, and never blur them:**

| Kind | Example |
| --- | --- |
| **Factual observation** | "NPPES returned an address that differs from the registered one." |
| **AGT methodology result** | "Under the proposed rules this would fall in category 2." |
| **Human determination** | "The analyst determined category 2; QA approved on 12 October." |
| **Awaiting program guidance** | "9,032 records show an address difference. Materiality is not settled." |

---

## 9. How priority reviews work

The COR names the entities and sets the deadline. AGT reviews them through the
same evidence, analyst and QA path as any other review — the urgency changes the
sequence, not the standard.

A priority status report records what the contract requires: the identified
issue, the root cause where it can be determined, the severity or impact,
recommendations to prevent recurrence, and the resolution — together with the
evidence consulted, the analyst determination, the QA decision, and the times at
each stage so turnaround against the COR's deadline is measurable.

Because the deadline is set per request, AGT measures against the deadline given
rather than against any fixed internal target.

---

## 10. How unresolved methodology questions are handled

They are named, counted, and reported — never quietly resolved in code.

Nine decisions are currently open and recorded in the COR Decision Register. Each
states the question, the options, what AGT recommends, and how many records it
affects. Until a decision is made, affected records are reported as *awaiting
methodology*, with the count shown.

This is deliberate. An unresolved question that is hidden becomes an assumption,
and an assumption embedded in a report is very hard to find later.

---

## 11. What AGT will not do

- Report an entity as non-compliant without a human determination and independent
  QA approval.
- Treat a source outage as evidence about an entity.
- Resolve a methodology question by choosing a default in software.
- Present an AGT construct as an ONC, ASTP, RCE, Sequoia or TEFCA classification.
- Issue a finding to the Government before the release conditions are met — the
  full list is in the Official Finding Release Gate, and six of its twelve
  conditions are outside AGT's control today.

---

## 12. What AGT needs to begin

| | Needed from | Status |
| --- | --- | --- |
| Written acceptance of the D2 methodology | COR | Submitted 9 Jul, resubmitted 27 Jul; awaiting acceptance |
| Confirmation of the sampling parameters in D2 §5.1 | COR | 95% confidence, ±5%, 383 entities, stratified across 11 QHINs |
| Government assignment authorising Task 3 review | Government | Not issued |
| Entity data (CSV via Box) | COR | Not delivered |
| The nine open methodology decisions | COR | All pending |
| SAM.gov credential | Government / GSA | Not issued |

The platform is built, tested and idle. The critical path runs through these six
items.
