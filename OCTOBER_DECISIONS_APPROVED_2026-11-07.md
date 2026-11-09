# October classification decisions — owner approval record

Record ID: OCTOBER-OWNER-APPROVAL-2026-11-07-01
Decision date: 2026-11-07 (America/New_York)
Approver: Imran Ahmed Siddiqui, identifying himself in the conversation as owner and COR.
Evidence: The assistant presented the six recommendations below, explicitly as recommendations awaiting approval. The owner responded: "i am core and owner I can approved one step completed you start your work".
Recorded by: Codex, from that direct instruction. This records the user's stated authority and instruction; it is not independent verification of a contractual appointment.

## Approved decisions

| ID | Approved direction |
| --- | --- |
| D-01 | CONFIG A. Treatment missing-submitted-NPI cases receive B3 review/potential-B4 reasons, subject to HELD eligibility, supported applicability rebuttals and higher-priority approved conditions. Treatment B4 remains disabled pending separate approval. The 709 population is accounted for, not unconditionally assigned one bucket. |
| D-02 | Distinguish a structurally invalid single NPI, multiple NPIs in one cell, and a valid-format identifier with identity concerns. A multiple-NPI cell must not automatically become B4. This approval establishes the distinctions; new bucket mappings remain inactive until explicitly approved. |
| D-03 | An applicable mandatory check in FAILED, NOT_CHECKED or UNAVAILABLE prevents B1/B2. A completed mismatch or unresolved candidate is not an acceptable clean outcome either, with a specific reason rather than a false incomplete-check label. Higher-priority approved disqualifiers retain precedence. |
| D-15 | Skip currently HELD/re-HELD records during verification/classification with a recorded reason. Preserve historical evidence and classifications. No hold release or erasure of prior results. |
| M-1 | Require attributable SAM screening for B1 within the adopted applicability policy. The authorized official extract method is accepted, subject to source edition, scope and date limitations. Candidates remain separate from confirmed exclusions. |
| M-4 | Incomplete applicable mandatory evidence routes to B3 with a named reason, unless an established higher-priority approved B4 condition applies. |

## Execution scope and remaining gates

This approval permits recording these decisions and continuing the authorized inactive engineering, tests and local evidence replay.

It does not authorize merge, DEV migration, deployment, activation, DEV flags, population processing, new external lookups, hold release, analyst adjudication or external submission. PROD is excluded.

No independent QA approval or reviewer assignment is recorded here.
D-12 assessment-date selection, EX-3 implementation/activation approval, D-04/D-05 settings and other unmentioned decisions are not resolved by this record.
Do not request these same six approved directions again unless a material incompatibility requires a specific additional decision.

## Implementation checkpoint

Repository: DocuAction/docuaction-backend
PR: #205
Inspected head: 42a52c50cb9df53d5100a248c490de887265646c
File: app/tefca_registry/bucket_classifier.py

At the inspected head, SEED_RULES_V5 derives from v4 and ensure_rules_v5 inserts all v5 specifications. A direct activation therefore includes Treatment RULE-006 and is incompatible with D-01 CONFIG A unless the separate gating/composition work changes that path.

This is a source-code finding, not evidence of activation in DEV.
Before activation, verify the final reviewed code implements CONFIG A and the approved mandatory-source policy, and complete the outstanding release and QA gates.
