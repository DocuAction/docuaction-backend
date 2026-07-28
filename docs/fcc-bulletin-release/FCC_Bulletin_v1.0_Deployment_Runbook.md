# FCC Bulletin v1.0 — Deployment Runbook

**Date:** 2026-07-08
**Audience:** AGT release operator.
**Precondition:** This runbook documents the process; execution timing is an AGT decision. It does **not** propose any change to the current (approved, temporary) Railway architecture.

> ⚠️ Environment note (accepted operational fact): in the current shared architecture, **both** the Development service (`docuaction-backend`) and the Production service (`zesty-ambition`) deploy from `main` and share one PostgreSQL database. Therefore **merging to `main` deploys to Development and Production simultaneously.** Plan the merge window accordingly.

---

## 1. Merge process

1. Confirm the release commit: `feature/fcc-newsapi-ai-validation` @ `f344653` (contains only the two FCC Bulletin commits `6a69ee6`, `f344653`; nothing else).
2. Confirm isolation (already accepted): `git diff --name-only main..f344653` → all paths under `app/bulletin_intelligence/`.
3. Fast-forward merge (linear from `main`; no merge commit, no unrelated changes):
   ```
   git checkout main
   git merge --ff-only feature/fcc-newsapi-ai-validation
   git push origin main
   ```
4. Railway auto-deploys `main` to Dev **and** Prod via the existing pipeline (no manual deploy step, no branch-mapping change).

## 2. Validation process (post-merge, in the deployed app)

1. **Version:** in Railway, confirm the active deployment of `docuaction-backend` (Dev) is the merged `main` commit (message: "fix APScheduler event-loop error…").
2. **Scheduler:** Deploy Logs show the scheduler start line and jobs registered (`bulletin_watchdog`, `weekday_delivery`, `monday_delivery`, `sunday_preview`); confirm **no** new `Watchdog tick error` lines appear on the next hourly tick.
3. **Health:** `GET /health` → `scheduler.running = true`, jobs listed with `next_run` times.
4. **Collection:** run one `POST /api/v1/bulletin/collect/fcc` (existing auth). Then `GET /api/v1/bulletin/coverage/fcc`:
   - `provider_analytics["NewsAPI.ai"].articles_collected > 0`;
   - existing providers (RSS/GDELT/NewsAPI.org/Tavily) return normal volumes;
   - `provider_coverage_comparison`, duplicate counts, and `registry_editorial_queue` populate.
5. **UAT checks:** Radio Insight / Inside Radio present; a T-Mobile exec-style item is absent from the briefing.
6. **Exports:** download Word / Excel / HTML — US date format, Back-to-Top, Provider column, Similar Stories all present.
7. Record real numbers into the Provider Performance / Coverage / Duplicate / Missed-Story reports (replacing "Pending Measurement").

## 3. Rollback process

Because Railway retains prior deployments, rollback does **not** require an architecture change:
- **Fast path (Railway):** in `docuaction-backend` (and `zesty-ambition`) → Deployments → select the previous successful deployment (`4c39a1d`, "Production Source Registry v1.0") → **Redeploy/Rollback**. Restores prior code in minutes.
- **Git path:** `git revert <merge>` on `main` and push; Railway redeploys the reverted state.
- **Data:** no schema change was introduced, so rollback is code-only — no DB downgrade needed. New `Article` provider fields are ignored by older code (JSON-in-TEXT), so mixed-version rows remain readable.
- **Flags:** if only the editorial behavior needs reverting, set `BULLETIN_EDITORIAL_STRICT=false` (disables the corporate-noise filter) without redeploying.

## 4. Post-deployment verification

- [ ] Dev + Prod active commit = merged `main` (scheduler-fix commit).
- [ ] One full hour with **zero** `Watchdog tick error` lines in both services' logs.
- [ ] `/health` scheduler running with all 4 jobs.
- [ ] One `POST /collect/fcc` succeeds; NewsAPI.ai `articles_collected > 0`; other providers unchanged.
- [ ] Coverage comparison + duplicate analysis + registry queue populated.
- [ ] Radio Insight / Inside Radio collected; T-Mobile exec item rejected.
- [ ] Word / Excel / HTML exports open with correct formatting + Provider column.
- [ ] No runtime errors, no regressions in other modules (TEFCA/Healthcare/etc.).
- [ ] Only after all pass **and** AGT approval → apply the Production Ready tag.

## 5. Do-not list (scope guardrails)

Do not create new databases, change branch mappings, redesign CI/CD, or alter Railway/Azure architecture as part of this deployment. Those belong to the future Azure migration project.
