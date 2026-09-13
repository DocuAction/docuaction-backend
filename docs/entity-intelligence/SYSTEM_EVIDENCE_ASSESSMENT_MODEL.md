# System Evidence Assessment model (ASSESSMENT_RULES_VERSION 1.0)

Implemented in `app/core/entity_intelligence/assessment.py`. The term is **System Evidence Assessment**; never "verdict".

## States

EVIDENCE_CORROBORATES · EVIDENCE_PARTIALLY_CORROBORATES · EXPLAINABLE_VARIATION_IDENTIFIED · CONFLICTING_EVIDENCE · INSUFFICIENT_EVIDENCE · SOURCE_UNAVAILABLE

Forbidden as outputs (asserted at import and in tests): COMPLIANT, NON_COMPLIANT, APPROVED, REJECTED, PASS, FAIL, VERDICT, DETERMINATION.

## Rules, in order (no arithmetic, no voting)

1. Every consulted source unavailable → SOURCE_UNAVAILABLE.
2. Any conflict signal in any dimension → CONFLICTING_EVIDENCE. One authoritative conflict is enough; agreement elsewhere is not a counter-vote.
3. Any dimension explained only by a non-primary record (DBA / other name / former name / additional or mailing location), or any subject delta with an explainable-variation signal → EXPLAINABLE_VARIATION_IDENTIFIED.
4. Every dimension corroborates and no unexplained subject delta → EVIDENCE_CORROBORATES.
5. At least one dimension corroborates; the rest insufficient / ambiguous / unavailable → EVIDENCE_PARTIALLY_CORROBORATES.
6. Otherwise → INSUFFICIENT_EVIDENCE.

## Payload

`assessment`, `basis` (the signals in rule order), `comparisons[]`, `deltas[]`, `open_questions[]` (what still requires a person, one line per non-corroborating dimension or unexplained delta), `requires_human_review = true`, rules versions, and a note: "System evidence assessment. Not a determination, not a contractual category, not a verdict."

## Position in the workflow

```
SOURCE DATA → INDEPENDENT EVIDENCE → SYSTEM EVIDENCE ASSESSMENT → ANALYST REVIEW → ANALYST DETERMINATION → INDEPENDENT QA → CONTRACTUAL CLASSIFICATION
```

The assessment is an input to the analyst. It cannot place an entity in a Government category, cannot change a Task 3/4/5 workflow, and is not read by any report.

## Preserved on every source observation

source · source date (observed_at / effective dates where the source publishes them) · source authority · applicability · role · provenance (owner, delivery path, received-by, version, hash, record reference, parser version). Conflicting evidence stays visible; nothing is suppressed by agreement elsewhere.


## Additions (2026-09-12)

- Participation signals join the rule sets: PARTICIPATION_OBSERVED is corroborating; PARTICIPATION_CONFLICT is a conflict; PARTICIPATION_EVIDENCE_NOT_FOUND, INSUFFICIENT_PARTICIPATION_EVIDENCE and PARTICIPATION_NOT_COMPARABLE are insufficient-class (never adverse); SOURCE_UNAVAILABLE as before.
- `cross_source_notes[]`: the MULTI-SOURCE NAME VARIATION CORROBORATION note (template `MULTI_SOURCE_NAME_VARIATION_CORROBORATION`) appears only under EXPLAINABLE_VARIATION_IDENTIFIED and only when every explaining source resolved the same delivered identifier uniquely. Tested with a differing NPI in the second source and with multiple candidates: no note.
- Unchanged: no arithmetic, no voting; one conflict from any source dominates; authority is never a weight.
