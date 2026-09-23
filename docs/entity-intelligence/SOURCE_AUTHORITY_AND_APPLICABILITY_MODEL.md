# Source authority and applicability model

Authority is **contextual and descriptive**. There is no universal numeric score, no ranking and no vote; a test swaps the authority class of a conflicting source through every value and the assessment does not change. What differs between classes is *what question each can answer*.

## Authority classes (`SourceAuthority`)

| Class | Answers | Cannot answer | Examples |
|---|---|---|---|
| PROGRAM_DELIVERY | what ONC/RCE delivered (the subject) | anything about itself | the 41-field delivery |
| RCE_GOVERNING_MATERIAL | what TEFCA rules apply and when | any fact about an entity | Common Agreement 2.1, SOPs |
| FEDERAL_REGISTRY (federal identity reference) | NPI enumeration, legal business name, other names with type codes, practice locations as published | licensure, credentialing, Medicare enrollment, TEFCA eligibility, contractual compliance | NPPES V2 |
| FEDERAL_PROGRAM_ENROLLMENT | what the applicable current CMS public enrollment dataset reports for a linked enrollment | historical enrollment, street-level location (PPEF), TEFCA anything, licensure | CMS PPEF; hospital/FQHC/RHC/hospice enrollment files |
| FEDERAL_EXCLUSION_OR_INTEGRITY | whether an exclusion/revocation record exists for an identifier | identity, location, enrollment | OIG LEIE, SAM exclusions, CMS revocation list |
| RCE_PROVIDED_THIRD_PARTY | what a third-party file delivered through the RCE states | anything until its terms and layout are known | expected IQVIA OneKey file |
| COMMERCIAL_REFERENCE | licensed commercial statements | federal or program facts | (none today) |
| SUPPLEMENTAL | address-quality or web corroboration | organisational identity | Google Address Validation (research only), websites |
| STATE_REGISTRY | legal entity registration facts per jurisdiction | practice locations, federal enrollment | (design only) |
| DOCUACTION_HISTORICAL | what DocuAction observed before | anything new | prior observations |
| PRIOR_HUMAN_DETERMINATION | that a decision was recorded, and its reference | nothing for the rules — echoed only | analyst/QA record |

## Applicability (`EvidenceApplicability`) — aligned to the methodology draft §5

REQUIRED · APPLICABLE · CONDITIONALLY_APPLICABLE (needs a key from a prior lookup, e.g. PPEF needs an NPI) · NOT_APPLICABLE (absence is expected and carries no meaning) · UNKNOWN (pending methodology). Applicability is decided per entity before any lookup and recorded on the observation; the comparison engine echoes it in PARTICIPATION_EVIDENCE_NOT_FOUND explanations.

## Absence (`AbsenceReason`)

NOT_APPLICABLE · NOT_IN_POPULATION · SOURCE_LIMITATION · IDENTIFIER_NOT_AVAILABLE · NOT_FOUND · DATA_ISSUE. An absence is an observation with a reason and a dataset version; every reason maps to an INSUFFICIENT-class signal, never to a conflict (tested for each reason).

```
NO CMS ENROLLMENT RECORD  != NOT ENROLLED
NO NPPES RECORD           != NOT A PROVIDER
NO LEIE RECORD            != CLEARED (it is "no exclusion record found in the edition consulted")
SOURCE UNAVAILABLE        != NOT FOUND
```

## Effective dates and editions

Every observation carries `source_delivery_id` (edition), `observed_at` (the source's own date where published), `effective_from/to` where the source states them, and provenance with file hash and retrieval time. A newer edition that repeats a statement produces an evidence-side UNCHANGED delta scoped PROGRAM_ENROLLMENT / RELATIONSHIP / EVIDENCE, never a subject delta. "Current dataset" and "historical evidence" are therefore distinguishable in the record.

## Identifier linkage

Two sources are only ever brought together through the identifier the delivery carries (NPI for NPPES and PPEF). The multi-source name-variation note is produced only when each source resolved that identifier uniquely (tested with a differing NPI and with multiple candidates, both suppressed). No source is linked to another by name similarity.

## Tiering (see the executive decision document)

Tiering describes *how the program should sequence sources*, not weight. Tier 1 today: program delivery, NPPES V2, DocuAction history/prior review. Tier 2: CMS public enrollment evidence (already used by the frozen Task 3 path) and the RCE-provided IQVIA file once delivered. Tier 3: address-quality evidence (USPS-certified or Google, if ever approved). Future/program-dependent: state registries, LEIE beyond the existing Task 3 screening, SAM (blocked on a credential decision), licensure.
