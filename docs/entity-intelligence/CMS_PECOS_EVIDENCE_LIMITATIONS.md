# CMS / PECOS public enrollment evidence — limitations, encoded

What a match in a CMS public enrollment dataset means, and what it does not. These limitations determine the evidence language the engine is allowed to use.

## Precisely what a match means

"An enrollment record in the applicable current CMS public enrollment dataset (edition X) carries this NPI / this ORG_NAME / this locality." PPEF is "a subset of Provider Enrollment, Chain, and Ownership System (PECOS) data" (CMS fact sheet) containing "providers who are actively approved to bill Medicare or have completed the 855O at the time the data was pulled". The match is a statement about that extract on its date.

## Limitations and their encoding

| Limitation (source) | Effect on evidence | Encoding in the model |
|---|---|---|
| Current approved enrollments only; historical enrollment information is not in the public files (PPEF methodology; NBER keeps vintages but CMS does not publish history) | A match says "enrolled as of the extract"; absence says nothing about the past; a prior-quarter match that disappears is an EVIDENCE/PROGRAM_ENROLLMENT delta, not a revocation | `observed_at` and `source_delivery_id` on every observation; deltas scoped PROGRAM_ENROLLMENT; language "the applicable current CMS public enrollment dataset (Q3 2026) contains …" |
| "not intended to be used as real time reporting as the data changes from day to day and the files are updated only on a quarterly basis" (PPEF methodology) | Weeks-old at best; unsuitable for asserting today's status | never say "currently enrolled"; say "in the <edition> extract" |
| Multiple NPIs may occur (MULTIPLE_NPI_FLAG; Additional NPIs sub-file) and multiple enrollments per provider are normal (methodology §8: 2,433 records matched more than one) | Multiplicity is not ambiguity of identity; it is the data's shape | MULTIPLE_CANDIDATE_ENTITIES only when the *identifier* resolves to different entities; multiple enrollments of one PAC ID are recorded as multiple observations |
| Practice locations are enrollment-level relationships, and not all enrollment scenarios contain practice locations | An enrollment without a location is not "no location" | absence with reason SOURCE_LIMITATION / NOT_APPLICABLE |
| PPEF practice-location file publishes city/state/ZIP only (repo `address_comparison.py`; methodology §23) | Street-level agreement cannot be assessed against PPEF | LOCATION comparison against PPEF yields at most AMBIGUOUS_LOCATION (locality) — never PRIMARY_LOCATION_MATCH; role `CMS_ENROLLMENT_LOCATION` |
| Some data with known quality issues may be omitted (PPEF methodology) | Absence may be a data issue | AbsenceReason.DATA_ISSUE available; never "not enrolled" |
| No publisher-issued row ids in PPEF (methodology §23) | Citations use a deterministic content key | `source_record_ref` = deterministic row key + component + edition hash (as the frozen store already does) |
| PPEF carries no DBA; provider-type files do | DBA corroboration only for hospital/FQHC/RHC/hospice populations | NameKind.DOING_BUSINESS_AS only from a source that labels it |
| PPEF carries no payment-suspension field (repo FR-T3-010) | Cannot be reported | never fabricated; not an EI concern |
| PECOS_ASCT_CNTL_ID "maps closely to a Social Security Number for an individual or an EIN for an organization" (data dictionary) | Near the EIN/TIN exclusion | opaque link key only; never displayed as tax identity; never compared to a TIN |
| Enrollment ≠ licensure, credentialing, TEFCA eligibility, contractual compliance | A match is not a status | PARTICIPATION_OBSERVED template disclaims; banned-claims test |
| The RCE accepts a link to the PPEF listing as Tier 2 vetting evidence only until 2026-12-31 | A PPEF listing is time-boxed evidence for the RCE, and no evidence at all for the ARC contract unless the methodology says so | policy register entry RCE.XP_VETTING.TIER2_CMS_DIRECTORY with effective_to 2027-01-01 |

## Language

BAD: "PECOS proves this organization has always operated at this address."
GOOD: "The applicable current CMS public enrollment dataset (PPEF Q3 2026, practice-location sub-file) contains a practice-location observation in Baltimore, MD 21201 for enrollment I2026…, which is linked to the delivered NPI. The file publishes no street line; street-level agreement cannot be assessed from this source."

BAD: "Not enrolled in Medicare."
GOOD: "The applicable current CMS public enrollment dataset contains no enrollment observation for the delivered NPI (reason: NOT_FOUND; applicability: CONDITIONALLY_APPLICABLE — Medicare relevance depends on taxonomy). Absence in this dataset is a statement about the dataset, not about the entity's enrollment status."

## What absence can mean (methodology §5 and §8, restated)

not applicable (entity type not enrollable) · not in population (e.g. non-billing organisation) · source limitation (omitted for quality; no location for this enrollment scenario) · different identifier (the enrollment is under another NPI — check Additional NPIs) · not enrolled · data issue. The engine prefers CMS_ENROLLMENT_EVIDENCE_NOT_FOUND (signal PARTICIPATION_EVIDENCE_NOT_FOUND) with the reason and applicability attached. Human review required.
