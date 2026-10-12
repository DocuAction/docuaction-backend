# A2: screening states and unavailable-source handling (2026-10-08)

Stacked on draft PR #127 (SAM screening separation). Nothing here changes what B1 or B4 mean in active behavior.
All behavior changes are PROPOSALS behind flags that default OFF.

## Sources answered
- Task 2 (10_07_2026) "Indeterminate" row: a required source unavailable means the entity "cannot be classified until all sources are confirmed"; held, re-reviewed normally within 1 business day, escalated to the COR after 3 business days, alternative method where one exists.
- Task 2 "Important" paragraph: "No entity will be classified as No Discrepancy unless all required sources are successfully queried."
- Task 2 B4 row and "Analyst adjudication": B2/B3/B4 findings are human-reviewed before they are final.
- Review matrix conflicts 1 (no Indeterminate; B1 with SAM/PECOS unavailable), 2 (B4 from unconfirmed hits), 5 (registration and exclusion legs mixed); decision C4 (which sources are REQUIRED) is open with the COR.

## Implemented (additive, no policy)
| Item | Where | Flag |
|---|---|---|
| Four distinct states INCOMPLETE_SCREENING / NO_HIT / POTENTIAL_MATCH / ADJUDICATED_CONFIRMATION, per exclusion control and overall (precedence ADJUDICATED > POTENTIAL > INCOMPLETE > NO_HIT) | `app/tefca_registry/rce/screening_state.py` (pure) | recorded on the review record only when `ENABLE_SCREENING_STATE_RECORDING` is on (default OFF) |
| ADJUDICATED_CONFIRMATION requires analyst CONFIRM plus QA approval by a different person; no automated path emits it | `screening_state._adjudicated` | n/a |
| Documented unavailable-source handling (Indeterminate hold, 1 bd re-review, 3 bd COR escalation, alternative methods, required-source set OPEN) attached to any record with an incomplete control; marked `implemented_on_rce_path: false`, `timers_implemented: false` | `UNAVAILABLE_SOURCE_HANDLING` | with the recording flag |
| Classifier-input `sources.<x>.screening_leg` carried through when the SAM evidence says which leg answered | `arc_pipeline.dimensions_to_verification_results` | none; key absent for other sources; status/bucket unchanged (tested) |

NO_HIT is always reported with `no_hit_is_clearance: false`: it is scoped to the sources and date searched.

## Proposals (default OFF, NOT active policy)
| Proposal | Effect when on | Flag | Needs |
|---|---|---|---|
| A SAM registration lookup (NPI-less path, `screening_leg == registration_only`) is INCOMPLETE_SCREENING for the exclusion question; added to the exclusion-screening gaps on a B1 record | Recorded as a gap in `verification_claim`; `verified` is withheld only if `ENFORCE_COMPLETE_EXCLUSION_SCREENING` is also on | `SAM_REGISTRATION_ONLY_IS_INCOMPLETE_SCREENING` (needs recording flag) | Owner/COR decision (matrix conflict 5, decision C4) |

## Not done (needs a decision)
- No INDETERMINATE bucket and no change to B1/B4 assignment. `classification_bucket` is `String(2)`; an Indeterminate state needs COR decision C4 (required sources) and a schema/rule decision.
- No timers, queue or notification for 1 bd / 3 bd handling.
- Under the existing rules a B1 can still be assigned with SAM/PECOS unavailable; this PR makes that visible per record, it does not stop it.

## Tests
`tests/test_screening_state_a2_2026_10_08.py` (16, DB-free). Existing SAM, exclusion, prior-risk, verification-completeness suites unchanged and green.
