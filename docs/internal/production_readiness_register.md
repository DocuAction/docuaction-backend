# Production Readiness Register — INTERNAL

**AGT internal use. Not part of the COR package and not for external release.**
TEFCA ARC · Contract 7571MN26F80064 · 2026-08-24

---

## Why this is separate

The COR needs methodology, process, evidence sources and deliverables. The COR
does not need AGT's infrastructure backlog. Mixing the two invites questions
about matters that are AGT's to manage, and dilutes the decisions AGT actually
needs from the Government.

This register tracks everything between "certified on development data" and
"authorised to run production operations on Government data".

---

## Classification

| Class | Meaning |
| --- | --- |
| **COMPLETE** | Done and verified |
| **AGT ACTION** | AGT can close it without anyone else |
| **COR / GOVERNMENT INPUT** | Cannot be closed without a Government decision or grant |
| **PRE-PRODUCTION BLOCKER** | Must be closed before production operations on Government data |
| **POST-ACTIVATION IMPROVEMENT** | Genuinely can follow activation without risk |

An item can be both a blocker and dependent on Government input. Where it is,
both are stated.

---

## Register

| # | Item | Class | Status and next step |
| --- | --- | --- | --- |
| 1 | **`docuaction_owner` role creation and ownership transfer** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | Area-1 immutability currently rests on revoked privileges held by a role the application can authenticate as. An owner may grant back to itself. Moving ownership to a role the application cannot authenticate as closes this. Deployment step, not a code change. **Not performed; explicitly out of scope of every phase to date.** |
| 2 | **Azure artifact storage** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | No storage account is provisioned and the client libraries are deliberately absent. The adapter exists, is configuration-gated, and raises rather than pretending. Requires: provision the account, add the packages, implement and test against a real account. Local filesystem storage is operational and sufficient for development only. |
| 3 | **Linux PDF execution** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | The container image now installs the Pango/Cairo/GObject stack and fails the build if the engine cannot start; a CI workflow runs the rendering checks on Linux. **Neither has executed** — both require a push. Closing this needs one CI run. |
| 4 | **Manual Section 508 review** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | Automated structural checks pass. Keyboard operability, screen-reader walkthrough and PDF/UA validation have **not** been performed. No conformance is claimed anywhere, which is correct but is not a substitute. |
| 5 | **HHS 508 checklist per deliverable** | COR / GOVERNMENT INPUT | Decision D9. The contract requires delivered electronic content to meet HHS acceptance criteria and be accompanied by the appropriate checklist. AGT needs the format decision before preparing checklists. |
| 6 | **D8 retention period / WORM** | COR / GOVERNMENT INPUT | Retention metadata is recorded on every artifact; period is null; no irreversible lock is applied. This is deliberate — a WORM lock cannot be undone. The design applies an approved period without changing report semantics, proven by test. |
| 7 | **D9 deliverable format** | COR / GOVERNMENT INPUT | No file format is contractually specified. AGT proposes HTML plus PDF. |
| 8 | **SAM.gov credential and routing** | COR / GOVERNMENT INPUT · **affects every review** | **Corrected 2026-08-24.** A `SAM_GOV_API_KEY` configuration value **is present** in Azure DEV and PROD; the earlier "no credential held" was inaccurate. Operational validity and Entity Management authorization have **not** been independently validated — classification **UNDETERMINED**, and SAM.gov Entity Management is not to be represented as operational until separately proven. Every entity still carries a SAM.gov source limitation and the outcome is unchanged; only the stated reason was wrong. Certified evidence is unaffected (applicability reads no credential). Reviews can proceed with the limitation disclosed; they are weaker for it. See `production_readiness_verification_2026-08-24.md` §B1. |
| 9 | **Backup and restore rehearsal** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | A documented procedure exists. **It has not been rehearsed.** An untested restore is a hypothesis. Requires a scheduled rehearsal against a representative dataset with the recovery time recorded. |
| 10 | **Monitoring and alert routing** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | Configuration scripts exist. End-to-end alert delivery to a named on-call recipient has **not** been proven. Requires one deliberate test alert, received and acknowledged. |
| 11 | **Priority-review surge load test** | AGT ACTION · POST-ACTIVATION IMPROVEMENT | The contract anticipates twenty reviews per month with capability to exceed. The workflow is per-case and imposes no serialisation, but sustained throughput has not been measured. The real constraint is analyst and QA staffing, not the platform. Measure before committing to a surge figure. |
| 12 | **FIPS-199 categorisation memo** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | Required by the security clauses. Not drafted. |
| 13 | **NIST 800-53 control assessment** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | Required at the agreed impact level. An SSP exists, updated 20 July 2026; the control assessment against it does not. |
| 14 | **CUI marking and handling controls** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | E.O. 13556 obligations apply to handling, marking, safeguarding, transport, dissemination, reuse and disposal. A procedure is required; marking is not yet applied to deliverables. |
| 15 | **One-hour incident reporting path** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | The contract requires reporting discovered threats or hazards within one hour of discovery. A named path, named recipients and a tested escalation are required. Not established. |
| 16 | **HSPD-12 requirements** | COR / GOVERNMENT INPUT | Depends on whether contract personnel require Government credentials for the access model in use. Needs confirmation of applicability before AGT can act. |
| 17 | **Section 889 attestation** | AGT ACTION | Representation required. Confirm current attestation is on file with the Contracting Officer. |
| 18 | **Production deployment authorisation** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | No deployment has been performed and none is authorised. Gated on items 1, 2, 9, 10 and the security items above. |
| 19 | **PTA / PIA** | **PRE-PRODUCTION BLOCKER** · AGT ACTION | Privacy Threshold Analysis and, if triggered, Privacy Impact Assessment. Not drafted. |
| 20 | **HHS Data Access Agreement finalisation** | COR / GOVERNMENT INPUT | AGT contract personnel have signed. AGT's weekly reports link entity data delivery to finalisation. AGT has asked what remains outstanding. |

---

## Summary

| Class | Count |
| --- | --- |
| COMPLETE | 0 of the items above — everything listed is open by definition |
| AGT ACTION | 11 |
| COR / GOVERNMENT INPUT | 6 |
| **PRE-PRODUCTION BLOCKERS** | **12** |
| POST-ACTIVATION IMPROVEMENT | 1 |

**Nothing in this register blocks the COR methodology review, and nothing blocks
controlled operations on development data.** Items 1, 2, 9, 10 and the security
items block production operations on Government data.

---

## What is genuinely complete

Recorded so the register is not read as a list of everything undone:

- HHS Data Access Agreement signed by all AGT contract personnel
- Non-disclosure agreements complete
- System Security Plan drafted, updated 20 July 2026
- Platform training complete for contract personnel
- FIPS 140-validated encryption in transit and at rest
- Evidence immutability enforced at the database
- Analyst/QA segregation of duties enforced, including by database trigger
- Append-only decision history with no delete or override path
- Full regression: 2,102 passed, 56 skipped, 0 failed
- End-to-end certification on development data

---

## Suggested sequencing

**Before the COR meeting:** nothing. None of these is a prerequisite.

**Immediately after methodology acceptance:** items 1, 3 and 10 — each closable
in days and each a genuine gate.

**Before Government data arrives:** items 2, 9, 12, 14, 15, 19.

**Before the first Government-facing deliverable:** items 4, 5, 7.

**When measured, not before:** item 11.
