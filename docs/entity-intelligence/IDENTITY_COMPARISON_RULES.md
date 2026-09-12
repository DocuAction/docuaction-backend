# Identity comparison rules (COMPARISON_RULES_VERSION 1.0)

Implemented in `app/core/entity_intelligence/comparison.py`; explanations in `explanations.py`; normalisation in `normalize.py`. Every result: one dimension, one signal, the matched/candidate observation ids, a controlled explanation ending "Human review required.", `requires_human_review = True`.

## Principles

- The program delivery is the SUBJECT (`source_authority = PROGRAM_DELIVERY`); it never corroborates itself.
- Source observations stay separate; nothing is merged into a truth record.
- No voting: rules operate on signals per source; a conflict is reported even when other dimensions agree.
- Normalisation removes formatting only (case, punctuation, dotted abbreviations, suffix spellings, street-type abbreviations, ZIP5). It never equates different organisations.
- A role/kind is used only when the source stated it (NPPES type code 3 → DBA; reference-file row → additional practice location).

## Organisation identity (identifier)

| Signal | Rule |
|---|---|
| SOURCE_UNAVAILABLE | source recorded `{"unavailable": true}` |
| MISSING_IDENTIFIER | no delivered identifier, or source has no record for it |
| IDENTIFIER_CORROBORATED | exactly one source identifier observation equals the delivered value |
| MULTIPLE_CANDIDATE_ENTITIES | more than one equal |
| IDENTIFIER_CONFLICT | source has identifier observations, none equal |

## Name identity (evaluated in this order)

1. SOURCE_UNAVAILABLE · INSUFFICIENT_NAME_EVIDENCE (no delivered name / no source names)
2. DIRECT_NAME_MATCH — delivered == legal business name, exact
3. NORMALIZED_NAME_MATCH — equal after normalisation
4. Other-name match, signal by the SOURCE's kind: DBA_MATCH_IDENTIFIED (code 3) · FORMER_NAME_MATCH (code 4) · OTHER_NAME_MATCH (code 5/other). If the delivered name matches other-name records of different kinds → AMBIGUOUS_NAME.
5. AMBIGUOUS_NAME — same core words as the legal name, different organisational suffix (LLC vs INC): not a match.
6. NAME_CONFLICT — none of the above.

## Location identity

Usable address = first line + (city+state or ZIP5). Street key = line 1 + line 2 normalised, so a suite on line 2 equals a suite inside line 1.

| Signal | Rule |
|---|---|
| SOURCE_UNAVAILABLE / INSUFFICIENT_LOCATION_EVIDENCE | as above |
| PRIMARY_LOCATION_MATCH | street + ZIP5 equal to a PRIMARY_PRACTICE_LOCATION |
| ADDITIONAL_PRACTICE_LOCATION_MATCH | equal to an ADDITIONAL_PRACTICE_LOCATION (non-primary reference-file row) |
| MAILING_LOCATION_MATCH | equal only to the MAILING_LOCATION |
| NORMALIZED_LOCATION_MATCH | equal to a location of unstated role |
| AMBIGUOUS_LOCATION | same locality (city+state or ZIP5) but a different street |
| LOCATION_CONFLICT | no locality match |

When several roles carry the same address, the primary role wins the signal.

## Relationship identity

Only observations of the SAME relationship kind code are compared. A commercial CORPORATE_PARENT_HCO relationship against a delivered program relationship → RELATIONSHIP_NOT_COMPARABLE, never a corroboration or a conflict. Same kind: RELATIONSHIP_CORROBORATED when any related-entity name matches after normalisation; otherwise RELATIONSHIP_CONFLICT.

## Explain the difference (templates)

Example — delivered "ABC Mobile Clinic", NPPES legal "ABC Healthcare LLC", NPPES other name code 3 "ABC Mobile Clinic":
> The delivered organisation name differs from the NPPES_V2 legal business name (ABC Healthcare LLC). Available NPPES_V2 evidence associates the delivered name with an Other Name record classified as Doing Business As for the matched organisational NPI. Human review required.

Example — delivered Frederick, NPPES primary Baltimore, additional practice location Frederick:
> The delivered location differs from the NPPES_V2 primary practice location but corresponds to an available non-primary practice-location record for the matched NPI. Human review required.

Templates are deterministic; a missing fact renders "unstated". No language model is involved.
