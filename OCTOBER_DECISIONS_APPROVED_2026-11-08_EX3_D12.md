# EX-3 and D-12 decisions — owner approval record

Record ID: OCTOBER-OWNER-APPROVAL-2026-11-08-03.
Decision received: 2026-11-08, ~01:4x EST (America/New_York; UTC−05:00 —
~06:4x UTC), in the running Claude Code session ("Proceed with the
review-only scope above. Record EX-3 Option B and D-12 assessment date
2026-11-08 with their approval provenance.").
Approver: Imran Siddiqui, President & CEO, Alliance Global Tech Inc. —
owner direction stated in the conversation.
Separate documentary COR concurrence: NOT ATTACHED.
Independent QA approval: NOT RECORDED (reviewer still UNASSIGNED).
Recorded by: Claude Code, from the owner's direct instruction. This records
the owner's stated authority and instruction; it is not independent
verification of a contractual appointment.
Companion records (all preserved): OCTOBER-OWNER-APPROVAL-2026-11-07-01
(commit 1da7efe) and OCTOBER-OWNER-APPROVAL-2026-11-08-02 (D-01 superseding).

## EX-3 — DECIDED: Option B

An unresolved SAM exclusion candidate classifies **B3** with reason
EXCLUSION_POSSIBLE_MATCH_PENDING; a CONFIRMED applicable exclusion
(analyst-adjudicated identity and scope) classifies **B4**. The two Treatment
overlaps (October lines 23274 and 25058) retain B4 through the owner-adopted
Treatment rule. Implementation: component `exclusion_candidate_b3` + producer
flag `RCE_PROPOSED_EXCLUSION_STATES` (both inactive until Gate-C activation
under this record). Measured preview delta on evidence-complete inputs:
28 × B4(RULE-005) → 26 × B3(RULE-008) + 2 × B4(RULE-006 owner).

## D-12 — DECIDED: current assessment, assessment date 2026-11-08

The October classification run is a CURRENT assessment of the October
submission with the explicit assessment date **2026-11-08**. Original source
and evidence dates are PRESERVED as their own facts, never replaced by the
assessment date: snapshot/population date 2026-10-14 (filename-derived, as
recorded); SOP effective 2026-08-03; §7 compliance 2026-09-03; software gates
2026-10-25 and 2026-10-26; each source outcome carries its own
edition/timestamp (e.g. SAM extract edition 2026-11-07; NPPES/LEIE/PECOS
evidence 2026-10-25→2026-11-08) and is later corroboration, never
October-14 source status. Execution: `RCE_PROPOSED_ASSESSMENT_AS_OF=2026-11-08`
(or the equivalent run parameter) at Gate-C activation under this record;
the run row's assessment_metadata records the date and its source.

## Scope

This record permits recording the decisions and the separately authorized
review-only actions (marking #204/#205 ready for substantive review). It does
not authorize merge, DEV migration, deployment, activation, flag changes,
SAM persistence, classification or report submission.
