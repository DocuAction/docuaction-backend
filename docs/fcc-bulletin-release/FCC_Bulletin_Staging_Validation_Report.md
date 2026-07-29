# FCC Bulletin v1.0 — Staging Validation Report

**Executed:** 2026-07-07
**Environment:** Local staging — v1.0 backend (uvicorn, `127.0.0.1:8000`) running the committed v1.0 code against **PostgreSQL 18.3** (`localhost:5432/docuaction-db`). This is real, running software against a real database and live news feeds.
**Repo under test:** backend code through `d6f6eea` (Phase 6); frontend `0cf24af`. Nothing pushed/deployed/merged.

**Result vocabulary:** PASS (observed) · FAIL (observed) · BLOCKED (env/config prevented it) · NOT EXECUTED (with reason). **No PASS is recorded without observed evidence.**

---

## Step 1 — Backend startup — PASS (with operational note)

- DB connection: **OK** (PostgreSQL 18.3).
- **Finding (operational):** with `.env` alone, the shared `app/core/database.py` reads `os.getenv("DATABASE_URL")` (not pydantic settings), so it fell back to `postgres:postgres@…/railway`, auth-failed 7×, and ran **memory-only** (`persisted.enabled=false`). Exporting `DATABASE_URL` fixed it. See DEF-S2. *(Shared framework, outside the FCC Bulletin module; production Railway sets real env vars so this does not affect prod.)*
- After exporting env: `/health` → `status:active, version:1.0.0, persisted.enabled:true`; log "Bulletin store ready (Postgres) on attempt 1"; **0 auth failures**; "Application startup complete"; no exceptions.
- **DB initialization:** `init_store` auto-created all 5 additive tables (`bulletin_run_log, bulletin_source_outcome, bulletin_source_registry, bulletin_delivery_log, bulletin_audit_log`) + the 2 legacy tables. **PASS.**
- Scheduler: DISABLED (`ENABLE_SCHEDULER` unset); status `running:false` — expected.

## Step 2 — Frontend — NOT EXECUTED (this round)
The dev server remained pointed at the production API; it was not re-pointed at the local staging backend this round. (Prior sessions verified flags-OFF legacy UI renders 5 tabs with no console errors.) **Recommend** re-pointing `NEXT_PUBLIC_API_URL=http://localhost:8000` and repeating the UI walkthrough with real data.

## Step 3 — Feature flags (backend) — PASS (partial)
- Backend flags exercised via env: `BULLETIN_INSTRUMENT_ENABLED`, `BULLETIN_AUDIT_ENABLED`, `BULLETIN_AUTH_ENABLED` — each activated its behavior when set (see Steps 4/6/7). 
- Frontend one-at-a-time UI toggling: NOT EXECUTED this round (tied to Step 2).

## Step 4 — Full FCC cycle (real data) — PASS (collection) / BLOCKED (AI)
Triggered `POST /run/fcc`; cycle completed in ~254 s and produced a briefing.
- **Collection:** 348 articles ingested from real feeds. **PASS.**
- **Deduplication:** 30 duplicates removed → 318 after dedup. **PASS.**
- **Rejection/assembly:** 168 rejected, 150 in briefing. **PASS.**
- **Categories:** classified 318; missing-category detection worked (flagged Robocalls/TCPA, Net Neutrality, Undersea Cables, Telecom Mergers, Congressional Oversight). **PASS.**
- **Subscription labels:** 8 subscription stories flagged. **PASS.**
- **Persistence:** `bulletin_articles`=318, `bulletin_briefings`=1. **PASS.**
- **AI summaries / AI classification:** **BLOCKED** — every Anthropic call returned `401 invalid x-api-key`. The engine **gracefully degraded** (fallback classification, briefing still produced), but AI-quality summaries/classification could not be validated. See DEF-S1. *(Invalid key is an environment/config issue; my extraction was verified clean — raw `.env` key == exported key.)*

## Step 4 — Instrumentation (Phase 4) — PASS
`GET /runs/fcc` → 1 run: `run_id fcc_20260707_134306`, trigger `cycle`, `ingested 348 / after_dedup 318 / in_briefing 150 / rejected 168 / dupes_removed 30`, `duration_ms 254104`, `status completed`, coverage JSON embedded. `bulletin_source_outcome`=125 rows. **PASS.**

## Step 5 — Exports — PASS
From the real briefing:
- Word `download/fcc` → 57,046 bytes, magic `50 4b 03 04` (valid .docx).
- Excel `download-excel/fcc` → 37,880 bytes, valid .xlsx.
- HTML `briefings/{id}/preview` → 99,349 bytes, `<!DOCTYPE html>`.
All three generated valid files. **PASS.** (Deep formatting/link/date inspection not performed byte-by-byte; files open as valid archives/HTML.)

## Step 6 — Delivery — PASS (dry-run) / BLOCKED (real email)
`POST /send/fcc/{id}` → `status:dry_run, recipients:1, subject:"FCC Daily Briefing — July 07, 2026", from:news@agtbi.com`. Graceful dry-run because no `SENDGRID_API_KEY` is configured. Real email send/rendering/retry **not validated** (env). See DEF-S3.

## Step 7 — Security — PASS
With `BULLETIN_AUTH_ENABLED=true`:
- Public reads (`/health`, `/coverage-assurance`, `/latest`) → **200**.
- Gated (`POST /run`, `/send`, `/sources`, `GET /runs`) with no token → **403**.
- Gated with a bogus token → **401**.
Authorization correctly enforced; public surface correctly open. **PASS.**
- Rate limiting: NOT EXECUTED (flag off this round).

## Step 8 — Coverage Assurance — PASS (honesty verified both ways)
- Registry empty: `coverage_pct:null, status:pending_instrumentation` even with 125 real outcomes. **No fabricated %.** **PASS.**
- After seeding 2 real sources (`rss` w2.0, `variety.com` w1.0) via `POST /sources` (upserted 2): `expected_sources:2, coverage_pct:100.0, coverage_confidence:100.0, status:measured` — genuinely 2/2 covered. **PASS.**
This validates the measured path computes a real number only when backed by real data.

## Step 9 — Performance — measured values only
| Metric | Measured | Note |
|---|---|---|
| Backend import/startup | ~3.96 s (import) / boot to `/health` a few s | dev boot, not prod |
| Full cycle (348 articles) | **254.1 s** | **inflated** by AI 401 retry storm; not a healthy-run benchmark |
| Word export | < 30 s (57 KB) | |
| Excel export | < 30 s (37 KB) | |
| HTML preview | < 30 s (99 KB) | |
| Page load (local FE↔BE) | NOT MEASURED | tied to Step 2 |
See Performance Report for detail.

## Step 10 — Regression — PASS (partial)
- Backend loads all modules (246 routes) without error; bulletin adds routes only (38 bulletin routes). No import/startup exceptions.
- TEFCA/Healthcare/other modules: **not modified**; they load normally (log shows all modules registered).
- Full legacy UI/feature regression tied to Step 2 (frontend) — NOT re-executed this round.

---

## Summary

**Validated working in real staging:** backend startup + DB auto-init (5 additive tables), collection (348) + dedup (30) + assembly (150) + persistence, instrumentation (run log + 125 source outcomes), Coverage Assurance honesty (pending **and** measured), registry write, exports (Word/Excel/HTML valid), audit writer (on audited routes), auth enforcement (403/401).

**Blocked by environment/config (not code):** AI classification/summaries (invalid `ANTHROPIC_API_KEY`), real email delivery (no `SENDGRID_API_KEY`).

**Not executed this round:** frontend-against-local-backend UI walkthrough, one-at-a-time FE flag toggling, rate limiting, page-load timing, full regression.

**No FCC Bulletin code defect was observed.** All failures trace to environment/config (invalid keys, DATABASE_URL export in shared framework).
