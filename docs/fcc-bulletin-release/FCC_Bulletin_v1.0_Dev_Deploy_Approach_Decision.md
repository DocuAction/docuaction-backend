# FCC Bulletin v1.0 — Dev Validation Deploy-Approach Decision

**Prepared:** 2026-07-08
**Method:** Read-only inspection of the live Railway UI (authenticated session). No changes made to any environment.

---

## Decision: ⛔ Option 1 (cherry-pick into `main`) is UNSAFE — not executed.

**Reason (hard evidence from Railway):** the `main` branch auto-deploys to **both** Development **and** Production.

| Environment | Service | Domain | Deploy branch | Active commit | Notes |
|---|---|---|---|---|---|
| **Dev** | `docuaction-backend` | api.docuaction.io | **`main`** | "Production Source Registry v1.0" (`4c39a1d`), Jul 8 1:31 PM EDT | old code; watchdog error recurring hourly |
| **production** | `zesty-ambition` | **api-prod.docuaction.io** | **`main`** | "Production Source Registry v1.0" (`4c39a1d`), Jul 8 1:31 PM EDT | **live FCC Bulletin traffic** (`GET /api/v1/bulletin/archive/fcc` 200 OK); watchdog error also recurring |

Both services' **Details → Deployed via GitHub** explicitly show branch **`main`** (repo `DocuAction/docuaction-backend`). Therefore:

> **A push to `main` would deploy the unvalidated FCC Bulletin commits to the live Production backend (`api-prod.docuaction.io`).** That violates "Do not deploy to Production" and "Do not declare Production Ready."

Cherry-picking vs. fast-forward is irrelevant — any advance of `main` triggers the Production build. **Option 1 cannot be used.**

---

## Recommended path: Option 2 — isolated Railway preview/validation environment

Create a **new, isolated environment** (e.g. `fcc-validation`) in the `positive-enchantment` project that:
1. Runs `docuaction-backend` from `feature/fcc-newsapi-ai-validation` (commit `f344653`).
2. Has its **own isolated Postgres** (not the production DB).
3. Has the required env vars/keys set on that env: `ENABLE_SCHEDULER`, `ENABLE_QA_MONITOR`, `NEWSAPI_AI_KEY`, `NEWSAPI_KEY`, `TAVILY_API_KEY`, `ANTHROPIC_API_KEY`.

This validates `f344653` with real data, touches neither `main` nor Production, and is discarded after.

**Option 3 (manual Dev-only deploy of `f344653`)** is possible but carries a risk that must be resolved first (below).

---

## ⚠️ Blocking caveat before ANY collection is run (applies to Option 2 and 3)

Prior project knowledge indicates the **Dev `docuaction-backend` may connect to the production Postgres** (real data) rather than an isolated Dev DB. Before running `POST /collect` in any environment, the target service's `DATABASE_URL` **must be confirmed to point at an isolated (non-production) database** — otherwise a collection writes production data, violating "Never write to Production." For Option 2 this is guaranteed by provisioning a fresh Postgres in the new environment; for Option 3 it must be verified explicitly.

---

## What I did NOT do (per the rules)

- ❌ No push to `main`. ❌ No merge. ❌ No tag. ❌ No Production deploy/modification.
- ❌ No collection run (wrong code deployed to Dev + DB-isolation unconfirmed).
- ❌ Did not change the Dev service's tracked branch (it is shared by other modules).
- ✅ Read-only inspection only; two evidence screenshots saved.

---

## Go / No-Go

🚦 **NO-GO** (unchanged). The validation cannot proceed until `f344653` runs in an environment that is (a) not `main`-tracked and (b) isolated from the production database.

## Requested direction

Because executing Option 2 or 3 requires Railway **infrastructure actions** (creating an environment / provisioning a DB / setting keys / triggering a deploy) that are hard to reverse on a production-adjacent federal system, I will not perform them unprompted. Please either:

- **(A)** Stand up the isolated `fcc-validation` environment (Option 2) with the keys above and tell me when it's live + its URL — I'll run the full validation there; **or**
- **(B)** Explicitly authorize a specific manual Dev-only deploy of `f344653` (Option 3) **and** confirm the Dev `docuaction-backend` `DATABASE_URL` is isolated from production — then I'll proceed.

Either way I'll rerun the complete Development validation on real data and produce the updated Go/No-Go.
