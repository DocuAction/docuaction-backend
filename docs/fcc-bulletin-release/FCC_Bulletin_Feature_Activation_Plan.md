# FCC Bulletin v1.0 — Feature Flag Activation Plan

**Prepared:** 2026-07-07
**Status:** Plan only. **No flags have been changed.** (Frontend `featureFlags.js` defaults are unchanged; backend env flags are unset.) Enable in staging first, one at a time, verifying after each.

**Golden rule:** enable → verify → proceed. If verification fails, roll back the flag before continuing.

---

## Recommended activation order

1. Deploy with **everything OFF** (baseline).
2. `honestStatus`, `unifiedExport` (low-risk UI).
3. `coverageAssurance` (needs a completed run).
4. `BULLETIN_INSTRUMENT_ENABLED` (backend) → then `collectionPipeline`, `analyticsUpgrade`, `opsConsole`.
5. Seed source registry (`POST /sources`) → validate `coverage-assurance` "measured" path.
6. `BULLETIN_AUDIT_ENABLED`.
7. `qaReview`, `delivery` (UI).
8. `BULLETIN_RATE_LIMIT_ENABLED`.
9. `BULLETIN_AUTH_ENABLED` — **last**, after token wiring is verified.

---

## Per-flag detail

### honestStatus (frontend)
- **Purpose:** Derived Live/Delivered/Failed status; retires "Pending Approval".
- **Default:** OFF · **Dependencies:** briefings present.
- **Rollback:** set false, redeploy FE.
- **Verify:** Run History chips read Live/Delivered/Failed; no "Pending Approval".

### unifiedExport (frontend)
- **Purpose:** Word/Excel usable on custom date ranges (day-window mapping).
- **Default:** OFF · **Dependencies:** none.
- **Rollback:** set false.
- **Verify:** with a custom range, Word/Excel enabled + caption; HTML matches exact range.

### coverageAssurance (frontend)
- **Purpose:** Coverage Assurance panel on Daily Briefing over `/coverage`.
- **Default:** OFF · **Dependencies:** a completed run (`/coverage`).
- **Rollback:** set false.
- **Verify:** panel shows real coverage numbers; "Coverage %/Sources Failed/Avg AI Confidence" = "Not Yet Instrumented"; "Not Available" when no run.

### BULLETIN_INSTRUMENT_ENABLED (backend env)
- **Purpose:** Persist run log + per-source outcomes per cycle.
- **Default:** OFF · **Dependencies:** none (best-effort).
- **Rollback:** unset env, restart.
- **Verify:** after a run, `GET /runs/{fcc}` returns a row with funnel/timing; `/runs/{id}` returns source outcomes.

### collectionPipeline (frontend)
- **Purpose:** Pipeline tab.
- **Default:** OFF · **Dependencies:** `BULLETIN_INSTRUMENT_ENABLED` + recorded runs.
- **Rollback:** set false.
- **Verify:** tab lists runs; "Sources →" shows outcomes; "Not Available" if instrument off.

### analyticsUpgrade (frontend)
- **Purpose:** Operations Analytics section on Analytics.
- **Default:** OFF · **Dependencies:** `BULLETIN_INSTRUMENT_ENABLED` + runs.
- **Rollback:** set false.
- **Verify:** shows runs recorded / avg duration / avg in-briefing / latest run; "Not Available" if none.

### opsConsole (frontend)
- **Purpose:** Morning Operations Console tab.
- **Default:** OFF · **Dependencies:** recent run (better with instrumentation).
- **Rollback:** set false.
- **Verify:** freshness, scheduler status, coverage tiles, "requiring attention" list render.

### Source registry seeding (data, via `POST /sources` — admin)
- **Purpose:** provide the Coverage % denominator (expected sources).
- **Dependencies:** `BULLETIN_INSTRUMENT_ENABLED` (for outcomes) + `BULLETIN_AUTH_ENABLED` if admin gating on.
- **Rollback:** disable/clear registry rows.
- **Verify:** after seeding + a run, `/coverage-assurance` returns `status:"measured"` with a real `coverage_pct`; before seeding it stays `pending_instrumentation`.

### BULLETIN_AUDIT_ENABLED (backend env)
- **Purpose:** append-only audit trail.
- **Default:** OFF · **Dependencies:** none.
- **Rollback:** unset env, restart.
- **Verify:** after collection/delivery/purge, `GET /audit/{fcc}` returns events; no secrets/PII.

### qaReview (frontend)
- **Purpose:** QA Dashboard tab (coverage-level).
- **Default:** OFF · **Dependencies:** a recent run.
- **Rollback:** set false.
- **Verify:** missing-category/dupe/subscription checks render.

### delivery (frontend)
- **Purpose:** Delivery Dashboard tab.
- **Default:** OFF · **Dependencies:** history.
- **Rollback:** set false.
- **Verify:** delivered vs. not from history. **Note:** per-recipient log not written in v1.0.

### BULLETIN_RATE_LIMIT_ENABLED (+ BULLETIN_RATE_MAX_PER_HOUR) (backend env)
- **Purpose:** per-client cap on `/collect`, `/send`.
- **Default:** OFF (threshold 20) · **Dependencies:** none.
- **Rollback:** unset env, restart.
- **Verify:** exceeding the cap returns 429. In-memory per-process.

### BULLETIN_AUTH_ENABLED (backend env) — enable LAST
- **Purpose:** enforce `require_role` on gated endpoints.
- **Default:** OFF · **Dependencies:** deployed login that supplies a JWT the FE forwards.
- **Rollback:** unset env, restart (fastest security rollback).
- **Verify:** authorized role succeeds; unauthorized → 401/403; public reads/downloads still work.

### llmVisibilityPanel (frontend) — already ON
- **Purpose:** LLM Visibility panel (pre-existing).
- **Default:** ON · No activation needed.

### Reserved (no dedicated UI): `audit`, `clipsView`
- Keep OFF; no activation step in v1.0.

---

*This plan describes intended activation steps. No flag has been changed.*
