# DOCUACTION TEFCA ARC
# METHODOLOGY DECISION PACKAGE

**Date:** 2026-08-22
**Prepared for:** COR / Program Methodology Authority
**Prepared by:** DocuAction engineering (Alliance Global Tech)
**Contract:** 7571MN26F80064 (HHS/ONC)
**Status:** Awaiting program decision. No decision has been made or implemented.

---

## BACKGROUND

DocuAction verifies TEFCA entities against six evidence dimensions (D1–D6) and
classifies each entity into a verification bucket (B1–B4), which determines the
review tier (T1–T3).

During system verification of **43 entities** drawn from the ONC/RCE delivery of
2026-07-20 (23,566 records), **seven methodology questions** were identified that
require program-level decisions.

These are not implementation defects. Each is a point where the system must
assert something the approved methodology does not currently state, and where two
defensible readings produce different compliance outcomes for a real entity.
Engineering has deliberately made no choice on any of them.

The four originally identified questions are **D1–D4**. Three further questions —
**D5, D6, D7** — were surfaced by a signal-plumbing repair completed on
2026-08-22 and are included because they are of the same kind.

**A note on what changed and what did not.** A field-name defect was corrected
(the name-comparison signal was being looked up under the wrong key and therefore
never reached the classifier). That was an engineering fix and is complete. It
changed the classification of **1 entity of 43**. It did not resolve any question
in this package, and it surfaced D5–D7.

---

## SUMMARY OF DECISIONS REQUESTED

| # | Question | Current state | Impact on the 43-entity population |
|---|---|---|---|
| **D1** | NPI not found in NPPES → which bucket? | Classifier A says B4; Classifier B says B3 | 20 entities |
| **D2** | No classification rule matches → what result? | Classifier A defaults to B1; Classifier B defaults to B3 | 1 entity (was 1 before the repair) |
| **D3** | B3 → which review tier? | Classifier A routes T2; Classifier B routes T3 | 20 entities |
| **D4** | A required source is unavailable → does it change the bucket? | SAM.gov unreachable for 43/43; the two classifiers behave differently | 43 entities |
| **D5** | Is a name difference "minor" regardless of magnitude? | Every name difference is graded `minor` | 22 entities |
| **D6** | Does a failed NPI check digit make an entity non-compliant? | Two vocabularies (`flagged` / `invalid`) that never meet | 0 today; latent |
| **D7** | Does a potential exclusion match become an automatic B4? | B4 is currently unreachable on the primary path | 0 today; latent |

---

## DECISION D1 — NPI NOT FOUND

**When an entity's NPI cannot be found in the authoritative NPPES registry, what
ARC bucket should result?**

### Option A — B3, requires analyst investigation

Potential rationale: absence from NPPES is an observation warranting
investigation, not automatically a compliance determination. CMS states that NPI
issuance does not validate licensing or credentialing, so absence likewise does
not by itself establish non-compliance. Many TEFCA entities legitimately hold no
NPI — 4,584 of the 23,566 delivered records (19.45%) carry none at all.

### Option B — B4, non-compliant

Potential rationale: if the approved ARC methodology establishes that an
applicable entity must have a verifiable NPI, failure to corroborate that
required identifier may constitute a material verification failure.
**Program confirmation required** — engineering has found no document in the
contract record establishing that requirement.

### Option C — Conditional / other

For example: B3 where the entity is not Medicare-relevant, B4 where it is; or a
distinction between "no NPI was supplied" and "an NPI was supplied and NPPES does
not recognise it". The system currently treats these as the same case.

### Examples from the current population

| Entity | NPI supplied |
|---|---|
| COREY C CHINN MD LLC | none |
| UT Health Rio Grande Valley | none |

**Applies to 20 of 43 entities reviewed.**

### Current system behaviour

- Classifier A (`ValidationEngine`) assigns **B4**.
- Classifier B (`BucketClassifier`, the versioned rule set) assigns **B3**.

---

## DECISION D2 — DEFAULT WHEN NO CLASSIFICATION RULE MATCHES

**When the evidence pipeline completes but no explicit B1–B4 classification rule
matches the resulting evidence state, what should the system do?**

### Option A — B1, no discrepancy identified

### Option B — B3, manual examination required

### Option C — a separate UNDETERMINED / INSUFFICIENT_EVIDENCE state

A state that does not map to B1–B4 at all until additional evidence or analyst
review resolves it. This is the only option that does not require the system to
assert a compliance position it has no basis for.

### Important

`NO_MATCH_OBSERVED` and `SOURCE_UNAVAILABLE` must not be described as "clean"
evidence. These are distinct evidence states, and neither is a finding of
compliance. Option A would convert "the rule set does not describe this case"
into "no discrepancy was found", which are different claims.

### Example from the current population

| Entity | Before the 2026-08-22 repair | After |
|---|---|---|
| CHIA GRANDA MD LLC | no rule matched → defaulted to B3 | RULE-003 now matches → B2 |

This entity is instructive: it reached the default only because a signal was
missing. Repairing the signal removed it from the default path. **The default
path is now taken by zero entities in this population** — but it remains reachable
and undefined, and the question stands.

### Current system behaviour

- Classifier A: no findings → **B1**.
- Classifier B: no rule matched → **B3**, recorded under the reserved code
  `DEFAULT-UNMATCHED` so the determination still cites something.

---

## DECISION D3 — B3 REVIEW TIER

**Which review tier should handle B3 entities?**

### Option A — T2, Reviewer

### Option B — T3, Senior Analyst

### What a B3 entity looks like in practice

All 20 current B3 entities are small provider organisations for which no NPI was
supplied, so NPPES could not confirm identity and CMS enrolment could not be
established or refuted. There is no adverse finding against any of them; the
common feature is an absent identifier.

| Entity | Why B3 |
|---|---|
| Kauai Medical Clinic | no NPI supplied; NPPES not_found |
| CHILDRENS CLINIC | no NPI supplied; NPPES not_found |
| HAWAII COALITION FOR HEALTH | no NPI supplied; NPPES not_found |

### Why this must be decided before the analyst queue is built

The tier determines which queue an entity enters, which role may work it, and
which endpoint serves it. `senior_analyst` (privilege level 5) gates the T3 queue;
`viewer` (level 1) can read the T2 queue. Twenty entities would be routed to a
different person at a different privilege level depending on the answer. The
analyst-queue wiring cannot be finalised until this is settled — see
`docs/analyst_queue_wiring_plan.md`.

### Current system behaviour

- Classifier A: B3 → **T2** (`reviewer`).
- Classifier B: B3 → **T3** (`senior_analyst`).

Nothing in the contract record states a bucket-to-tier mapping.

---

## DECISION D4 — REQUIRED SOURCE UNAVAILABLE

**When an evidence source applicable to a verification dimension is technically
unavailable — for example SAM.gov, which is currently returning HTTP 404 for
every entity — how should classification proceed?**

### Option A — continue, recording SOURCE_UNAVAILABLE per dimension

Classification proceeds on the remaining evidence; the unavailability is recorded
on the dimension and does not count against the entity.

### Option B — route to manual review regardless of other evidence

### Option C — remain UNDETERMINED until the source is restored

### Option D — follow source-specific applicability rules

Some sources may be required, others corroborative; the answer differs per source
and per dimension.

### The question that must be answered explicitly

**Does temporary technical unavailability change the B1–B4 classification, or only
review readiness?** These are different things and the system currently conflates
them differently in each classifier.

### Constraint that applies to every option

The system must never interpret `SOURCE_UNAVAILABLE` as `NO_MATCH_OBSERVED`.
A source that did not answer has not said anything about the entity.

### Current situation

SAM.gov has been unreachable for the entire population. Engineering has
established, with reproduction from three independent networks and against both
`api.sam.gov` and `api-alpha.sam.gov`, that the platform is not routing its API at
all: every path returns an empty HTTP 404, with a valid key, with an invalid key,
and with no key. **This is not a credential problem and no key will resolve it.**

Consequently **D3 (Exclusion / Debarment / Revocation) is UNAVAILABLE for 43 of 43
entities**, even though OIG LEIE answered for 23 and CMS Revocation answered for
all 43. SAM is one of three separately-identifiable controls inside that
dimension.

### Current system behaviour

- Classifier A: marks every entity `indeterminate`, blocks auto-completion
  entirely, and routes all 43 to T2. It never assigns T1.
- Classifier B: excludes unavailable sources from discrepancy counting and
  auto-completes 12 entities at T1.

### A related question the program may wish to answer at the same time

**Is an ARC determination deliverable while D3 is permanently UNAVAILABLE?**
No entity in the population can currently reach a fully-covered D3.

---

## DECISION D5 — SEVERITY OF A NAME DIFFERENCE

*Surfaced 2026-08-22 by the signal-plumbing repair.*

**Should every organisation-name difference be graded "minor", or should
magnitude determine severity?**

The system now correctly detects a name difference between the ONC/RCE submission
and NPPES. It grades every such difference as `minor`, which routes it to B2
(Minor or Administrative).

### The two readings

**Option A — all name differences are minor.** An organisation's trading name
differing from its NPPES legal name is an ordinary administrative fact, not a
compliance concern.

**Option B — magnitude matters.** A punctuation or abbreviation difference is
minor; a name that shares no recognisable relationship with the registered legal
name is a material identity question.

### Why this is not an engineering choice

Classifier A already implements a five-band similarity model
(≥0.90 no finding · ≥0.70 abbreviation · ≥0.50 DBA-vs-legal · ≥0.30 completely
different · below 0.30 unresolvable), with the last two bands escalating to B3 and
B4. Classifier B has no bands at all. Choosing bands, or choosing to have none, is
a methodology decision — the thresholds are not derivable from anything in the
contract record.

### Examples from the current population, at both extremes

| Submitted (ONC/RCE) | NPPES legal name | Currently graded |
|---|---|---|
| UTMB - Health | THE UNIVERSITY OF TEXAS MEDICAL BRANCH | minor |
| Buffalo Medical Group | (differs) | minor |
| JAMES Y SIM, MD | (differs by punctuation) | minor |

The first and the third receive identical treatment.

### A second question inside the same decision

Of the 22 entities where a name difference is now detected, **12 are still
classified B1 — "No Discrepancy"**. This is because RULE-001 (B1 Full Pass,
priority 10) is evaluated before RULE-003 (B2, priority 30) and its exclusion
list does not mention `name_mismatch`. An entity can therefore be reported as
having no discrepancy while a name discrepancy has been observed and recorded.

**Is that intended?** Adding `name_mismatch` to RULE-001's exclusion list would be
a rule change and has not been made.

| Entities with an observed name difference, classified B1 | 12 |
|---|---|
| REV-2026-000002 Buffalo Medical Group | RULE-001 |
| REV-2026-000003 SC - CAROLINA ORTHOPAEDIC AND NEUROSURGICAL | RULE-001 |
| REV-2026-000004 PA - Fountain Medical Associates, PC | RULE-001 |
| REV-2026-000005 ALOHA FOOT CENTERS SA | RULE-001 |
| REV-2026-000010 Hawaii Pacific Health | RULE-001 |
| REV-2026-000011 MALAMA KINO PRIMARY CARE INC | RULE-001 |
| REV-2026-000015 KAANAPALI MEDICAL SERVICES | RULE-001 |
| REV-2026-000016 KUHIO MEDICAL CENTER | RULE-001 |
| REV-2026-000017 CHARLIE Y SONIDO MD | RULE-001 |
| REV-2026-000018 TERRY Q YEE MD | RULE-001 |
| REV-2026-000020 PACIFIC PULMONARY CONSULTANTS | RULE-001 |
| REV-2026-000037 PEGGY M LIAO MD | RULE-001 |

---

## DECISION D6 — NPI VALIDATION VOCABULARY

*Surfaced 2026-08-22.*

**Does a malformed NPI or a failed check digit make an entity non-compliant (B4),
or merely ineligible for a clean pass (B1)?**

The rule set uses two different words for the NPI-validation signal and they never
meet:

| Rule | Condition | Effect |
|---|---|---|
| RULE-001 / 002 / 003 | `none_of npi_validation = flagged` | an NPI problem prevents B1 and B2 |
| RULE-005 (B4) | `any_of npi_validation = invalid` | an NPI problem forces B4 |

The RCE evidence path emits only `flagged`. The registry review path emits only
`valid` / `invalid`. Neither path can satisfy both readings.

### The two readings

**Option A — deliberate two-level model.** `flagged` means "suspicious, do not
auto-pass"; `invalid` means "definitively bad, non-compliant". Both are correct
and the RCE path simply needs to emit `invalid` in the definitive cases.
This requires deciding **which** NPI defects are definitive.

**Option B — vocabulary drift.** The two words mean the same thing and one rule
set was written against the wrong term.

### Current impact

Zero entities today — the delivered population contains 4 malformed NPIs and 2
check-digit failures, none of which belongs to the 43 entities reviewed.
The question is latent but will bite on the next sample.

Engineering has **not** changed either vocabulary. A regression test pins the
current behaviour so that the question must be answered rather than silently
resolved by a future edit.

---

## DECISION D7 — DOES A POTENTIAL EXCLUSION MATCH BECOME AN AUTOMATIC B4?

*Surfaced 2026-08-22.*

**B4 (Non-Compliant) is currently unreachable on the primary verification path.**
Across 43 entities, zero B4 determinations were possible — not because no entity
qualified, but because none of RULE-005's four conditions can be satisfied.

| RULE-005 condition | Why it cannot fire |
|---|---|
| `oig_leie = excluded` | The evidence layer reports an exclusion hit as `REVIEW` — deliberately, "pending identity matching, never an automatic rejection" — and no disposition maps to the state `excluded` |
| `sam_gov = excluded` / `debarred` | Same, plus SAM is unreachable (D4) |
| `npi_validation = invalid` | Vocabulary split (D6) |
| `required_verification_failed` | No producer exists anywhere in the system |

### The decision

**Should a potential OIG LEIE or SAM exclusion match automatically produce B4, or
should it route to an analyst for identity confirmation first?**

The evidence layer's current position is explicit and deliberate: an exclusion
match is a potential match until a human confirms the identity, because LEIE
matching on name or NPI can collide. The rule set's position is that an exclusion
is disqualifying regardless of what other sources say.

Both are defensible. They cannot both be implemented. Engineering has changed
neither, and a regression test pins the current unreachability so it cannot be
resolved accidentally.

**This is the most consequential item in this package.** Until it is decided, the
system cannot produce a Non-Compliant determination through its primary path.

---

## REQUESTED

Program decision on **D1, D2, D3, D4** and, if the program wishes to address them
in the same cycle, **D5, D6, D7**.

After decisions are received, DocuAction will implement the approved methodology
as a new **versioned rule set** (the current active set is version 2), so that
determinations made under version 2 remain explainable and are not silently
re-meant. The 43 determinations already recorded will be preserved exactly as
issued; any re-classification under the approved methodology will be recorded as a
new review generation, not as an edit to the existing record.

**Until these decisions are received, engineering will not designate either
classifier as authoritative.**

---

## APPENDIX — WHAT WAS FIXED WITHOUT A DECISION, AND WHY IT NEEDED NONE

For completeness, three engineering corrections were made on 2026-08-22 under the
stabilization gate. None of them chose a methodology position.

| Fix | Why it required no decision |
|---|---|
| Name-comparison signal was looked up under the key `name` while the evidence layer writes `legal_name` | A field-name mismatch. Seven independent layers — the dimension's own declared vocabulary, the NPPES and SAM connector payloads, the database column, and all 92 persisted conflict rows — use `legal_name`. The consumer was the sole outlier. Restoring the key delivers the input RULE-003 was already written to consume; it does not change what the rule does with it. **Changed 1 entity of 43.** |
| Duplicate DQ rule IDs were silently deduplicated | `FMT-005` and `FMT-006` already exist. A duplicate would have executed twice, merged its counters, mis-stamped severities, and then failed the whole 23,566-record run at commit. The set now refuses to load with a duplicate and reports the next free IDs. **Changed no classification.** |
| Four rule conditions had no producer | Each was assessed against the wiring criteria and **none was wired**, because each would have required inventing a threshold, a comparison, or a semantic. They are declared unproduced with a recorded reason and are covered by a test that fails if a new rule condition is added without either wiring or declaring it. **Changed no classification.** |
