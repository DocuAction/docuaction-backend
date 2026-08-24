# TEFCA ARC — Analyst Standard Operating Procedure

**DRAFT — NOT FOR COR RELEASE** · Version 0.1 · 2026-08-23
Methodology `arc-methodology-0.1` · Evidence `phase6-bulk-1.1.0`

Companion to `docs/TEFCA_USER_OPERATIONS_GUIDE.md`, which covers logging in,
dashboards and navigation. This SOP covers only the review act itself.

---

## The one rule that governs everything below

**You produce a determination. You do not produce a reportable finding.** A
finding becomes reportable only when a QA lead — a different person — records
APPROVE. The system refuses a self-approval.

## Procedure

### 1. Open the assigned exception
Work the queue in priority order. An older item of equal priority is not less
urgent than a newer one.

### 2. Verify entity identity
Confirm the cited evidence belongs to **this** entity by **organisation OID**.

> TEFCAID is **not unique** — 43 TEFCAIDs are shared across up to 69 records.
> Matching on TEFCAID can attach evidence to the wrong organisation. HCID is
> near-unique and also unsafe as a key.

### 3. Verify the source was applicable
Check the applicability recorded on each observation. `NOT_APPLICABLE` means the
lookup could not be keyed — nothing was asked, so nothing was answered.

### 4. Review the observation
Read the Layer-1 state precisely. `MATCH_OBSERVED`, `NO_MATCH_OBSERVED`,
`MULTIPLE_MATCHES`, `AMBIGUOUS`, `SOURCE_UNAVAILABLE`, `LOOKUP_NOT_APPLICABLE`,
`INSUFFICIENT_IDENTIFIER`, `ERROR`. The last four are never adverse.

### 5. Review the source evidence
Open the cited values. For an address, read the **normalised** values —
formatting differences were already excluded.

### 6. Review provenance
Confirm the source edition and its hash. If an observation cites an edition you
cannot locate, stop and raise it; do not determine on evidence you cannot open.

### 7. Review related evidence
An entity may carry evidence from several sources. An OIG hit and a CMS
revocation are two things to adjudicate, not one.

### 8. Check methodology
If the condition is `METHODOLOGY_PENDING`, **do not determine it**. Record that
it is blocked and on which decision. Determining it anyway invents the
methodology.

### 9. Check source limitations
SAM.gov is unevaluated for the whole population. PPEF publishes no street line.
Neither is a fact about the entity.

### 10. Record your rationale
Mandatory, minimum ten characters, and it must address the evidence cited. "Looks
fine" is not a rationale.

### 11. Record the determination
CONFIRM or RECLASSIFY. A revision is a **new** event referencing the one it
supersedes; nothing is overwritten.

### 12. Submit to QA
You cannot approve it yourself.

---

## Worked examples

### CMS Revoked match
*Observed:* `CMS_REVOCATION` `MATCH_OBSERVED` on NPI, 22 such records.
*Do:* confirm the NPI belongs to this OID; open the revocation row; check whether
the revoked enrolment is the one the delivery references; state in the rationale
what the match establishes and what it does not.
*Do not:* conclude the organisation is barred from TEFCA. Revoked Medicare
billing privileges and TEFCA participation are different questions.

### OIG exclusion, NPI match
*Observed:* `OIG_LEIE` `MATCH_OBSERVED`, 1 record.
*Do:* verify the NPI, open the LEIE row, note the exclusion type and date.
*Do not:* treat it as settled without checking the NPI is genuinely this entity's.

### OIG exclusion, name-only (AMBIGUOUS)
*Observed:* `OIG_LEIE` `AMBIGUOUS`, 2 records. The business name matched; there
was no NPI to corroborate.
*Do:* treat as a name collision until proven otherwise. Look for any independent
corroboration.
*Do not:* record an exclusion. Most LEIE individual rows carry `0000000000`, so
name-only matching is exactly where false positives arise. **An exclusion
asserted against a named organisation on a name collision is a false accusation.**

### NPPES identity anomaly
*Observed:* `NPPES` `NO_MATCH_OBSERVED` (2 records) or `MULTIPLE_MATCHES` (1).
*Do:* confirm the delivered NPI is well-formed; record that NPPES could not
resolve it.
*Do not:* conclude the entity does not exist, or that the NPI is invalid. NPPES
non-resolution is an identity observation; D1 governs its classification and is
**PENDING COR DECISION**.

### Address discrepancy
*Observed:* `dimension_disposition = CONFLICT`. 10,426 observations, 9,032 records.
*Do:* note it and stop. These are `METHODOLOGY_PENDING` on
`D4_ADDRESS_MATERIALITY`.
*Do not:* call the address wrong, invalid, inaccurate, non-compliant or failed.
The delivery supplies a **registered** address; NPPES and PPEF publish **practice
locations**. A difference may be entirely proper.

---

## Prohibited conclusions

| Do not say | Because |
| --- | --- |
| "Address is wrong / entity failed" | `D4_ADDRESS_MATERIALITY` is undecided |
| "SAM shows nothing, so the entity is clear / not registered" | The source did not answer |
| "No PPEF record, so no Medicare enrolment" | If `NOT_APPLICABLE`, nothing was asked |
| "Name matched LEIE, so excluded" | Name-only is `AMBIGUOUS` |
| "I reviewed it, so it is reportable" | Only a QA APPROVE makes it reportable |
