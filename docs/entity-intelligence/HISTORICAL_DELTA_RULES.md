# Historical delta rules — WHAT CHANGED (DELTA_RULES_VERSION 1.0)

Implemented in `app/core/entity_intelligence/delta.py`.

## Slotting

Observations are grouped into slots of (source, observation type, role). Prior and current slots are compared by normalised content. `unavailable` observations are excluded.

| Situation | Delta |
|---|---|
| no prior observations at all | NEW_ENTITY |
| same normalised values | UNCHANGED (one per value) |
| single-valued slot, value differs | NAME_CHANGED / ADDRESS_CHANGED / IDENTIFIER_CHANGED / RELATIONSHIP_CHANGED |
| multi-valued slot | NEW_VALUE per added member, REMOVED_VALUE per missing member, UNCHANGED per retained member |
| value now stated by a different source | reported as two deltas under each source (SOURCE_CHANGED reserved for a future explicit mapping) |

Each delta carries before/after display values, the prior and current observation ids, and `subject` — True when the delta concerns the PROGRAM_DELIVERY (the entity as delivered), False when it concerns what an evidence source says. Evidence-side deltas are reported but never "explained": a change in NPPES is a fact for the analyst, not a variation of the delivered identity.

## Explainable variation

For subject deltas, the current comparison signal for the same dimension decides:

| Delta | Comparison signal | Variation |
|---|---|---|
| NAME_CHANGED / NEW_VALUE / REMOVED_VALUE (name) | DBA_MATCH_IDENTIFIED, OTHER_NAME_MATCH, FORMER_NAME_MATCH, DIRECT/NORMALIZED match | EXPLAINABLE_VARIATION_SIGNAL |
| ADDRESS_CHANGED (location) | ADDITIONAL_PRACTICE_LOCATION_MATCH, MAILING_LOCATION_MATCH, PRIMARY/NORMALIZED match | EXPLAINABLE_LOCATION_VARIATION_SIGNAL |
| any subject change without such a signal | UNEXPLAINED_VARIATION (basis names the signal that was present) |
| UNCHANGED, NEW_ENTITY, evidence-side | NOT_APPLICABLE |

These are system observations. "Explainable" means *the available evidence is consistent with the change*; it is not a determination that the change is acceptable.

## Task 4 use (future)

Current-vs-prior delivery comparison: run the delta over PROGRAM_DELIVERY observations from the two deliveries plus the current evidence set; surface subject deltas with their variation signal and the prior analyst determination / QA result alongside (the program supplies those; Core does not read review tables).


## Scope and wording (2026-09-12)

Every delta carries `scope`: **DELIVERED_VALUE** (the program delivery changed), **EVIDENCE** (a source's statement changed), **SOURCE_VERSION** (same statement, newer edition; designed, produced by the acquisition job), NONE. `PRIOR_DECISION_EXISTS` is answered by the run (`prior_decision_exists`, `prior_review_reference`) and is never a delta.

Wording is careful by construction: templates say "The delivered organisation address changed between the compared observations … This records a change in what was stated, not a real-world event." and "The source-stated legal business name changed …". Tests forbid "moved", "relocated", "closed", "opened", "renamed" in any delta explanation. Only subject (DELIVERED_VALUE) deltas are analysed for explainable/unexplained variation; evidence-side deltas remain NOT_APPLICABLE.


## Scope refinement (2026-09-12)

`DeltaScope` now: DELIVERED_VALUE · EVIDENCE · PROGRAM_ENROLLMENT · RELATIONSHIP · SOURCE_VERSION · RULE_VERSION · PRIOR_HUMAN_DECISION · NONE. `compute_deltas` assigns DELIVERED_VALUE to subject deltas and PROGRAM_ENROLLMENT / RELATIONSHIP / EVIDENCE to evidence-side deltas by observation type. RULE_VERSION and PRIOR_HUMAN_DECISION are vocabulary for the integration layer (policy register, review record) and are never produced from observations. Example from the brief: prior delivered "ABC Healthcare LLC", current delivered "ABC Mobile Clinic", NPPES current DBA "ABC Mobile Clinic" → DELIVERED_VALUE NAME_CHANGED with "not a real-world event" wording and an EXPLAINABLE_VARIATION_SIGNAL from the DBA comparison; the words "changed its legal name" never appear (tested).
