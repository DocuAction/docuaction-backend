# D-01 superseding decision — owner-adopted Treatment classification policy

Record ID: OCTOBER-OWNER-APPROVAL-2026-11-08-02 (supersedes the D-01 row of
OCTOBER-OWNER-APPROVAL-2026-11-07-01 ONLY; every other row of that record
stands).
Decision date: 2026-11-08.
Approver: Imran Siddiqui, identifying himself in the conversation as owner and
COR.
Recorded by: Claude Code, from the owner's direct instruction ("OWNER
CORRECTION — SUPERSEDES D-01 CONFIG A ONLY", 2026-11-08). This records the
owner's stated authority and instruction; it is not independent verification
of a contractual appointment.

## Superseding D-01 direction (owner's adopted classification policy)

> IS_ACTIVE = 1
> AND purposesofuse contains the exact token T-TRTMNT, in any combination
> AND submitted NPI is blank
> → B4.
>
> Node type does not gate this owner-adopted rule. Supplemental NPPES, IQVIA
> or vetting NPIs must not fill the submitted blank or suppress the
> condition. Keep inactive records separate. HELD records remain held and
> explicitly unclassified; do not release them. Preserve higher-priority
> approved conditions. Do not force a B4 count of 709: reconcile HELD and
> higher-priority outcomes. Label this as the owner's adopted classification
> policy, not proof that every record independently establishes an SOP breach.

## History preserved

The earlier D-01 CONFIG A approval (B3 review / potential-B4 reasons,
Treatment B4 disabled) remains on the record as history: file
`OCTOBER_DECISIONS_APPROVED_2026-11-07.md`, GitHub commit `1da7efe`. It is
superseded, not overwritten.

## Implementation (inactive)

Component `treatment_b4_owner_adopted` (RULE-009, priority 6, consuming the
producer-emitted signal `treatment_owner_adopted_missing_npi`), declared
mutually exclusive with the CONFIG-A components `treatment_review_b3` /
`treatment_b4` — one treatment regime at a time, enforced by
`compose()`/`activate_rule_configuration`. RULE-005 (exclusions, priority 5)
retains precedence; HELD records are excluded upstream under the approved
D-15 direction and listed with reasons.

## Scope

This record permits recording the decision and continuing the authorized
inactive engineering, tests and local evidence replay. It does not authorize
merge, DEV migration, deployment, activation, DEV flags, population
processing, hold release or external submission. No independent QA approval
or reviewer assignment is recorded here.
