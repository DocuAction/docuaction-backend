# TEFCA ARC — Evidence-Based Verification Methodology

**Internal engineering record. Not a Government deliverable. No secrets, no
Government row-level values.**
Contract 7571MN26F80064 · 31 August 2026 · methodology version 1.0.0

---

## 1. The defect this closes

**"1 of 3 sources agree"** as a determination.

That sentence treats NPPES, SAM.gov and LEIE as three interchangeable votes on
one question. They are not. They answer different questions, about different
entity types, under different contract requirements. Counting them makes an HIE
with no NPI look like a provider hiding one, and makes a SAM.gov absence —
non-determinative for almost every TEFCA participant — look like a finding.

**A missing record is not a discrepancy.**

The headline is now stated in controls:

    5 of 6 applicable controls satisfied, 1 requires analyst review

Source agreement still appears, but **inside** a control, where it is
supporting evidence rather than a verdict.

---

## 2. D1 — the participation anchor

TEFCA participation is established by the **RCE/QHIN-delivered population** and
nothing else. `PARTICIPATION_ANCHOR = "RCE_DELIVERED_POPULATION"`.

No external database is consulted to decide whether an organisation
participates. NPPES does not know about TEFCA; SAM.gov absence says nothing
about Participant status. External evidence validates **attributes** of a
participant already known.

---

## 3. D2 — classification and a configurable applicability matrix

Eight entity classes, including `REQUIRES_CLASSIFICATION` — which is a routing
decision, not a failure. An unrecognised organisation goes to a human rather
than inheriting a provider's obligations by default.

Classification reads the **delivered record**. Matching is on word boundaries,
not substrings: `"hin"` matched "somet**hin**g" during development and
classified an unmapped organisation as an HIE. Short acronyms are exactly the
tokens most likely to hide inside ordinary words.

`APPLICABILITY_MATRIX` is **data, not control flow** — `entity class → control →
(requirement, sources, rationale)`. Nothing branches on an entity class; every
caller consults the table. It is the artefact a methodology reviewer reads and
corrects.

Two rules are visible in it, and they are the point:

| | |
|---|---|
| **NPI** | `NOT_APPLICABLE` for HIE/HIN, Health IT, payers and federal bodies. They legitimately have none. |
| **SAM.gov** | `CORROBORATIVE` everywhere; **no entity class requires it**, asserted by test. |

Controls carry a contract task, so a determination traces to a requirement
rather than to a database.

### `satisfied_by_absence`

Some controls are satisfied by *presence*, others by *absence*, and conflating
them was a real defect found by test.

* **Exclusion screening** — a provider **not** on the LEIE is the good outcome.
  Absence satisfies the control. Sending every clean screen to a human would
  make the queue unusable and teach reviewers to click through.
* **Identity / enumeration** — absence means we did not confirm what we set out
  to confirm, so a person should look.
* **Federal award eligibility** — absence satisfies *nothing* and withholds
  nothing. It tells us nothing, so it is `NOT_FOUND` on a corroborative control
  and cannot gate compliance either way.

---

## 4. D3 — the six evidence states

| State | Meaning |
|---|---|
| `VERIFIED` | applicable evidence corroborates the attribute |
| `CONFLICT` | applicable evidence **materially contradicts** it |
| `NOT_FOUND` | the source answered and had no record |
| `NOT_APPLICABLE` | the control has no bearing on this entity class |
| `SOURCE_UNAVAILABLE` | the source did not answer |
| `MANUAL_VERIFICATION_REQUIRED` | electronic evidence cannot settle it |

**The rule, enforced in code and asserted exhaustively by test:**

    NOT_FOUND ≠ CONFLICT
    NOT_APPLICABLE ≠ CONFLICT
    SOURCE_UNAVAILABLE ≠ CONFLICT

None of those three may ever, alone, produce an adverse determination.
`CONFLICT` is reachable **only** through an explicit `contradicts=True`
assertion — a returned legal name that is a different organisation, an exclusion
that matches the screened identity. A source simply having no record can never
reach it.

Cardinality and weak matches (`MULTIPLE_MATCHES`, `AMBIGUOUS`,
`INSUFFICIENT_IDENTIFIER`) and our own `ERROR` all route to
`MANUAL_VERIFICATION_REQUIRED`, never to `CONFLICT`.

---

## 5. Two concepts that must never collapse

| ENTITY VERIFICATION | CONTRACTUAL COMPLIANCE |
|---|---|
| `VERIFIED` | `SATISFIED` |
| `PARTIALLY_VERIFIED` | `POTENTIAL_FINDING` |
| `UNRESOLVED` | `NON_COMPLIANT` — human + QA only |
| `CONFLICTING` | `UNABLE_TO_DETERMINE` |
| | `NOT_APPLICABLE` |

"Identity not automatically verified" is a statement about **our evidence**.
"TEFCA non-compliant" is a statement about **the organisation**. The first must
never become the second.

---

## 6. D4 — automation stops at POTENTIAL_FINDING

`preliminary_assessment` **cannot return `NON_COMPLIANT`**. That is not a
convention; a test drives every pairwise combination of all eight observation
states across four entity classes, with a contradiction forced, and asserts the
value is never produced.

The chain is unchanged and remains the certified one:

    automation → preliminary assessment → analyst accept/modify/reject
               → independent QA → final reportable determination

---

## 7. Manual / documentary verification

`MANUAL_VERIFICATION_REQUIRED` with no way out would be an unfalsifiable
backlog. `ManualEvidence` closes it, and enters the **same** maker/checker chain:

* it carries evidence type, source, received date, **document hash**, analyst,
  rationale, QA reviewer and QA disposition;
* it resolves a control **only** when independent QA approved it, and the QA
  reviewer is not the analyst — self-approval resolves nothing;
* it can **never overturn a `CONFLICT`**. A document asserting the contrary of
  the evidence is a disagreement for a human to weigh, not an override.

---

## 8. Provenance

Every observation carries source, state, match method, dataset version,
retrieval timestamp, query attributes, returned name and identifier, detail and
an **evidence hash** — a digest of the observation, so an unchanged answer is
visibly unchanged across runs.

For LEIE the `match_method` distinguishes **bulk screening** from the
**authorised OIG search process**.

**EIN/TIN never travel into the assessment.** A test builds an entity carrying
an EIN and asserts it does not appear anywhere in the rendered output.

---

## 9. Where it lives

| Module | Role |
|---|---|
| `verification_methodology.py` | classification, matrix, six states, control assessment, coverage |
| `verification_coverage_service.py` | adapter from stored `tefca_verifications` — reads only |
| `review_routes.py` | `GET /api/tefca/arc/entities/{id}/verification-coverage`, `viewer`, read-only |

Three existing layers are **unchanged and reused**: `applicability.py`
(dimensions), `source_applicability.py` (sources), `evidence_vocabulary.py`
(`ObservationState`). The methodology is a *reading* of evidence that already
exists, not a second evidence store — two answers to "what did LEIE say" would
mean the older one goes stale first.

---

## 10. Known limitation

The classification signal set is deliberately small and reads one delivered
field (`sequoiaorgtype`). It is configuration, so it is cheap to extend — and an
entity it does not recognise is routed to a human rather than guessed at, which
is the safe direction. Broadening it is a methodology decision, not an
engineering one.
