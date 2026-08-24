# Phase 8 Learning Center — handoff from Phase 7

**What Phase 8 needs from Phase 7, and where it already exists.**
Prepared 2026-08-23 · Phase 7 commits `c9c41fc`, `24ae032`

> **DEVELOPMENT / TEST DATA.** Every count referenced below is a development
> validation result, not an ONC finding. The Government entity CSV has not been
> imported. **Nothing in this document is an implementation instruction** — it is
> the inventory Phase 8 will draw on. No Learning Center work is done here, and
> **Phase 8 is not started.**

---

## 0. The constraint that shapes all of this

**Do not build an LMS.** A Learning Center here means: the right explanation
available at the moment a person has to make a decision, drawn from the same
definitions the software uses. Content that restates the vocabulary in its own
words will drift from the code within one release, and a training page that
disagrees with the screen is worse than no training page.

The existing `app/Tefca/learning_content.py` already imports its vocabulary from
the live enums for exactly this reason. Phase 8 should extend that pattern, not
replace it.

---

## 1. Report procedures

**What Phase 8 needs to teach:** which report answers which contractual
question, who may generate it, and what makes it deliverable.

| Source | Where |
| --- | --- |
| The ten deliverables, quoted to solicitation paragraph | `docs/phase7_contract_reporting_matrix.md` §3 |
| Which report family lives on which API path | same, §5 |
| The twelve release conditions | `docs/OFFICIAL_FINDING_RELEASE_GATE.md` |
| Worked examples, rendered | `docs/development_examples/` |

**The specific misconception to design against:** that generating a report makes
its contents a finding. It does not. Generation is a read; delivery is governed
by the release gate, and six of its twelve conditions are currently outside
AGT's control.

**Carried from Phase 7:** the canonical path (`/api/reports/*`) and the
contract-aligned path (`/api/tefca/reports/*`) are still different paths. Phase 8
content must say which one a given deliverable comes from, and must not present
the split as settled — it is a known consolidation item.

---

## 2. Analyst instructions

**Source:** `docs/deliverables/TEFCA_ARC_Analyst_SOP_DRAFT.md`;
triage vocabulary from `app/Tefca/exception_triage.py`.

What an analyst has to understand before their first determination:

1. **An observation is not a finding.** Triage sorts work; it never decides.
2. **The five triage dispositions and what each one is asking of them** —
   `READY_FOR_ANALYST`, `METHODOLOGY_PENDING`, `INFORMATIONAL_ONLY`,
   `SOURCE_LIMITATION`, `DUPLICATE_CONSOLIDATED`. Only the first is work.
3. **A rationale is part of the determination.** A determination nobody else can
   evaluate cannot be QA'd, which means it cannot become reportable.
4. **`METHODOLOGY_PENDING` is not a backlog to clear.** It is a question for the
   COR. Deciding one of those items individually would manufacture the
   methodology decision the item exists to flag.
5. **A source that could not answer says nothing about the entity.**

**Live numbers to teach against** (development data): of 188,528 observations,
**28** are `READY_FOR_ANALYST`, **33,992** are `METHODOLOGY_PENDING`, **154,499**
are `INFORMATIONAL_ONLY` and **9** are `SOURCE_LIMITATION`. The ratio is the
lesson: the overwhelming majority of observations are not analyst work, and an
analyst who treats the queue as "everything the system produced" will drown in
items that were never theirs.

---

## 3. QA instructions

**Source:** `docs/deliverables/TEFCA_ARC_QA_SOP_DRAFT.md`; behaviour from
`app/tefca_registry/qa_gate.py`; traversals from `tests/test_phase7_pilot.py`.

1. **Approve, Return, Escalate — three different outcomes**, not three degrees of
   the same one. Escalate is not a slow approve.
2. **Return preserves the determination.** It is not an edit and not a deletion.
3. **Approval is not permanent.** A later return revokes it.
4. **Supersession, not overwriting**, when an approved determination must change.
5. **You cannot approve your own work.** The system enforces it; the training
   should explain *why* — an approval by the author is not independent evidence
   of anything.

**The five pilot cases in `tests/test_phase7_pilot.py` are ready-made worked
examples.** They are synthetic, clearly labelled `PILOT-DEV-*`, and each
demonstrates exactly one outcome. Phase 8 can narrate them directly rather than
inventing new scenarios.

---

## 4. Priority review procedure

**Sources:** solicitation ¶146–¶147; `docs/phase7_contract_reporting_matrix.md`
D5.1; `app/Tefca/reporting.py::generate_priority_status_report`.

The five required content elements, straight from ¶147 — identified issue, root
cause if determined, severity or impact, recommendations to prevent recurrence,
resolution.

**Two things Phase 8 must get right, because both are easy to state wrongly:**

- **There is no fixed SLA.** ¶146 says the deadline "will be communicated by the
  COR", per request. Training that teaches a standing turnaround target would be
  teaching an invented requirement.
- **Urgency changes the sequence, not the standard.** A priority review still
  needs an analyst determination and independent QA.

---

## 5. Methodology explanation

**Source:** `docs/deliverables/TEFCA_ARC_Methodology_for_COR.md` — written in
program language, deliberately free of table names, class names and the internal
layer terminology.

Phase 8 should treat that document as the register for *all* outward-facing
explanation, including staff-facing content. Staff who learn the internal
vocabulary first tend to use it with the COR.

**Label discipline to carry into every module:** the four discrepancy categories
are **Government-defined** (solicitation ¶136, ¶137, ¶142). **B1–B4 is AGT
shorthand** and must never be presented as a TEFCA, ONC, ASTP, RCE or Sequoia
classification. `app/Tefca/learning_content.py` already carries a list of
prohibited conclusions; this belongs on it if it is not there already.

---

## 6. Source descriptions

**Source:** `docs/deliverables/TEFCA_ARC_Methodology_for_COR.md` §3;
`docs/CONNECTOR_DEPENDENCY_MATRIX.md`.

Per source, a learner needs: what it is authoritative *for*, what it is **not**
authoritative for, how current it is, and what its silence means.

**The distinction that causes the most misreading:** NPPES and PECOS publish
**practice locations**; the Government's list carries a **registered address**.
These can legitimately differ for a fully compliant organisation. Any module
touching addresses has to establish that before showing a single conflict count.

**Known limitation to state plainly:** SAM.gov has no credential, so it is
recorded as unavailable across the whole development population. Training must
show unavailability as a fact about the lookup, never about the entity.

---

## 7. Report interpretation

What a reader must be able to do with a report in front of them:

1. **Find the classification banner** and know what it means.
2. **Read the provenance table** — source fingerprint, evidence version, cycle,
   payload hash — and know that a report without them is not answerable.
3. **Tell an observation from a determination.** Reports mark the difference;
   readers have to notice it.
4. **Not add up figures that are different quantities.** The address numbers are
   the standing example: **8,584** NPPES conflict observations, **1,842** PPEF,
   **10,426** observations in total, **9,032** distinct entities of which
   **1,394** conflict on both sources. Every one of those has been quoted as "the
   number of address problems" at some point. Only one of them answers any given
   question.

---

## 8. Common exceptions

Ranked by how often they occur in the development population, which is a
reasonable proxy for what staff will meet first.

| Exception | Development volume | What a learner needs to know |
| --- | --- | --- |
| Address difference | 10,426 observations / 9,032 entities | Registered address vs practice location. Awaiting the materiality decision. **Not a failure.** |
| Source unavailable | Whole population (SAM.gov) | A fact about the lookup. Never about the entity. |
| Insufficient data to compare | 6,940 | Different from "they disagree". Collapsing the two overstates problems. |
| Identity not resolvable | 28 | The genuine analyst queue. |
| Multiple matches | within the 28 | Ambiguity is a reason to stop, not to pick. |

---

## 9. What Phase 8 must NOT inherit as settled

Stated so Phase 8 does not build training around something still open:

| Item | Status |
| --- | --- |
| The nine COR decisions (D1–D9) | **All pending.** Do not teach a resolution. |
| Delivery format per deliverable | **Open** (D9 / matrix F1). Do not teach "reports are PDFs". |
| Records retention period | **Open** (D8 / matrix F3). |
| Report path consolidation | **Not executed.** Two paths still serve reports. |
| Tagged-PDF accessibility | **Not achieved.** See `docs/phase7_certification.md` §3. |
| Durable object storage | **Not implemented.** Reports persist in the database. |

---

## 10. What Phase 8 does not need to build

Already complete and re-usable as-is:

- `app/core/learning/framework.py` — the delivery mechanism
- `app/Tefca/learning_content.py` — 7 modules, 26 glossary terms, 8 help
  surfaces, 22 prohibited conclusions, vocabulary imported from live enums
- `docs/phase8_learning_center_inventory.md` — the content inventory
- `tests/test_learning_center.py` — including the test that content cannot drift
  from the enums

Phase 8's job is to extend this with the Phase 7 material above, not to rebuild
it.
