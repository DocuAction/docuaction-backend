# FCC Bulletin v1.0 — Risk Register

**Prepared:** 2026-07-07
**Status:** Living register. Reflects known risks as of pre-staging. **No risk is closed by deployment claims; nothing has been deployed.**

**Scales:** Impact = Low / Medium / High · Likelihood = Low / Medium / High · Status = Open / Mitigating / Accepted / Closed.
**Owners** are roles (AGT to assign names): Ops = Operations Lead, QA = QA Lead, Eng = Engineering, Sec = Security.

---

| ID | Description | Impact | Likelihood | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|
| R-01 | No staging env yet → functional flows unvalidated | High | High (now) | Stand up staging (Deployment Guide); run Operational Test Plan | Eng/Ops | Open |
| R-02 | Auth OFF by default → endpoints unauthenticated if enabled without token wiring verified | High | Medium | Enable `BULLETIN_AUTH_ENABLED` last; verify FE token + 401/403 in staging (Gate G13) | Sec/Eng | Open |
| R-03 | Full backend boot + DB unverified (only import tested) | Medium | Medium | Verify boot + `init_store` in staging (Gate G1/G3) | Eng | Open |
| R-04 | Coverage % unavailable until registry seeded + per-source failure capture built | Medium | High | Seed registry; scope failure instrumentation; keep honest "pending" until then | Eng/Ops | Open |
| R-05 | Delivery log table has no writer (delivery visibility incomplete) | Medium | High | Implement C3 delivery-log writer; interim: history-based view, labeled as such | Eng | Open |
| R-06 | Per-source failure/timing not captured (coverage confidence limited) | Medium | High | Wrap ingest sources to record outcomes | Eng | Open |
| R-07 | "Section 508: Compliant" banner unaudited (contract-representation risk) | Medium | Medium | Remove/replace chip pending a formal 508 audit | Ops/Eng | Open |
| R-08 | No formal 508 audit / screen-reader / contrast testing | Medium | High | Commission a 508 audit; remediate; do not claim certification | QA | Open |
| R-09 | No penetration test | Medium | Medium | Schedule security review before external exposure | Sec | Open |
| R-10 | Rate limiter in-memory per-process (softer than configured on multi-instance) | Low | Medium | Move to shared store (Redis) if scaling out; document current behavior | Eng | Open |
| R-11 | Daily deliverable depends on scheduler being enabled (`ENABLE_SCHEDULER`) | High | Low | Confirm `ENABLE_SCHEDULER=true` in prod; watchdog/retry present; monitor | Ops | Mitigating |
| R-12 | Email uses `httpx` (no SendGrid SDK); provider/credential must be correct | Medium | Medium | Verify email credential/endpoint in staging delivery test (Gate G8) | Eng/Ops | Open |
| R-13 | Local frontend CORS-blocked from prod API (dev-only limitation) | Low | High (dev) | Use same-origin/allowlisted staging frontend; not a prod risk | Eng | Mitigating |
| R-14 | Enabling multiple flags at once could mask a regression | Medium | Medium | Enable one flag at a time, verify, per Activation Plan | QA | Mitigating |
| R-15 | Instrumentation/audit write to prod DB once enabled (volume/retention) | Low | Low | Best-effort, low volume; confirm DB capacity/retention expectations | Eng/Ops | Open |
| R-16 | Per-item QA (approve/reject/notes) not implemented; ops may expect it | Medium | Medium | Communicate v1.0 QA is coverage-level; scope per-item QA as future | Ops/QA | Open |
| R-17 | Nothing pushed/deployed; production behavior under real load unverified | High | Medium | Complete staging validation + Production Gate before prod | Eng | Open |
| R-18 | Per-repo tag distribution (no single repo has all 8 tags) could confuse release ops | Low | Low | Documented in Release Package; optionally mirror tags | Eng | Accepted |

---

## Top risks to clear before production

1. **R-01** — validate in staging (blocks everything functional).
2. **R-02** — verify auth enforcement + token wiring.
3. **R-08 / R-07** — resolve 508 audit + banner representation.
4. **R-05 / R-06 / R-04** — complete delivery-log + per-source failure capture to make coverage/delivery assurance real.

*This register lists risks to be managed by AGT. No mitigation herein has been executed.*
