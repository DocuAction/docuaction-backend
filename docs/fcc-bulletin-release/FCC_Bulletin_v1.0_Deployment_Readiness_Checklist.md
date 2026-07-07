# FCC Bulletin v1.0 — Deployment Readiness Checklist

**Prepared:** 2026-07-07
**State:** Built, committed, tagged **locally**. **Not pushed, not deployed.**

**Status legend**
- ✅ **Verified** — actually executed/observed in this work.
- 🟡 **Implemented** — code exists and compiles/builds, but not end-to-end verified in a live/deployed environment.
- ⏳ **Pending** — not built, not enabled, or prerequisite not met.
- 📋 **Planned** — roadmap.

> No item below is marked ✅ unless it was actually run. No production-readiness certification and no 508 certification is claimed.

---

## Readiness matrix

| # | Area | Status | Evidence / Notes |
|---|---|---|---|
| 1 | **Build status (overall)** | ✅ Verified | All phase builds/compiles passed locally; working trees clean; nothing pushed. |
| 2 | **Backend compile** | ✅ Verified | `python -m py_compile` passed for every changed file (`auth.py`, `audit.py`, `instrumentation.py`, `routes.py`, `bulletin_store.py`, `engine.py`); import smoke test loads the router. |
| 3 | **Frontend build** | ✅ Verified | `npm run build` "Compiled successfully" for each FE phase (0, 1, 2, 5, 7); zero errors/warnings. |
| 4 | **Regression (default config)** | ✅ Verified | Flags OFF → `guard()==[]`, audit/instrument `record_run` no-op, 34 bulletin routes build, live `run_daily_cycle` path unchanged. Behavior is unchanged by default. |
| 4a | Regression (live, real data) | ⏳ Pending | Not run — local only; localhost is CORS-blocked from the prod API. Requires a deployed/staging env. |
| 5 | **Feature flags** | ✅ Verified | All new flags default OFF (`{auth:false, audit:false, instrument:false}`; FE flags false) except `llmVisibilityPanel` which was already live. |
| 6 | **Security — capability** | 🟡 Implemented | Flag-gated `require_role` on 9 endpoints + optional rate limiting; reuses shared auth (unmodified). |
| 6a | Security — enforced by default | ⏳ Pending | `BULLETIN_AUTH_ENABLED` is OFF → endpoints are **unauthenticated** (same as pre-v1.0). Enable + wire login token before exposing. |
| 6b | Security — review / pen-test | ⏳ Pending | Not performed. |
| 7 | **Audit** | 🟡 Implemented / ⏳ default OFF | Append-only trail + `GET /audit`; no-op unless `BULLETIN_AUDIT_ENABLED`. Actual row-writing not verified (needs DB + flag). Actor = `"api"` (no per-user attribution). |
| 8 | **Accessibility (508)** | 🟡 Partial / ⏳ not audited | ARIA tab roles, semantic `<button>`s, iframe titles, `rel=noopener` present. **No** contrast/keyboard/screen-reader audit. **508 certification NOT claimed.** Static "Section 508: Compliant" banner chip predates this work and must be reconciled with a real audit. |
| 9 | **Coverage Assurance** | ✅ Design verified / ⏳ metric pending | Endpoint returns `pending_instrumentation` (coverage_pct = null) by default — honest, no fabricated %. Real Coverage % is ⏳ pending registry seed + per-source **failure** instrumentation. |
| 10 | **Rollback** | ✅ Verified/Documented | Phase tags exist per repo; flag-disable is instant (no redeploy for env flags); `git reset --hard <tag>` documented; additive commits revert cleanly. |
| 11 | **Deployment** | ⏳ Pending (not done) | Nothing pushed/deployed; both repos 5 commits ahead of origin. Procedure documented in Release Package §3. Deploying with flags OFF is behavior-neutral. |
| 12 | **Known limitations** | 📋 Documented | See Release Package §12 / Architecture §14 (per-source failure capture, delivery log writer, per-user audit, in-memory rate limiter, reserved flags). |
| 13 | **Outstanding risks** | 📋 See below | — |

---

## Outstanding risks (before enabling features / going live)

1. **Enabling auth without login-token wiring in the deployed UI** would 401 the gated actions. The FE attaches the JWT when present; verify a logged-in session supplies it before flipping `BULLETIN_AUTH_ENABLED`.
2. **Coverage % correctness depends on a correctly-seeded registry** and on per-source *failure* capture (not yet built). Until then, present coverage as "pending," never as a completeness guarantee.
3. **`bulletin_delivery_log` is created but unwritten** — the Delivery Dashboard reflects run history, not a per-recipient log; do not represent it as a delivery audit.
4. **In-memory rate limiter** is per-process; on multiple Railway instances the effective limit is higher than configured.
5. **Instrumentation writes to prod Postgres** once enabled — low volume, best-effort, but confirm DB capacity/retention expectations.
6. **508 banner** asserts compliance that has not been audited — a contract-representation risk until reconciled.

---

## Go / No-Go summary (honest)

- **Deploy code with all flags OFF:** ✅ **Safe** — verified behavior-neutral; no regression by construction.
- **Enable auth / audit / instrumentation:** 🟡 **Conditional** — implemented, but enable one at a time in staging and verify (token wiring, DB writes) first.
- **Advertise Coverage %, delivery audit, or 508 compliance:** ⏳ **No** — prerequisites not met; would overstate the system.
- **Overall production-readiness certification:** **Not claimed.** The module is deploy-safe in its default (dormant) configuration; feature activation and the items in "Outstanding risks" require completion and verification first.

---

*This checklist reports only verified facts as of 2026-07-07. Nothing is deployed.*
