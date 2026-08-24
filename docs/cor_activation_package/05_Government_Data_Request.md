# Request for the Authorised Entity Population

**TEFCA ARC · Contract 7571MN26F80064 · Alliance Global Tech, Inc.**
Prepared for the COR · 2026-08-24

---

## Purpose

AGT is ready to begin Task 3 review operations. Two Government actions are
required first: the assignment authorising the review, and delivery of the
authorised entity population.

This document states precisely what AGT needs so the delivery can be made once,
correctly, without a round trip.

**AGT has imported no entity data and will import none before the assignment is
issued.**

---

## 1. What is requested

| | |
| --- | --- |
| **Item** | The authorised population of Participants and Subparticipants for review under Task 3 |
| **Purpose** | To draw the contractual sample and perform the retrospective review |
| **Authority to release** | Contracting Officer's Representative |
| **Recipient** | AGT Program Manager, for loading into the controlled review environment |

---

## 2. Transfer mechanism — confirmation required

> **CONFIRMATION REQUESTED — T1.** AGT requests the COR confirm the transfer
> mechanism.

The solicitation does not specify one. AGT's weekly progress reports have
recorded the expectation of **secure encrypted transfer**, and delivery has been
linked to finalisation of the **HHS Data Access Agreement**.

AGT can receive the file by any of the following and has no preference beyond
the Government's own policy:

| Option | AGT readiness |
| --- | --- |
| HHS-provided secure file transfer or managed file transfer service | Ready |
| Government-hosted secure repository to which AGT is granted access | Ready |
| Encrypted attachment by secure email, with the passphrase sent separately | Ready |

In every case AGT applies FIPS 140-validated encryption at rest and in transit,
consistent with the contract's encryption requirement.

**AGT will not name a specific commercial platform as the agreed mechanism
unless the Government directs it.** Any mechanism the Government designates is
acceptable.

> **CONFIRMATION REQUESTED — T2.** Is finalisation of the HHS Data Access
> Agreement a precondition to delivery, and if so what remains outstanding on
> it? AGT's contract personnel have signed the HHS Data Access Agreement and
> non-disclosure agreements.

---

## 3. Expected content

AGT can work with whatever the Government holds. The fields below are what the
review consumes; **absence of any of them is a data-quality observation, not a
blocker**, and AGT will report rather than infer.

### Required for review to be meaningful

| Field | Why it is needed |
| --- | --- |
| Entity name | The subject of the review; the primary comparison value |
| Entity type | Participant or Subparticipant — determines applicable checks |
| QHIN attribution | The stratification basis required by the contract |

### Strongly preferred

| Field | Why it is needed |
| --- | --- |
| Provider identifier (NPI), where the entity has one | The decisive key for NPPES and CMS corroboration. Without it, corroboration relies on name and address, which is materially weaker. |
| Registered address — street, city, state, postal code | Address comparison; jurisdiction |
| Unique Entity Identifier (UEI), where held | SAM.gov corroboration once credentialed |
| Date of onboarding or designation | Establishes whether an entity is a new entrant for Task 4 |

### Useful if available

| Field | Why it is needed |
| --- | --- |
| A stable record identifier assigned by the Government | Lets AGT reference an entity in a report without reproducing its name |
| Parent or affiliated organisation | Explains legitimate differences between related entities |
| Exchange purposes / participation role | Supports the role-consistency check |
| Point of contact | Only if the Government intends AGT to make enquiries; **AGT will not contact any entity without written direction** |

**Format:** any delimited text or spreadsheet format. AGT records the file
exactly as received and does not alter it.

---

## 4. Population and version identification

So that a report issued months later can state precisely what it covered, AGT
asks that the delivery be accompanied by:

| | |
| --- | --- |
| **Population label** | A name or version the Government recognises |
| **Effective date** | The date the extract represents, which may differ from the send date |
| **Record count** | The number of entities the Government believes it contains — AGT's control total, checked on receipt |
| **Source of record** | Which system or register the extract was taken from |
| **Scope statement** | Whether it is the complete population or a defined subset |

On receipt AGT records the file unaltered, computes an integrity value, confirms
the record count against the Government's stated total, and **reports any
discrepancy to the COR before beginning work**.

---

## 5. Updates and replacements

> **CONFIRMATION REQUESTED — T3.** How should AGT treat a subsequent delivery?

The retrospective review assesses a population as at a point in time. A later
extract raises a question the Government should answer:

| Situation | AGT proposal |
| --- | --- |
| A **correction** to the same population | Record as a new version; continue the review against the original unless the COR directs otherwise; disclose the correction in the next progress report |
| A **refreshed** population for a later period | Treat as a distinct population with its own sample |
| A **partial** or supplementary delivery | Record separately; do not merge into the original without direction |

**AGT does not overwrite a delivered population.** Every delivery is retained,
so any report can state which version it rests on.

---

## 6. What AGT will do on receipt

| When | Action |
| --- | --- |
| On receipt | Record the file unaltered; compute and record its integrity value; confirm the record count against the Government's control total |
| Within 1 business day | Confirm receipt to the COR, with the integrity value and any variance from the control total |
| Within 3 business days | Provide the stratified sampling frame by QHIN for COR review |
| Before any review begins | Obtain COR confirmation of the sampling frame and parameters |
| Then | Begin verification under the accepted methodology |

**No review begins before the sampling frame is shared and the parameters are
confirmed.**

---

## 7. Points of contact

| Role | Name |
| --- | --- |
| AGT Program Manager | Nabeel Ashraf |
| AGT delivery point of contact | To be confirmed with the COR |
| Government authority to release | Contracting Officer's Representative |

---

## 8. Confirmations requested

| ID | Question |
| --- | --- |
| **T1** | What transfer mechanism should be used? |
| **T2** | Is the HHS Data Access Agreement a precondition, and what remains outstanding? |
| **T3** | How should subsequent deliveries be treated? |
| **T4** | Is the population the same as the figure cited in the solicitation Q&A, or does the delivered file define it? |
| **T5** | Are there entities the Government wishes excluded from review? |

Answers to T1 and T2 are what unblock delivery. The remainder can be settled at
or shortly after the methodology review meeting.
