# D-01 superseding decision — owner-adopted Treatment classification policy

Record ID: OCTOBER-OWNER-APPROVAL-2026-11-08-02 (supersedes the D-01 row of
OCTOBER-OWNER-APPROVAL-2026-11-07-01 ONLY; every other row of that record —
including M-1, whose reference remains commit 1da7efe — stands).
Decision received: 2026-11-07, ~23:5x EST (America/New_York; UTC−05:00) =
2026-11-08 ~04:5x UTC, in the running Claude Code session; register entry
written 2026-11-08 ~00:1x EST (05:1x UTC) and amended with this provenance
2026-11-08 ~01:0x EST.
Approver: Imran Siddiqui, President & CEO, Alliance Global Tech Inc. —
OWNER DIRECTION, stated in the conversation.
Separate documentary COR concurrence: NOT ATTACHED.
Independent QA approval: NOT RECORDED.
Recorded by: Claude Code, from the owner's direct pasted instruction ("OWNER
CORRECTION — SUPERSEDES D-01 CONFIG A ONLY", then the Step 2C addendum
confirming it). This records the owner's stated authority and instruction;
it is not independent verification of a contractual appointment.
Superseded entry (preserved, linked): OCTOBER_DECISIONS_APPROVED_2026-11-07.md
(OCTOBER-OWNER-APPROVAL-2026-11-07-01, commit 1da7efe) — its D-01 CONFIG A
row is history; no earlier entry is overwritten.

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

Component `treatment_b4_owner_adopted` (a NEW VERSION of RULE-006 itself,
priority 6 — the historical initiating-gated RULE-006 definition is retained
as the prior version and RULE-007 is left unselected — consuming the
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
