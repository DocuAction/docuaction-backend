# FCC Bulletin v1.0 — Staging Validation Checklist

**Prepared:** 2026-07-07
**Status:** Preparation checklist only. **Nothing below has been executed.** Each item is a step for AGT to perform in staging and record PASS/FAIL with evidence.

**How to use:** Perform top-to-bottom after deploy (see Deployment Guide). Record: result, timestamp, evidence (log line / screenshot / response). Do not mark PASS without observed evidence.

---

## A. Frontend
- [ ] `/bulletin` loads without error; hero + 5 legacy tabs render.
- [ ] Browser console has no application errors (ignore third-party extension warnings).
- [ ] Agency selector shows FCC; coming-soon agencies disabled.
- [ ] With all flags OFF, no Operations/Pipeline/QA/Delivery tabs and no Coverage panel appear.

## B. Backend
- [ ] Service boots; no startup exceptions in logs.
- [ ] `GET /api/v1/bulletin/health` → ok.
- [ ] `GET /api/v1/bulletin/agencies` returns FCC.
- [ ] New read endpoints return safe defaults (empty / `pending_instrumentation`).

## C. Database
- [ ] `init_store` completed (log).
- [ ] Tables exist: `bulletin_articles`, `bulletin_briefings`, `bulletin_run_log`, `bulletin_source_outcome`, `bulletin_source_registry`, `bulletin_delivery_log`, `bulletin_audit_log` (+ indexes).
- [ ] Staging DB is **separate** from production (confirm connection string).

## D. Scheduler (if `ENABLE_SCHEDULER=true`)
- [ ] Scheduler thread starts (log); `/health` reflects scheduler status.
- [ ] Watchdog/retry configured (`MAX_CYCLE_ATTEMPTS`).
- [ ] Confirm next scheduled run time is ~1 AM ET.

## E. Collection
- [ ] Trigger `POST /api/v1/bulletin/collect/{fcc}` (or Collect News Now in UI).
- [ ] Cycle starts; concurrency guard prevents a second overlapping run.
- [ ] Cycle completes; response includes `briefing_id` and funnel counts.
- [ ] Articles persisted (archive count increases).

## F. Run History
- [ ] Run History tab lists the generated briefing with status and counts.
- [ ] Preview/HTML/PDF/Excel actions open.

## G. Coverage
- [ ] `GET /coverage/{fcc}` returns a report after a run (sources scanned, collected, in-briefing, rejected, dupes, subscription, missing categories).
- [ ] `GET /coverage-assurance/{fcc}` returns `pending_instrumentation` (until registry seeded) — **verify it never shows a fabricated %**.

## H. Analytics
- [ ] Analytics tab renders archive stats (topics, monthly volume) with data.
- [ ] LLM Visibility panel runs (if enabled) and returns results.
- [ ] (If `analyticsUpgrade` + instrumentation on) Operations Analytics shows run metrics.

## I. QA
- [ ] (If `qaReview` on) QA Dashboard shows coverage-level checks (missing categories, dupes, subscription).
- [ ] Confirm per-item approve/reject/notes are **not** expected in v1.0 (coverage-level only).

## J. Delivery
- [ ] Preview a briefing (HTML render) — layout correct.
- [ ] Send a test briefing to an approved recipient (`admin@docuaction.io` / `imran@agtbi.com`).
- [ ] Confirm email received and renders.
- [ ] (If `delivery` on) Delivery Dashboard reflects delivered_at from history. **Note:** per-recipient delivery log is not written in v1.0.

## K. Exports
- [ ] Download Word — opens/valid.
- [ ] Download Excel (QA) — opens/valid.
- [ ] Download HTML Email — opens/valid.
- [ ] (If `unifiedExport` on) Custom date range: Word/Excel export a day-window; HTML matches the exact range.
- [ ] Large export (widest window) completes without error.

## L. Authentication (only when `BULLETIN_AUTH_ENABLED=true`)
- [ ] Logged-in user with a sufficient role can trigger gated actions (collect/run/refresh/send/approve per role).
- [ ] Unauthenticated / insufficient-role request returns 401 / 403.
- [ ] Frontend attaches the JWT to gated calls for a logged-in session.
- [ ] Public reads/downloads still work without a token.

## M. Audit (only when `BULLETIN_AUDIT_ENABLED=true`)
- [ ] After a collection/delivery/purge, `GET /audit/{fcc}` returns events.
- [ ] Audit rows contain event metadata only (no secrets/PII).

## N. Rate limiting (only when `BULLETIN_RATE_LIMIT_ENABLED=true`)
- [ ] Exceeding `BULLETIN_RATE_MAX_PER_HOUR` on `/collect` or `/send` returns 429.
- [ ] Note: limiter is in-memory per-process (multi-instance limit is softer than configured).

---

*This checklist enumerates steps to be performed by AGT. No item has been executed.*
