# TEFCA ARC — Operating Runbook

**DRAFT — NOT FOR COR RELEASE** · Version 1.0 · 2026-08-23

The index to running the programme. It links the other documents rather than
restating them; anything explained elsewhere is referenced, not duplicated.

---

## Where everything is

| I need to… | Go to |
| --- | --- |
| Understand what the system does and does not decide | Learning Center module 1 · `TEFCA_ARC_Review_Methodology_DRAFT.md` §1–3 |
| Learn the evidence vocabulary | Learning Center module 3 · Methodology §6 |
| Work an exception | `TEFCA_ARC_Analyst_SOP_DRAFT.md` |
| Review a determination | `TEFCA_ARC_QA_SOP_DRAFT.md` · `templates/05_QA_Review_Checklist.md` |
| Run the day / the delivery / the week | `TEFCA_ARC_Operations_Playbook_DRAFT.md` |
| Produce a deliverable | `templates/02`–`09` |
| Find an open programme decision | `COR_Decision_Register.md` |
| Understand why a report says DRAFT | Methodology §25 · Learning Center module 6 |
| Look up a term | Learning Center glossary (26 terms) |
| Log in, navigate, troubleshoot | `docs/TEFCA_USER_OPERATIONS_GUIDE.md` |
| Deploy to production | `TEFCA_ARC_Production_Readiness_Checklist.md` |

## The chain, once

```
Government delivery
  → Area 1 (immutable: byte-for-byte file + one row per line)
  → parse → data quality → normalisation (Area 2 only)
  → canonical entity
  → source applicability (decided before any lookup)
  → authoritative source evidence
  → observation (Layer 1: what the source said)
  → triage (work assignment, never an answer)
  → analyst determination (rationale mandatory)
  → QA decision (APPROVE / RETURN / ESCALATE)
  → reportable finding
  → government deliverable (five release gates)
```

**Automation produces evidence and observations. It does not produce findings.**

## Current operating state

| | |
| --- | --- |
| Delivery | 23,566 records · `689472073480b1cc…` |
| Evidence | `phase6-bulk-1.1.0` · 188,528 observations · 116,218 hops |
| Analyst-ready | 28 items across 28 entities |
| Methodology-pending | 33,992 observations |
| QA-approved findings | **0** |
| Release | **DRAFT — NOT FOR COR RELEASE** (Gate 4 closed) |

## Standing constraints

1. Never call an address conflict a failure. `D4_ADDRESS_MATERIALITY` is open.
2. Never read SAM's silence as a result. No credential is configured.
3. Never match entities on TEFCAID. It is not unique.
4. Never quote an observation count as an entity count.
5. Never remove the DRAFT watermark.
6. Never edit a determination. Supersede it.

## Escalation

| Situation | Action |
| --- | --- |
| Artefact hash mismatch | Stop all reporting. Evidence chain is broken. |
| Schema fingerprint drift | Promotion halts automatically. Reconcile the field map. |
| A figure cannot be traced to a source row | Withhold the figure, not the disclosure. |
| Analyst and QA disagree | RETURN or ESCALATE. Never overwrite. |
| A methodology-pending condition blocks work | Record it; do not decide it. |

## First actions when operations begin

1. Assign the 28 analyst-ready items.
2. Confirm QA staffing and that analyst ≠ QA.
3. Pursue the four external prerequisites: dataset provenance, methodology
   approval, D-series decisions, SAM credentialing.
4. Perform the `docuaction_owner` transfer with its validation step before any
   production deployment.
