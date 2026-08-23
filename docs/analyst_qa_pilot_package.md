# TEFCA ARC — Five-Case Analyst and QA Pilot Package

**Classification:** INTERNAL OPERATIONS · 2026-08-23 · Evidence `phase6-bulk-1.1.0`

Five cases selected from **existing evidence**. Nothing was fabricated to create a
case. Only 5 of the 28 analyst-ready items are assigned; the remaining 23 wait
until the pilot has run.

**No determination and no QA decision is supplied here.** The whole point of the
pilot is that humans produce those. `review_decision_events` currently holds 0
rows and must still hold 0 when this package is handed over.

---

## Case selection

| Case | Category | Entity | Area-1 line |
| --- | --- | --- | --- |
| 1 | Clean / normal identity | MI - Allergy Asthma and Pulmonary Center | 11,927 |
| 2 | NPPES identity discrepancy | El Dorado Clinic, P.A. *(held, not promoted)* | 12,684 |
| 3 | Exclusion match | FL - The Pain Management Institute LLC | 11,189 |
| 4 | Address discrepancy | AA - Humana PCO | 11,174 |
| 5 | Ambiguous exclusion | Family Medical Clinic | 1,504 |

All five categories had a genuine evidence-backed case; no substitution was needed.

---

## CASE 1 — clean / normal identity

| | |
| --- | --- |
| Entity | MI - Allergy Asthma and Pulmonary Center · Subparticipant |
| Organisation OID | `2.16.840.1.113883.3.564.8366` |
| Delivered NPI | 1790921138 |
| Delivered address | 3216 ROCHESTER RD, ROYAL OAK, MI 48073 |
| Trigger | NPPES `MATCH_OBSERVED`, address `EXACT_MATCH` |
| Source edition | npidata 2026-08-09 |
| Triage | INFORMATIONAL_ONLY |

**Inspect:** that the NPI resolves to one NPPES record; that the OID — not the
TEFCAID — is what links the evidence; that every applicable source answered.

**Do not assume:** that a clean record is "verified". It is a record against which
no adverse observation was made. Absence of a discrepancy is not proof of accuracy.

**Permitted actions:** record a determination with rationale; submit to QA.
**Escalate if:** any cited source edition cannot be opened.
**Reportability:** none until a QA APPROVE.

---

## CASE 2 — NPPES identity discrepancy (and a held record)

| | |
| --- | --- |
| Entity | El Dorado Clinic, P.A. · Subparticipant · **not promoted** |
| Delivered NPI | `1780787176, 1770559767` — **two NPIs in one field** |
| Delivered address | 700 W. Central, Suite 205, El Dorado, KS 67042 |
| Trigger | NPPES `MULTIPLE_MATCHES` — "2 NPIs on one record matched NPPES" |
| Curated status | **HELD** — 1 unresolved HIGH issue, `NPI-002` |
| Triage | READY_FOR_ANALYST (priority 80) |

This is one of the four records the system refused to promote. It did not choose
an NPI; it stopped.

**Inspect:** both NPIs in NPPES; whether they are the same organisation, a parent
and subsidiary, or unrelated; what the delivered name and address support.

**Do not assume:** that either NPI is "the" NPI; that a multi-valued cell is a
typo; that the record is invalid. **D6** (identifier quality states) is
**PENDING COR DECISION** — you may describe what you observe, not classify it.

**Permitted actions:** record what each NPI resolves to; recommend a correction
route. **Escalate if:** the two NPIs resolve to different organisations — that is
a delivery-integrity question above analyst level.
**Reportability:** none. The record is held and unpromoted.

---

## CASE 3 — exclusion match on NPI

| | |
| --- | --- |
| Entity | FL - The Pain Management Institute LLC · Subparticipant |
| Organisation OID | `2.16.840.1.113883.3.564.3306` |
| Delivered NPI | 1639333859 |
| Delivered address | 601 JENNINGS AVENUE, EUSTIS, FL 32726 |
| Trigger | OIG LEIE `MATCH_OBSERVED` — matched on NPI |
| Source edition | LEIE UPDATED 2026-08-10 |
| Triage | READY_FOR_ANALYST (priority 100) |

The single highest-consequence observation in the population.

**Inspect:** the LEIE row — exclusion type, date, address; whether the NPI is
genuinely this entity's; whether the LEIE business name corroborates.

**Do not assume:** that an NPI match is conclusive without checking the NPI
belongs to this OID; that exclusion from federal healthcare programmes equates to
a TEFCA participation finding — those are different questions, and **D7** (whether
an exclusion match may become an automated finding) is **PENDING COR DECISION**.

**Permitted actions:** record the match, the corroboration you found, and a
determination. **Escalate if:** the exclusion is current and the entity is
actively exchanging — that is time-sensitive.
**Reportability:** none until a QA APPROVE.

---

## CASE 4 — address discrepancy

| | |
| --- | --- |
| Entity | AA - Humana PCO · Subparticipant |
| Organisation OID | `2.16.840.1.113883.3.564.32711` |
| Delivered NPI | 1780300319 |
| Delivered address | 6544 W. Thomas Road Suite 11, Phoenix, AZ 85033 |
| Trigger | NPPES address `CONFLICT` — **line, city and state all differ** |
| Triage | **METHODOLOGY_PENDING** — `D4_ADDRESS_MATERIALITY` |

A three-field conflict, which is at the strong end of the 8,584 NPPES conflicts.

**Inspect:** the normalised values on both sides; that formatting was already
excluded; whether the NPPES practice location is plausibly a different site of
the same organisation.

**Do not assume — and this is the one that matters:** do **not** call this wrong,
invalid, inaccurate, non-compliant or failed. The delivery supplies a
**registered** address; NPPES publishes a **practice location**. No approved rule
establishes when a difference between them is material.

**Permitted actions:** record the observation and that it is methodology-pending.
**Do not record a determination on the address itself.**
**Escalate if:** the difference suggests the record describes a different
organisation entirely — that is an identity question, not an address one.
**Reportability:** none, and none available until D4_ADDRESS_MATERIALITY is decided.

---

## CASE 5 — ambiguous exclusion (name-only)

| | |
| --- | --- |
| Entity | Family Medical Clinic · Subparticipant |
| Organisation OID | `1.2.840.114350.1.13.239.2.7.3.688884.100.12782` |
| Delivered NPI | 1467433888 |
| Delivered address | 1150 Niles Cortland Rd, NILES, OH 44446 |
| Trigger | OIG LEIE `AMBIGUOUS` — business-name match, **no NPI corroboration** |
| Triage | READY_FOR_ANALYST (priority 90) |

"Family Medical Clinic" is close to the most generic organisation name possible.
The system matched the name, found no NPI corroboration, and deliberately refused
to call it an exclusion.

**Inspect:** the LEIE row's address and state against the delivered ones; whether
the delivered NPI appears anywhere in LEIE; how many distinct organisations share
this name.

**Do not assume:** that a name match is an exclusion. Most LEIE individual rows
carry `0000000000`, so name-only matching is precisely where false positives
arise. **An exclusion asserted against a named organisation on a name collision
is a false accusation.**

**Permitted actions:** record what corroboration exists or does not; recommend
CONFIRM-as-not-matched or escalate. **Escalate if:** address and state align and
you cannot rule the match out.
**Reportability:** none until a QA APPROVE.

---

## QA instructions — all five cases

QA verifies the **determination**, not the entity.

| Check | Case-specific attention |
| --- | --- |
| **Evidence sufficiency** | Case 3: was the LEIE row actually opened? Case 5: was corroboration genuinely sought? |
| **Source applicability** | Case 2: PPEF is APPLICABLE despite the held status — the analyst must not treat "held" as "not applicable" |
| **Identity linkage** | All: the OID must be the link. A determination citing only TEFCAID is returned |
| **Methodology compliance** | Case 4: any determination on address materiality is an automatic **RETURN** |
| **Analyst reasoning** | Rationale must address the evidence cited, not restate the observation |
| **Source limitations** | All: SAM is unevaluated on every case; a determination implying SAM clearance is returned |
| **Reportability** | Only APPROVE sets it. A later RETURN or ESCALATE withdraws it |

**Segregation of duties is enforced by the system.** The analyst on a case cannot
QA it; the attempt is refused. An exception requires a grant from a different,
more senior person with a written reason, recorded permanently.

### Expected pilot outcome

Five analyst determinations and five QA decisions, each an appended
`review_decision_events` row. Expect at least one RETURN — a pilot in which
everything is approved first time has tested the happy path only.
