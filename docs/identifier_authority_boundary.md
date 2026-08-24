# Identifier Authority Boundary — what each identifier proves, and where AGT's authority stops

**Classification:** INTERNAL ENGINEERING / COR-SHAREABLE · 2026-08-24
**Contract:** 7571MN26F80064 · TEFCA ARC
**Implementation:** `app/Tefca/identifier_boundary.py` · boundary version `1.0.0`
**Tests:** `tests/test_source_applicability.py`, `tests/test_evidence_vocabulary.py`

---

## The confusion this exists to prevent

An NPI that resolves cleanly in NPPES *feels* like the organisation has been
verified. It has not been.

An NPI establishes that a provider identifier exists and which organisation CMS
associates with it. It says nothing whatsoever about the entity's taxpayer
identity. The two get conflated constantly, because in ordinary speech both are
"the organisation's number" — and a reviewer who conflates them will write
"verified" in a report that supports no such claim.

Confirming that a TIN, EIN or FEIN belongs to a named organisation requires
**IRS authority**. AGT does not hold it.

---

## Why this is permanent, not a backlog item

This is the part most often misread as a gap waiting to be closed. It is not:

- **There is no public IRS API** that verifies a for-profit entity.
- **IRS TEOS covers only tax-exempt organisations** — a subset that does not
  span the delivered population.
- **IRS data is keyed on EIN**, which the delivered records do not carry.

So the boundary is not a connector nobody has built yet. There is nothing to
build. AGT has no authority to confirm a TIN and **will not acquire one under
this contract**. Any plan, schedule or report implying that TIN verification is
forthcoming is wrong.

This is categorically different from the SAM.gov gap, and the two should never
be described together. SAM.gov is a source AGT *may* query and currently lacks a
credential for — a solvable access problem. The IRS boundary is a source AGT
*may not* query at all.

---

## The authorities

### Contractor-verifiable

| | **NPI** — National Provider Identifier |
| --- | --- |
| Authority | CMS / NPPES |
| **Establishes** | That the identifier exists and is well formed · which organisation CMS associates with it · the registered address CMS holds · taxonomy and enumeration type |
| **Does NOT establish** | Taxpayer identity (TIN/EIN/FEIN) · legal corporate registration · tax-exempt status · that the organisation is the one the delivered record intended |

| | **UEI** — Unique Entity Identifier |
| --- | --- |
| Authority | GSA / SAM.gov |
| **Establishes** | Federal registration status, *when SAM.gov can be queried* · debarment and exclusion status recorded by GSA |
| **Does NOT establish** | Taxpayer identity · clinical or provider credentials |
| Access | Requires a SAM.gov credential. Until one is issued, SAM.gov answers are recorded as `SOURCE_UNAVAILABLE` — a fact about the lookup, **never** about the entity. |

### Government-restricted — `TIN`, `EIN`, `FEIN`

| | |
| --- | --- |
| Authority | Internal Revenue Service |
| **Establishes** | Taxpayer identity, **when confirmed by the IRS** |
| **Does NOT establish** | Anything at all when unconfirmed. An unverified TIN is a string. |
| Contractor-verifiable | **No**, permanently |

---

## The four rules

For any entity whose verification would require restricted Government access:

1. It **MUST NOT** become `PASS` because some other identifier matched.
2. It **MUST NOT** become `FAIL` because AGT lacks IRS access.
3. It **MUST NOT** become `NO_MATCH` — nothing was asked, so nothing was not
   found.
4. It **MUST** remain explicitly unresolved, pending Government verification.

Rule 2 is the one with teeth. **The absence of AGT's authority is never reported
as an adverse finding against an entity.** An organisation does not become
suspect because its contractor was not permitted to ask a question.

---

## How it is represented

`government_verification_state(identifier)` is the only sanctioned
representation, and it returns the same three-layer combination every time —
because the answer does not depend on the entity, it depends on who AGT is:

| Layer | Value |
| --- | --- |
| Applicability | `PENDING_GOVERNMENT_VERIFICATION` |
| Layer 1 — observation | `LOOKUP_NOT_APPLICABLE` |
| Layer 3 — disposition | `INSUFFICIENT_EVIDENCE` |
| `is_resolved` | **Always `False`.** That is the point. |
| `is_adverse` | **Always `False`.** Absence of access is not evidence against anyone. |

The returned `BoundaryState` carries every layer at once, deliberately, so a
caller cannot pick up the applicability and drop the disposition.

Calling it with a contractor-verifiable identifier **raises**. Using this state
where a lookup could have been performed would hide a lookup that should have
happened — the inverse failure, and a worse one.

### Two vocabulary choices worth defending

**Why `LOOKUP_NOT_APPLICABLE` and not `SOURCE_UNAVAILABLE`.**
`SOURCE_UNAVAILABLE` means a source AGT may query did not answer. It is
transient and it invites a retry. This is neither: the lookup is not one AGT may
perform at all, and no retry will ever change that. Recording it as
`SOURCE_UNAVAILABLE` would leave a standing implication that the gap is being
worked.

**Why `PENDING_GOVERNMENT_VERIFICATION` was added rather than reusing
`NOT_APPLICABLE`.** `NOT_APPLICABLE` means "asking is meaningless for this
entity". This case is "asking is meaningful, and AGT is not permitted to ask".
Recording the second as the first would tell a reader the question does not
matter. It matters.

That is **one member added to an existing enum** — not a new vocabulary layer.
The five-layer vocabulary already carried the rest: Layer 1 has
`INSUFFICIENT_IDENTIFIER` for "we lacked the key" and `LOOKUP_NOT_APPLICABLE`
for "this lookup does not apply"; Layer 3 has `INSUFFICIENT_EVIDENCE`, which is
neither a pass nor a failure.

---

## What reports must say

`boundary_disclosure()` supplies the disclosure. **It is included whether or not
any entity in the population happened to carry a TIN**, because the limitation
is on AGT's authority, not on the data that arrived.

> NPI verification and TIN/EIN/FEIN verification are not equivalent. An NPI that
> resolves in NPPES establishes the provider identifier and the organisation CMS
> associates with it; it establishes nothing about taxpayer identity. AGT holds
> no authority to confirm a TIN, EIN or FEIN, and the absence of that authority
> is never reported as an adverse finding against an entity.

### Prohibited in any report or determination

- Reporting an entity as **verified** because its NPI matched.
- Reporting an entity **adversely** because AGT lacks IRS access.
- Recording a restricted lookup as `NO_MATCH_OBSERVED`.
- Recording a restricted lookup as `SOURCE_UNAVAILABLE`, which implies a retry
  would help.

---

## For analysts and QA reviewers

**Analyst.** If an entity's open question is taxpayer identity, that question is
not yours to close. Record the boundary state and say so in the rationale. Do
not write "unable to verify" without saying *who* would have to verify it — the
distinction between "AGT could not confirm this" and "this could not be
confirmed" is the difference between a limitation and an accusation.

**QA reviewer.** Checklist item **C3 — no unsupported conclusion** is where this
usually surfaces. The specific failure to look for: a determination resting on
an NPI match while the rationale speaks about the *organisation* rather than the
*provider identifier*. That is the conflation, wearing the clothes of a clean
verification.

**Both.** A restricted lookup is not a defect in the evidence and not a reason
to escalate. It is a correctly recorded limit on what a contractor may
establish.

---

## Related

| Document | Purpose |
| --- | --- |
| `evidence_vocabulary_design.md` | The five-layer vocabulary this fits into |
| `tefca_evidence_dimension_mapping.md` | Dimension-level mapping |
| `post_certification_operational_readiness.md` Part O | The SAM.gov gap — a different kind of limitation |
| `government_activation_runbook.md` | Where source limitations must be re-checked before reporting |
| `cor_activation_package/04_Decision_Package_D1_D9.md` | D4, the related methodology decision |
