# Authoritative Source Matrix

| Source | Purpose | Applies to | Key | Obtained | Match logic | Refresh | Unavailable treatment | Limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **COR entity data** | Subject of review | All | Organisation identifier | Submitted directory record | Not a corroborating source | Per delivery | Review cannot proceed | Delivered via Box on assignment |
| **NPPES** | Identity, name, practice location, taxonomy | Entities with an NPI | NPI | Legal name, address, taxonomy, entity type | Exact NPI | Monthly | Recorded unavailable | An NPI evidences enumeration, not licensure |
| **PECOS / PPEF** | Medicare enrolment and relationships | NPI + Medicare relevance | NPI → enrolment id | Enrolment, practice location, reassignment, specialty | Exact NPI | Quarterly | Recorded unavailable | Publishes no street line; no payment-suspension field |
| **OIG LEIE** | Exclusion | All identifiable entities | NPI; business name fallback | Exclusion type, date, address | NPI decisive; **name-only is ambiguous, never an exclusion** | ~Monthly | Recorded unavailable | Most individual records carry a placeholder NPI |
| **CMS Revocation** | Revoked Medicare billing privileges | NPI + Medicare relevance | NPI | Revocation record | Exact NPI | Quarterly | Recorded unavailable | Revocation is not itself a TEFCA finding |
| **SAM.gov** | Federal registration and debarment | All | UEI / name | — | — | — | **Currently unavailable — credential required** | Unevaluated until credentialed |

**A source that did not answer has said nothing about the entity.** Unavailability
is disclosed in the report and is never recorded as a clearance, a match, or a
discrepancy.

All verification sources are publicly available. AGT does not use
government-provided data for source validation.
