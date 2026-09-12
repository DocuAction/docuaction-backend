# Controlled terminology — Entity Identity & Location Intelligence

Words the capability uses, words it must never use, and the distinctions it must keep. Tests enforce the machine vocabulary (`assessment.py` forbidden terms, template safety tests); this page governs prose, documentation and any future UI copy.

## Words we use

| Term | Meaning | Never confused with |
|---|---|---|
| **Program delivery** | Data delivered by ONC/RCE about an entity; the SUBJECT under review | evidence |
| **Evidence observation** | One statement by one source about one entity, with provenance | a fact about the entity |
| **Source authority** | Descriptive class of who made the statement (federal registry, state registry, RCE-provided third party, commercial, supplemental, DocuAction historical, prior human determination) | a weight or rank |
| **Corroborates** | An independent source states the same value | "confirms", "verifies", "validates" |
| **Explainable variation** | The delivered value differs from the primary record but an available source record relates the two (DBA, former name, additional location) | a match |
| **Conflict** | An authoritative source states a different value and no record relates them | error, fraud, non-compliance |
| **Insufficient evidence** | Not enough observations to compare | a negative finding |
| **Source unavailable** | The source could not be consulted | "no match", "not found" |
| **Ambiguous** | Candidates exist but the evidence cannot single one out | match or conflict |
| **System Evidence Assessment** | The engine's closed-vocabulary summary of the comparisons and deltas | determination, verdict, category |
| **Delivered value changed** | A value in the program delivery differs between two deliveries | "the organisation moved/renamed" |
| **Evidence changed** | A source's statement differs between two editions | a change in the world |
| **Prior decision exists** | A recorded analyst/QA outcome is referenced for the analyst | an input to the rules |
| **Human review required** | Every output; the engine never finishes a case | a flag on some outputs |

## Words we do not use (for outputs, explanations, documentation of results)

COMPLIANT · NON-COMPLIANT · APPROVED · REJECTED · PASS · FAIL · VERDICT · DETERMINATION (except "analyst determination", which belongs to the analyst) · VALID/INVALID entity · LEGITIMATE · FRAUD · LEGAL IDENTITY · CREDENTIALED · LICENSED · OPERATING / NOT OPERATING · CLOSED · MOVED · RENAMED · SCORE · CONFIDENCE · PROBABILITY · SIMILARITY (as an identity claim) · "THE SYSTEM DECIDED".

## Distinctions that must hold

```
UNKNOWN                      != FALSE
MISSING                      != CONFLICT
SOURCE UNAVAILABLE           != NO MATCH
NO NPPES ADDITIONAL LOCATION != INVALID LOCATION
NO IQVIA RECORD              != INVALID ENTITY
NO GOOGLE RESULT             != INVALID ADDRESS
NORMALIZED SIMILARITY        != IDENTITY PROOF
DBA (source-stated)          != DBA (inferred)      — the second does not exist here
CORPORATE RELATIONSHIP       != PROGRAM RELATIONSHIP
DELIVERED VALUE CHANGED      != ORGANISATION CHANGED
PRIOR DECISION EXISTS        != PRIOR DECISION APPLIES
25,000 SOURCE RECORDS        != 25,000 HUMAN REVIEWS
```

## Sentence patterns

- Preferred: "The delivered organisation address changed from Baltimore to Frederick between the compared deliveries."
- Not: "The organisation moved to Frederick."
- Preferred: "Available NPPES evidence associates the delivered name with an Other Name record classified as Doing Business As."
- Not: "The delivered name is the organisation's DBA."
- Preferred: "NPPES_V2 could not be consulted; this is a fact about access, not about the entity."
- Not: "No NPPES record found."

## Spelling

Documentation uses the program's US spelling for controlled terms that appear in code (`ORGANIZATION_IDENTITY`) and either spelling in prose; enum values are never changed for spelling.


## Additional invariants (2026-09-12)

```
NOT FOUND                    != NON-COMPLIANT
NPI FOUND                    != CREDENTIALED
CMS ENROLLMENT FOUND         != TEFCA ELIGIBLE
ADDRESS FOUND                != ORGANIZATION OPERATING THERE TODAY
RELATIONSHIP FOUND           != TEFCA RELATIONSHIP
NO CMS ENROLLMENT RECORD     != NOT ENROLLED
RCE POLICY                   != FACTUAL IDENTITY      (rules are not evidence about an entity)
PROPOSED / UNDER CONSIDERATION != REQUIRED
RCE REQUIREMENT              != ARC CONTRACT REQUIREMENT
```

Preferred sentence for CMS evidence: "The applicable current CMS public enrollment dataset (edition) contains this practice-location observation for the linked enrollment." Not: "PECOS proves this organization has always operated at this address."
