# TEFCA ARC — QA Reviewer Standard Operating Procedure

**DRAFT — NOT FOR COR RELEASE** · Version 0.1 · 2026-08-23
Methodology `arc-methodology-0.1` · Evidence `phase6-bulk-1.1.0`

QA is the only gate that makes a determination reportable. This SOP maps
one-to-one onto the implemented `review_decision_events` workflow. **It does not
introduce a second approval mechanism** — there is exactly one, and it is the
decision event.

---

## What you are deciding

Not whether the entity is compliant. Whether the **determination** is supported
by the evidence cited, made under the correct methodology, and free of any
conclusion the evidence cannot carry.

## Your three actions

| Action | Effect | Requires |
| --- | --- | --- |
| **APPROVE** | The determination stands and becomes reportable | Rationale |
| **RETURN** | Back to the analyst | Rationale |
| **ESCALATE** | To a named individual | Rationale, recipient and escalation reason |

There is no fourth action. There is no override and no MODIFY — the model has
neither, deliberately.

## Reportability

`reportable_at` is set **only** by an APPROVE. A later RETURN or ESCALATE
**withdraws** reportability: the determination is back in play and must not be
cited as settled. Where an analyst issues a new determination after a RETURN, it
requires **fresh** QA approval; the earlier approval does not carry forward.

## Segregation of duties

You may not QA a determination you made. The system refuses.

An exception requires a grant from a **different, more senior** individual with a
written reason. Both are recorded permanently on the event and counted in
reconciliation. Your role is captured **as at the time of the decision**, so a
later role change cannot rewrite what a past decision was authorised under.

## Append-only history

A correction is a new event that **points at** the one it supersedes. The
superseded event keeps its author, timestamp and rationale forever. You are never
editing history; you are adding to it.

## Before you decide

Work `docs/deliverables/templates/05_QA_Review_Checklist.md`. Its sections:
subject and scope, evidence quality, methodology, analyst work, controls,
decision, release gates.

The three checks most often skipped:

- **C3** — no unsupported conclusion. Nothing described as failed,
  non-compliant, invalid, inaccurate or unverified without an approved rule.
- **C5** — observation counts are not entity counts.
- **A1** — the evidence belongs to this entity by organisation OID, not TEFCAID.

## When to RETURN

- The rationale does not address the evidence cited.
- A methodology-pending condition has been determined anyway.
- An unavailable source has been read as an adverse result.
- Evidence is cited from a superseded evidence version.
- The determination reaches further than the evidence supports.

## When to ESCALATE

- The evidence genuinely conflicts and the methodology does not resolve it.
- The determination would set a precedent the methodology has not established.
- You have a segregation concern you cannot resolve.

## Current state

43 review records · **0 QA-approved** · **0 decision events**. No finding is
reportable, and that is an accurate statement about the programme rather than a
gap in the system.

## What QA does not control

Report release. That is five separate gates, one of which — dataset contractual
provenance — is closed and cannot be opened by any QA action.
