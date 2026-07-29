# FCC Bulletin v1.0 — Staging Deployment Guide

**Prepared:** 2026-07-07
**Purpose:** Instructions for AGT to stand up a **staging** environment for the FCC Bulletin v1.0 module and prepare it for functional validation.
**Status of this document:** Preparation guide only. **Staging has NOT been deployed. No steps below have been executed. Nothing has been pushed or deployed.**

> Honesty: this is a procedure to be performed by AGT. It does not report results and does not assert production readiness.

---

## 1. Prerequisites

- Access to both GitHub repositories and permission to deploy to the staging targets.
- A **dedicated staging PostgreSQL database** (do **not** point staging at the production database).
- Staging hosting for backend (Railway service or equivalent) and frontend (Vercel preview/staging project or equivalent).
- Anthropic API access (Claude) for AI processing, scoped to staging.
- An email sending credential/endpoint for delivery tests (delivery uses an `httpx` HTTP call; the `sendgrid` SDK is **not** installed).
- Test recipient inbox(es) for delivery validation (recommend `admin@docuaction.io` and/or `imran@agtbi.com` — the only approved recipients).

## 2. Required repositories

| Repo | Remote | Staging target |
|---|---|---|
| Frontend | github.com/DocuAction/docuaction-frontend | Vercel (staging) |
| Backend | github.com/DocuAction/docuaction-backend | Railway (staging) |

## 3. Branch / commit requirements

- Deploy from the reviewed v1.0 commits. Frontend HEAD `0cf24af`; backend HEAD `94a2638` (docs) / code through `d6f6eea` (Phase 6).
- Phase tags for reference/rollback: `FCC-BULLETIN-PHASE0`…`PHASE7` (distributed per repo).
- These commits are **local and not yet pushed**. AGT must push to a staging branch (or `main`, per AGT policy) to trigger staging deploys. **Pushing is an AGT decision and is outside this package.**

## 4. Required environment variables

**Backend (staging)** — required app-wide:
| Var | Required | Notes |
|---|---|---|
| `SECRET_KEY` | Yes | App refuses to start without it. |
| `DATABASE_URL` | Yes | **Staging** Postgres (asyncpg URL). |
| `ALLOWED_HOSTS` / CORS allowlist | Yes | Must include the staging backend host and the staging frontend origin, or requests return 400/blocked. |
| `ENABLE_SCHEDULER` | Optional | `true` to run the ~1 AM ET daily cycle + watchdog in staging. |

**Backend — v1.0 capability flags (all default OFF; enable per the Activation Plan):**
`BULLETIN_AUTH_ENABLED`, `BULLETIN_RATE_LIMIT_ENABLED`, `BULLETIN_RATE_MAX_PER_HOUR` (default 20), `BULLETIN_AUDIT_ENABLED`, `BULLETIN_INSTRUMENT_ENABLED`.

**Backend — AI / email:** Anthropic API key (staging), email sending credential/endpoint.

**Frontend (staging):** `NEXT_PUBLIC_API_URL` → the **staging** backend URL (not production).

## 5. Database preparation

- Provision an **empty staging Postgres** (or a sanitized copy).
- No manual migration tool is required: on backend startup, `init_store` runs `CREATE TABLE IF NOT EXISTS` for the pre-existing tables (`bulletin_articles`, `bulletin_briefings`) and the 5 additive v1.0 tables (`bulletin_run_log`, `bulletin_source_outcome`, `bulletin_source_registry`, `bulletin_delivery_log`, `bulletin_audit_log`) plus indexes.
- Verify table creation after first boot (see §7).

## 6. Feature flags (initial state)

Deploy with **all feature flags OFF** (frontend `featureFlags.js` defaults + backend env unset). This makes the module behavior-neutral. Enable capabilities only per `FCC_Bulletin_Feature_Activation_Plan.md`.

## 7. Startup order

1. **Database** — provision staging Postgres; capture `DATABASE_URL`.
2. **Backend** — set env (`SECRET_KEY`, `DATABASE_URL`, allowlist, AI/email); deploy; confirm boot + `init_store` table creation.
3. **Backend health** — `GET /api/v1/bulletin/health` returns ok.
4. **Frontend** — set `NEXT_PUBLIC_API_URL` → staging backend; deploy; confirm `/bulletin` loads (5 legacy tabs, flags off).
5. **Scheduler (optional)** — set `ENABLE_SCHEDULER=true` if the daily cycle is to be validated in staging.
6. **Capability enablement** — follow the Activation Plan, one flag at a time with verification.

## 8. Verification steps (post-deploy smoke)

- `GET /api/v1/bulletin/health` → ok.
- Backend logs show `init_store` completed; the 5 additive tables exist (`\dt bulletin_*`).
- `/bulletin` renders with 5 legacy tabs only (flags off).
- New read endpoints return safe defaults: `/coverage-assurance/{agency}` → `pending_instrumentation`; `/runs/{agency}` → empty; `/audit/{agency}` → empty.
- (Full functional verification is in `FCC_Bulletin_Staging_Checklist.md`.)

## 9. Rollback

- **Feature rollback:** unset the env flag (backend restart) or set the frontend flag false (redeploy) → capability becomes a no-op.
- **Deploy rollback:** redeploy the previous staging build, or `git reset --hard <FCC-BULLETIN-PHASEx>` / `git revert` (additive commits revert cleanly).
- **Database:** additive tables are inert when flags are off; safe to leave. If removal is needed and no flag is writing: `DROP TABLE IF EXISTS bulletin_audit_log, bulletin_run_log, bulletin_source_outcome, bulletin_source_registry, bulletin_delivery_log;`.

---

*This guide describes actions to be performed by AGT in a staging environment. No action herein has been executed.*
