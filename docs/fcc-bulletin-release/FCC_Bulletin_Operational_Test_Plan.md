# FCC Bulletin v1.0 — Operational Test Plan (Morning Cycle Dry Run)

**Prepared:** 2026-07-07
**Status:** Test plan only. **This cycle has NOT been run. No results are recorded here.** AGT executes this in **staging** using real FCC news and records outcomes.
**Objective:** Exercise one complete morning bulletin cycle end-to-end and confirm each stage behaves correctly before production.

**Preconditions:** staging deployed per the Deployment Guide; DB reachable; `BULLETIN_INSTRUMENT_ENABLED=true` and `BULLETIN_AUDIT_ENABLED=true` (so this dry run also validates instrumentation/audit); approved test recipient available.

---

## Scenario: one complete morning cycle (FCC, real news)

### Step 1 — Trigger collection
- Action: `POST /api/v1/bulletin/collect/fcc` (or "Collect News Now"), or let the ~1 AM ET scheduler run.
- Record: start time, `briefing_id`, funnel counts (ingested / after-dedup / in-briefing / rejected / dupes).
- Expected: cycle completes; no exception; concurrency guard blocks overlapping runs.

### Step 2 — Deduplication
- Verify: `duplicates_removed` in coverage report is plausible; no obvious duplicate stories in the briefing.

### Step 3 — AI summaries
- Verify: articles have non-empty, on-topic summaries; no truncation errors.

### Step 4 — Categories / classification
- Verify: topics assigned; FCC category structure present; leadership/chairman prefixing where applicable; relevance tiers applied.

### Step 5 — QA (coverage-level)
- Action: `GET /coverage/fcc`; open QA Dashboard (if `qaReview` on).
- Verify: missing-category warnings, subscription counts, dupe counts are shown and reasonable.

### Step 6 — Exports
- Action: download Word, Excel (QA), HTML Email; (if `unifiedExport`) test a custom range.
- Verify: each opens and is well-formed; Excel QA columns present; HTML renders.

### Step 7 — Delivery
- Action: send to an approved recipient (`admin@docuaction.io` / `imran@agtbi.com`).
- Verify: email received before the operational target; renders correctly; `delivered_at` set.
- Note: per-recipient delivery log is not written in v1.0 (history-based).

### Step 8 — Run History
- Verify: the run appears with status, counts, and working preview/PDF/Excel actions.

### Step 9 — Coverage
- Action: `GET /coverage-assurance/fcc`.
- Verify: if the registry is unseeded → `pending_instrumentation` (no fabricated %); if seeded → `measured` with a real `coverage_pct` and confidence.

### Step 10 — Audit
- Action: `GET /audit/fcc` (with `BULLETIN_AUDIT_ENABLED=true`).
- Verify: collection (and delivery) events recorded; metadata only, no secrets/PII.

### Step 11 — Operations Dashboard
- Action: open Operations Console + Pipeline (flags on).
- Verify: today's briefing freshness, scheduler status, run funnel, per-source outcomes render; "requiring attention" reflects real gaps.

---

## Recording template (per step)

| Step | Executed (Y/N) | Result (PASS/FAIL) | Evidence | Notes |
|---|---|---|---|---|
| 1 Collection | | | | |
| 2 Dedup | | | | |
| 3 Summaries | | | | |
| 4 Categories | | | | |
| 5 QA | | | | |
| 6 Exports | | | | |
| 7 Delivery | | | | |
| 8 Run History | | | | |
| 9 Coverage | | | | |
| 10 Audit | | | | |
| 11 Ops Dashboard | | | | |

## Exit criteria
- All 11 steps PASS with evidence, **or** every FAIL has a logged defect and a disposition.
- No critical defect open (see Production Gate).

---

*This plan defines a dry run to be executed by AGT in staging. It has not been run; the table above is intentionally blank.*
