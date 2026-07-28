# FCC Bulletin v1.0 — Development Validation: STOP / Root-Cause Report

**Prepared:** 2026-07-08
**Phase:** Path C (browser validation of Railway Development)
**Result:** ⛔ **VALIDATION HALTED AT STEP 1** — the commit under validation is **not deployed** to Development. No collection was run. No merge, tag, or deploy performed.

---

## Summary

Browser validation of the live Railway **Dev** environment shows the deployed code is **not** the scheduler-fix commit `f344653`. It is the old `main` commit, and **the watchdog error the fix targets is still occurring hourly**. Steps 1 and 2 fail, so per the rules I stopped before Step 3 (collection).

---

## Evidence (from the live Railway UI, authenticated session)

**Environment confirmed = Development:**
- Project **positive-enchantment** → environment selector **"Dev"** (checkmark); a separate **production** environment also exists.
- Service **docuaction-backend** (Dev) → domain **api.docuaction.io** → Online. DB **Postgres-ellh** (Dev) → Online.

**STEP 1 — deployed commit ≠ f344653 → FAIL:**
- Active deployment (docuaction-backend, Dev): **"FCC Bulletin: Production Source Registry v1.0 (194 Approved Sources)"**.
- Details tab → **Deployed via GitHub**, source repo **DocuAction/docuaction-backend**, **branch `main`**, deployed **Jul 8, 2026, 1:31 PM EDT**.
- That commit message corresponds to **`4c39a1d`** (the `main` tip at the start of this work) — **not** `f344653` (scheduler fix) and **not** `6a69ee6` (NewsAPI.ai work).

**STEP 2 — watchdog error still present → FAIL:**
- Deploy Logs (same active deployment) show the target error **recurring every hour**:
  ```
  Jul 8 2026 15:32:28  ERROR:app.bulletin_intelligence.scheduler:Watchdog tick error: There is no current event loop in thread 'ThreadPoolExecutor_0_0'.
  Jul 8 2026 16:32:22  ERROR: … Watchdog tick error: There is no current event loop …
  Jul 8 2026 17:32:24  ERROR: … Watchdog tick error …
  Jul 8 2026 18:32:19  ERROR: … Watchdog tick error …
  Jul 8 2026 19:32:25  ERROR: … Watchdog tick error …
  ```
- The hourly `Job "Hourly watchdog…" executed successfully` line appears too, but the tick body still throws — exactly the pre-fix behavior. This confirms the deployed code does **not** contain `f344653`.

---

## Root-Cause Analysis

**The Railway Development service `docuaction-backend` auto-deploys from the `main` branch.** Pushing `feature/fcc-newsapi-ai-validation` (commit `f344653`) to GitHub therefore **did not deploy it to Development** — Railway only builds `main` for this environment. The active Dev deployment is the last `main` commit (`4c39a1d`, "Production Source Registry v1.0"), which predates all of the NewsAPI.ai and scheduler-fix work.

This creates a process conflict with the release plan:
- The plan says: validate `f344653` in Dev **before** merging to `main`.
- But Dev **only** deploys `main`, and merging to `main` is explicitly forbidden until validation passes.
- Net: with the current Railway wiring, `f344653` can never reach Dev without either changing the deploy source or merging first.

---

## Why the earlier local proof still stands (scope note)

The scheduler fix itself is correct and was proven locally against real APScheduler (reproduced the exact error, then showed the coroutine watchdog runs with no job errors). **The failure here is a deployment/wiring gap, not a code defect** in `f344653`. The fix simply isn't running in Dev yet.

---

## Recommended Fix (deployment/config — pick ONE; operator action, not a code change)

1. **Point the Dev service at the feature branch temporarily (recommended).**
   Railway → project `positive-enchantment` → env **Dev** → service **docuaction-backend** → **Settings → Source** → set deploy branch to `feature/fcc-newsapi-ai-validation` → **Deploy**. Re-run this validation. Revert the branch to `main` after the release is approved and merged.

2. **Use a Railway PR/preview environment** for `feature/fcc-newsapi-ai-validation` (isolated DB) and validate there.

3. **Manually trigger a one-off deploy** of the feature-branch commit to the Dev service (Railway "Deploy" → choose branch/commit), without changing the standing source.

Do **not** resolve this by merging to `main` first — that violates the "no merge before approval" gate.

> Blocking sub-issue to resolve before Step 3 regardless of the above: the Dev `docuaction-backend` is bound to **api.docuaction.io** and deploys from `main`. Before running any collection (a DB write), confirm the Dev service's `DATABASE_URL` targets the **Dev** `Postgres-ellh` and not the production Postgres, so `POST /collect` cannot write production data.

---

## Files Affected

- **None (code).** No source change is required to fix this — it is a Railway deploy-source/config action.
- The commit to be deployed is already on GitHub: `feature/fcc-newsapi-ai-validation` @ `f344653` (contains `scheduler.py`, `engine.py`, `editorial_rules.py`, `bulletin_download_routes.py`, `provider_analysis.py`).

## Estimated Effort

- **Redeploy the correct commit to Dev:** ~5–10 min (change deploy branch + build) + ~2–4 min Railway build/boot.
- **Confirm Dev DB isolation:** ~5 min (inspect the Dev service `DATABASE_URL` variable reference).
- **Re-run validation (Steps 1–8):** proceeds once Dev is on `f344653`.

---

## Report Status (all real-data reports blocked by Step 1)

| Report | Status |
|---|---|
| Scheduler Validation | ❌ **FAIL** — deployed commit is `4c39a1d` (main), not `f344653`; watchdog error still recurring hourly (evidence above) |
| Provider Performance | ⛔ Not run — blocked (no valid collection; wrong code deployed) |
| Coverage Comparison | ⛔ Not run — blocked |
| Duplicate Analysis | ⛔ Not run — blocked |
| Missed Story | ⛔ Not run — blocked |
| Export Validation | ⛔ Not run — blocked |
| Executive Summary | See below |
| **Go / No-Go** | 🚦 **NO-GO** |

## Executive Summary

Development is **not** running the commit under validation. The Railway Dev service deploys `main`, so the pushed feature branch (`f344653`) never reached Dev; the active Dev build is the old `4c39a1d` and the `Watchdog tick error: There is no current event loop…` is **still firing hourly** in its logs. This is a deployment-wiring gap, not a defect in the fix (which is proven locally). Validation is halted at Step 1 with no collection run, no merge, no tag, no deploy. **To proceed:** deploy `f344653` to the Dev service (point Dev at the feature branch, or use a preview env), confirm Dev DB isolation, then re-run Steps 1–8.

*All findings above are read directly from the live Railway Development UI/logs. Nothing is estimated or fabricated.*
