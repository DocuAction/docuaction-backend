# FCC Bulletin v1.0 — Production Readiness Gate

**Prepared:** 2026-07-07
**Status:** Criteria definition only. **No gate item is satisfied by this document.** Each must be verified in staging by AGT and marked with evidence before a production Go decision.

**Rule:** Production **GO** requires **every "Must" criterion** = PASS with evidence, **zero open Critical/High defects**, and sign-off from QA + Operations + Engineering. Any Must = FAIL/UNVERIFIED ⇒ **NO GO**.

---

## Gate criteria

| # | Criterion | Level | How verified | Status |
|---|---|---|---|---|
| G1 | Backend starts successfully (full boot + `init_store`) | Must | Staging boot logs; `/health` ok | ☐ Unverified |
| G2 | Frontend builds & loads | Must | `npm run build`; `/bulletin` renders | ☐ Unverified |
| G3 | Database tables present (2 base + 5 additive) | Must | `\dt bulletin_*` in staging | ☐ Unverified |
| G4 | Scheduler operational (if used for prod delivery) | Must* | `/health` scheduler status; timed run | ☐ Unverified |
| G5 | Collection succeeds (real FCC news) | Must | Op Test Plan Step 1 | ☐ Unverified |
| G6 | Deduplication / summaries / categories correct | Must | Op Test Plan Steps 2–4 | ☐ Unverified |
| G7 | Exports generated (Word/Excel/HTML) | Must | Op Test Plan Step 6 | ☐ Unverified |
| G8 | Email delivered to approved recipient | Must | Op Test Plan Step 7 | ☐ Unverified |
| G9 | Audit entries created (audit enabled) | Should | `/audit` returns events | ☐ Unverified |
| G10 | Instrumentation records runs/sources | Should | `/runs`, `/runs/{id}` | ☐ Unverified |
| G11 | Coverage behaves honestly (measured or pending; never fabricated) | Must | `/coverage-assurance` | ☐ Unverified |
| G12 | Feature flags validated (each on/off as intended) | Must | Activation Plan verifications | ☐ Unverified |
| G13 | Security enabled & enforced (auth on; 401/403 correct; token wiring) | Must (for exposed prod) | Checklist §L | ☐ Unverified |
| G14 | Rate limiting behaves (429 over cap) | Should | Checklist §N | ☐ Unverified |
| G15 | Accessibility reviewed (documented; no 508 certification claimed) | Should | A11y review notes | ☐ Unverified |
| G16 | No open Critical/High defect | Must | Defect Log review | ☐ Unverified |
| G17 | Performance within acceptable bounds (collection/export/page) | Should | Measured in staging | ☐ Unverified |
| G18 | Rollback rehearsed (flag-disable + code revert) | Must | Dry-run rollback | ☐ Unverified |

\*G4 is a Must only if production delivery relies on the in-app scheduler.

---

## Decision record (to be completed by AGT)

- QA Lead sign-off: __________ Date: ______
- Operations sign-off: __________ Date: ______
- Engineering sign-off: __________ Date: ______
- **Gate decision:** ☐ GO ☐ GO WITH CONDITIONS ☐ NO GO
- Conditions / notes: ______________________________________________

---

## Explicit non-claims (until actually performed)
This gate does **not** assert: Production Certified, Section 508 Certified, Penetration Tested, or Coverage Verified. Those require the corresponding activities to be completed and evidenced.

*All statuses above are "Unverified" by definition in this document. Nothing has been executed.*
