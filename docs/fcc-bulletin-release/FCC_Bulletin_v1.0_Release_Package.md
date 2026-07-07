# FCC Bulletin v1.0 — Release Package

**Module:** FCC News Bulletin (`app/bulletin_intelligence/**`, `frontend/src/app/bulletin/**`)
**Prepared:** 2026-07-07
**Status:** Built, committed, and tagged **locally**. **NOT pushed, NOT deployed, NOT merged to any remote.**
**Scope guarantee:** Only the FCC Bulletin module changed. TEFCA, Healthcare, Decision Intelligence, Case Management, Shared Framework, Authentication (import-only), and other agency modules were **not modified**.
**Design principle:** Every new capability is **additive and behind a flag that defaults OFF**, so the running application's behavior is unchanged until a flag is explicitly enabled.

> Honesty note: This package reports only what was actually implemented and verified in this work. Where something is not yet built or not formally audited, it is labeled as such. No metric, test count, or compliance status is estimated or fabricated.

---

## 1. Release Manifest

Two repositories. Phase tags live in the repo each phase actually modified (verified via `git`, not memory).

**Frontend — `docuaction-frontend`** (5 commits ahead of `origin/main`, not pushed)

| Phase | Tag | Commit | Date (−04:00) |
|---|---|---|---|
| 0 | FCC-BULLETIN-PHASE0 | `7c33a07` | 2026-07-07 02:32:32 |
| 1 | FCC-BULLETIN-PHASE1 | `2242611` | 2026-07-07 02:48:51 |
| 2 | FCC-BULLETIN-PHASE2 | `539af02` | 2026-07-07 10:59:55 |
| 5 | FCC-BULLETIN-PHASE5 | `5085c61` | 2026-07-07 11:19:41 |
| 7 | FCC-BULLETIN-PHASE7 | `0cf24af` | 2026-07-07 12:23:18 |

**Backend — `docuaction-backend`** (5 commits ahead of `origin/main`, not pushed)

| Phase | Tag | Commit | Date (−04:00) |
|---|---|---|---|
| 0 | FCC-BULLETIN-PHASE0 | `dbb7866` | 2026-07-07 02:32:32 |
| 2 | FCC-BULLETIN-PHASE2 | `c302196` | 2026-07-07 10:59:55 |
| 3 | FCC-BULLETIN-PHASE3 | `498b03c` | 2026-07-07 11:05:01 |
| 4 | FCC-BULLETIN-PHASE4 | `f4f28a8` | 2026-07-07 11:14:28 |
| 6 | FCC-BULLETIN-PHASE6 | `d6f6eea` | 2026-07-07 11:21:11 |

**Tag distribution (intentional):** Phase 1/5/7 = frontend-only; Phase 3/4/6 = backend-only; Phase 0/2 = both. No single repo holds all 8 tags; across both repos, all of PHASE0–PHASE7 exist.

**New / changed files**

- Frontend (all under `src/app/bulletin/`): `config/featureFlags.js`, `lib/constants.js`, `components/shared.js`, `components/{DailyBriefing,History,Archive,Analytics,Agencies}Tab.js`, `components/CoverageAssurance.js`, `components/{OpsConsole,CollectionPipeline,QaDashboard,DeliveryDashboard}.js`, `page.js` (thin shell).
- Backend (all under `app/bulletin_intelligence/`): `auth.py`, `audit.py`, `instrumentation.py` (new); `routes.py`, `bulletin_store.py`, `engine.py` (modified additively).

---

## 2. Release Notes

**Phase 0 — Foundation.** Refactored the 931-line `page.js` into `lib/` + `components/` + per-tab files (behavior-preserving). Introduced the feature-flag module. Added 5 inert `bulletin_*` tables.

**Phase 1 — UI surfacing & correctness (flag-gated).**
- Honest derived status (Live/Delivered/Failed) replacing the misleading "Pending Approval" display (`honestStatus`).
- Coverage Assurance panel surfacing the existing `/coverage` report honestly (`coverageAssurance`).
- Fixed the custom date-range export bug so Word/Excel are no longer dead buttons on a range (`unifiedExport`).

**Phase 2 — Endpoint security (flag-gated).** Optional authorization on 9 state-changing/costly endpoints via the shared `require_role`; optional per-client rate limiting; frontend attaches the JWT to gated calls.

**Phase 3 — Audit logging (flag-gated).** Append-only audit trail for collection/delivery/manual events; `GET /audit/{agency}` reader.

**Phase 4 — Collection instrumentation (flag-gated).** `run_daily_cycle` best-effort persists a run-log row (funnel + timing) and per-source outcomes; `GET /runs/{agency}` + `/runs/{agency}/{run_id}` readers.

**Phase 5 — Operational screens (flag-gated).** Four new tabs — Operations Console, Collection Pipeline, QA Dashboard, Delivery Dashboard — each shown only when its flag is on.

**Phase 6 — Honest Coverage Assurance (flag-gated data).** Expected-source registry + `GET /coverage-assurance/{agency}`. Coverage % is computed only when a registry and per-source outcomes both exist; otherwise `pending_instrumentation`.

**Phase 7 — Analytics enrichment (flag-gated).** Analytics tab gains an "Operations Analytics" section over the run-log.

---

## 3. Deployment Guide

> This module is currently **local-only**. Deployment has **not** been performed. The steps below are the procedure to deploy when authorized.

**Preconditions**
- Backend env already requires `SECRET_KEY` and `DATABASE_URL` (enforced app-wide). Host must be in `ALLOWED_HOSTS`/CORS allowlist (unlisted host → 400).
- Postgres reachable; the 5 additive tables auto-create on `init_store` (`CREATE TABLE IF NOT EXISTS`).

**Deploy order (recommended): backend first, then frontend.**

1. **Backend** — push `docuaction-backend main` (currently 5 commits ahead). Railway (`docuaction-backend`, api.docuaction.io) redeploys. On boot, `init_store` creates the 5 additive tables (idempotent). With **all env flags unset**, behavior is unchanged.
2. **Frontend** — push `docuaction-frontend main` (5 commits ahead). Vercel redeploys. With **all feature flags `false`**, the UI is identical to today (default tabs only).
3. **Verify** — `GET /api/v1/bulletin/health` returns ok; `/bulletin` loads with the original 5 tabs.
4. **Enable capabilities incrementally** (see §5, §6) — one flag at a time, verifying after each.

**No-push posture:** Nothing here should be pushed until a release owner authorizes it. Deploying the code with flags off is safe; enabling flags is the behavior-changing step.

---

## 4. Rollback Guide

**Nothing is deployed**, so "rollback" today = discard local commits/tags if desired. After a future deploy, use the same tags.

**Instant feature disable (no redeploy needed for backend env flags):** unset the env flag(s) → guards/writers become no-ops. For frontend flags, set to `false` and redeploy the frontend.

**Code rollback (per repo):**
- To a phase checkpoint: `git reset --hard <tag>` (e.g. `git -C backend reset --hard FCC-BULLETIN-PHASE3`).
- Frontend clean state pre-work: the pre-Phase-0 commit is the parent of `FCC-BULLETIN-PHASE0` (`7c33a07^`).
- Backend clean state pre-work: `dbb7866^`.

**Deployed rollback:** revert the deploy to the previous release, or `git revert` the phase commits (they are additive, so reverts are clean). Because every capability is flag-gated, **disabling the flag is the fastest and safest rollback** and rarely requires a code revert.

**Database:** the 5 additive tables are inert when flags are off; leaving them in place is harmless. If removal is required: `DROP TABLE IF EXISTS bulletin_audit_log, bulletin_run_log, bulletin_source_outcome, bulletin_source_registry, bulletin_delivery_log;` (only after confirming no flag is writing to them).

---

## 5. Configuration Guide

**Backend environment variables** (all optional; default = disabled):

| Env var | Default | Effect when `true` |
|---|---|---|
| `BULLETIN_AUTH_ENABLED` | `false` | Enforces `require_role` on gated endpoints (see §7). |
| `BULLETIN_RATE_LIMIT_ENABLED` | `false` | Per-client hourly cap on `/collect` and `/send`. |
| `BULLETIN_RATE_MAX_PER_HOUR` | `20` | Rate-limit threshold (only used when rate limiting on). |
| `BULLETIN_AUDIT_ENABLED` | `false` | Writes audit rows on collection/delivery/manual events. |
| `BULLETIN_INSTRUMENT_ENABLED` | `false` | Persists run-log + per-source outcomes per cycle. |

Flags are read at process start (deploy-time config). Changing one requires a backend restart/redeploy.

**Frontend feature flags** — `frontend/src/app/bulletin/config/featureFlags.js`:

| Flag | Default | Reveals |
|---|---|---|
| `honestStatus` | `false` | Derived status in Run History. |
| `coverageAssurance` | `false` | Coverage Assurance panel on Daily Briefing. |
| `unifiedExport` | `false` | Word/Excel enabled on custom ranges (day-window mapping). |
| `opsConsole` | `false` | "Operations" tab. |
| `collectionPipeline` | `false` | "Pipeline" tab. |
| `qaReview` | `false` | "QA" tab. |
| `delivery` | `false` | "Delivery" tab. |
| `audit` | `false` | (Reserved — no UI wired yet; see §12.) |
| `clipsView` | `false` | (Reserved — clips already surfaced in Archive.) |
| `llmVisibilityPanel` | `true` | LLM Visibility panel (already live today). |
| `analyticsUpgrade` | `false` | "Operations Analytics" section on Analytics. |

**Recommended enablement pairing:** the Pipeline/Operations-Analytics UIs are only meaningful with `BULLETIN_INSTRUMENT_ENABLED=true`; the QA/Coverage panels need a recent collection run; Coverage % needs the source registry seeded (§13).

---

## 6. Feature Flag Matrix

| Capability | FE flag | BE env flag | Data prerequisite | Default visible? |
|---|---|---|---|---|
| Honest status | `honestStatus` | — | briefings exist | No |
| Coverage Assurance panel | `coverageAssurance` | — | a run since restart (`/coverage`) | No |
| Export range fix | `unifiedExport` | — | — | No |
| Endpoint auth | — | `BULLETIN_AUTH_ENABLED` | logged-in user w/ role | No (unauth today) |
| Rate limiting | — | `BULLETIN_RATE_LIMIT_ENABLED` | — | No |
| Audit trail | — | `BULLETIN_AUDIT_ENABLED` | — | No |
| Run instrumentation | — | `BULLETIN_INSTRUMENT_ENABLED` | — | No |
| Operations Console | `opsConsole` | (better with instrument) | recent run | No |
| Collection Pipeline | `collectionPipeline` | `BULLETIN_INSTRUMENT_ENABLED` | recorded runs | No |
| QA Dashboard | `qaReview` | — | recent run | No |
| Delivery Dashboard | `delivery` | — | history | No |
| Honest Coverage % | (panel) | — | **registry seeded + outcomes** | Shows "pending" until both |
| Operations Analytics | `analyticsUpgrade` | `BULLETIN_INSTRUMENT_ENABLED` | recorded runs | No |

---

## 7. API Changes

**All additive. No existing endpoint path, method, request, or response shape was removed or changed** (aside from optional auth dependencies that are inert unless `BULLETIN_AUTH_ENABLED=true`).

**New endpoints**

| Method | Path | Auth (when `BULLETIN_AUTH_ENABLED`) | Purpose |
|---|---|---|---|
| GET | `/api/v1/bulletin/audit/{agency_id}` | contributor | Recent audit events (empty unless audit on). |
| GET | `/api/v1/bulletin/runs/{agency_id}` | contributor | Persisted run log (empty unless instrument on). |
| GET | `/api/v1/bulletin/runs/{agency_id}/{run_id}` | contributor | One run + per-source outcomes. |
| GET | `/api/v1/bulletin/sources/{agency_id}` | contributor | Expected-source registry (empty until seeded). |
| POST | `/api/v1/bulletin/sources/{agency_id}` | admin | Seed/update registry. |
| GET | `/api/v1/bulletin/coverage-assurance/{agency_id}` | public | Honest coverage; `pending_instrumentation` by default. |

**Existing endpoints given optional (flag-gated) authorization**

| Endpoint | Role floor |
|---|---|
| `POST /refresh/{agency_id}` | contributor |
| `POST /run/{agency_id}`, `POST /run/{agency_id}/sync` | contributor |
| `POST /collect/{agency_id}` | contributor (+ rate limit) |
| `POST /llm-visibility/{agency_id}` | contributor |
| `POST /send/{agency_id}/{briefing_id}` | qalead (+ rate limit) |
| `POST /briefings/{briefing_id}/approve` | qalead |
| `POST /agencies` | admin |
| `POST /admin/purge-articles` | admin |

Downloads/preview (`/download`, `/download-excel`, `/briefings/{id}/preview|pdf|excel`) remain **public** (unchanged), because browser `window.open` cannot carry a bearer header.

---

## 8. Database Changes

**All additive — 5 tables via `CREATE TABLE IF NOT EXISTS` (idempotent), created in Phase 0. No existing table/column/index altered or dropped.**

| Table | Written by | Status |
|---|---|---|
| `bulletin_run_log` | `save_run_log` (Phase 4) | Active when `BULLETIN_INSTRUMENT_ENABLED`. |
| `bulletin_source_outcome` | `save_source_outcomes` (Phase 4) | Active when instrument on (succeeded sources only). |
| `bulletin_source_registry` | `save_source_registry` (Phase 6) | Active via `POST /sources`; **empty until seeded**. |
| `bulletin_delivery_log` | — | **Created but no writer yet** (C3 pending — see §12/§13). |
| `bulletin_audit_log` | `save_audit` (Phase 3) | Active when `BULLETIN_AUDIT_ENABLED`. |

Indexes added: agency/run/entity/event lookups on the above. No migration tool required — creation happens on `init_store`. Pre-existing tables (`bulletin_articles`, `bulletin_briefings`) unchanged.

---

## 9. Security Checklist

| Item | Status |
|---|---|
| Endpoint authorization capability | ✅ Implemented (shared `require_role`), **default OFF** |
| **Endpoints authenticated in default config** | ❌ **No** — unauthenticated until `BULLETIN_AUTH_ENABLED=true` (same as pre-existing behavior; this release adds the capability, not the enforcement) |
| Role model reused (no new auth code) | ✅ Imports `app.core.security`; auth module unmodified |
| Rate limiting on costly endpoints | ✅ Implemented (`/collect`, `/send`), default OFF, in-memory per-process |
| Audit trail | ✅ Implemented (append-only), default OFF |
| Secrets in code | ✅ None — all config via env |
| Input validation on new POSTs | ✅ Pydantic models (`SourceRegistryItem`) |
| PII/secrets in logs or audit rows | ✅ None introduced (audit stores event metadata, actor = `"api"`, no tokens) |
| Downloads/preview exposure | ⚠️ Remain public by design (unchanged from today) |
| Per-user attribution in audit/handlers | ❌ Not yet — actor is `"api"`; handler does not resolve the calling user (see §12) |
| Formal security review / pen-test | ❌ Not performed in this work |

**Recommended before production exposure:** enable `BULLETIN_AUTH_ENABLED`, wire the frontend login token (already attached when present), and rotate any credentials shared during development.

---

## 10. Section 508 / Accessibility Checklist

> **No formal 508 audit or assistive-technology testing was performed in this work.** The following reflects what the code does and does not do. Treat unverified items as open.

| Item | Status |
|---|---|
| Interactive controls are real `<button>` elements | ✅ Yes (focusable, Enter/Space activatable) |
| Tab bar exposes ARIA roles | ✅ `role="tablist"`, `role="tab"`, `aria-selected` |
| Iframe has accessible title | ✅ `title="Briefing Preview"` |
| External links use `rel="noopener noreferrer"` | ✅ Yes |
| Form inputs have visible labels | ✅ Text labels on date/keyword/filter inputs |
| Arrow-key navigation within the tablist (WAI-ARIA pattern) | ❌ Not implemented (tabs are click/tab-focus only) |
| Color-contrast ratios verified against WCAG AA | ❌ Not measured |
| Visible focus indicators verified | ❌ Not verified (relies on browser defaults) |
| Screen-reader pass (NVDA/JAWS/VoiceOver) | ❌ Not performed |
| Non-text content alternatives (icons are decorative emoji) | ⚠️ Emoji used decoratively; no `aria-hidden` applied |

**Recommendation:** a dedicated 508 audit (contrast, keyboard, SR testing, focus management) before this is represented as "508 compliant." The hero banner currently displays a static "Section 508: Compliant" credential chip — that text predates this work and should be reconciled with an actual audit.

---

## 11. Test Summary

> Only tests actually executed are listed. **No unit/integration test suite was created or run; no coverage percentage is claimed.**

| Check | Scope | Result |
|---|---|---|
| `npm run build` (Next.js) | Frontend, every phase touching FE | ✅ Pass (Phases 0,1,2,5,7 — "Compiled successfully") |
| `python -m py_compile` | Every backend file changed | ✅ Pass |
| Import smoke test | Router builds; new endpoints registered; store fns present | ✅ Pass |
| **Default-OFF regression** | Flags unset → `guard()==[]`, writers no-op, 34 routes build, live cycle path unchanged | ✅ Pass |
| Visual verification (local dev server, Chrome) | Flag OFF = original UI; Flag ON = Coverage Assurance panel renders + honest "Not Available" | ✅ Pass (2 screenshots captured) |
| Scope diff (`git status --porcelain`) | Only `bulletin` paths changed each phase | ✅ Pass |
| Live data end-to-end (authenticated, deployed) | — | ❌ Not run (local only; localhost is CORS-blocked from prod API) |

---

## 12. Known Limitations

1. **Auth is not enforced by default** — endpoints remain unauthenticated until `BULLETIN_AUTH_ENABLED=true` (this matches pre-existing behavior; the capability is new, the enforcement is opt-in).
2. **Per-source *failure*/timing is not captured** — Phase 4 records only *succeeded* sources (from the coverage report's `sources_scanned`) with item counts. HTTP status, error, response time, and retry counts are not populated; deeper ingest wrapping is required.
3. **Coverage % is intentionally unavailable** until the expected-source registry is seeded **and** per-source outcomes exist. Until then it returns `pending_instrumentation` (by design — no estimate).
4. **`bulletin_delivery_log` has no writer** — the table exists but delivery is currently surfaced from run history (`delivered_at`), not a per-recipient delivery log (C3 not built).
5. **Audit actor is `"api"`** — events are not attributed to a specific user because the gated handlers do not resolve the caller identity.
6. **Rate limiter is in-memory, per-process** — not shared across workers/instances; suitable as a soft guard, not a distributed limit.
7. **Scheduled (1 AM) runs vs API-triggered** — audit hooks fire on API-triggered actions; the scheduler's internal `run_daily_cycle` is instrumented (run-log) but not audited as a "manual" event.
8. **`audit` and `clipsView` FE flags are reserved** — no dedicated UI is wired (audit has a backend read endpoint; clips are already in the Archive tab).
9. **508 not formally audited; live end-to-end not tested** (see §10, §11).
10. **Nothing is pushed/deployed** — all verification is local; production behavior under real load/data is unverified.

---

## 13. Outstanding Enhancements

Ordered roughly by value for reaching a fully "measured" state:

1. **Seed the expected-source registry** (`POST /sources/{agency}` with the researched source catalog) → unlocks honest Coverage % and confidence weighting.
2. **Per-source outcome instrumentation** — wrap each ingest source to capture attempted/succeeded/HTTP status/error/response-ms/retries → real failed-source metrics and a true Coverage % denominator.
3. **Delivery log (C3)** — write `bulletin_delivery_log` on send (per-recipient, SendGrid message id, result); build the compose/preview UI; surface it in the Delivery Dashboard.
4. **Per-user audit attribution** — pass the authenticated user into gated handlers so audit `actor` is the real user, not `"api"`.
5. **QA per-item actions** — exclude/approve/flag stale-date/missing-URL at the article level in the QA Dashboard.
6. **Distributed rate limiting** — move the limiter to a shared store (e.g., Redis) if multi-instance.
7. **Formal 508 audit** — contrast, keyboard/arrow-key tablist pattern, focus indicators, screen-reader pass; reconcile the "Section 508: Compliant" chip with results.
8. **Automated test suite** — unit tests for `deriveStatus`, coverage-assurance math, guard/rate-limit behavior; API tests for the new endpoints.
9. **Reconcile per-repo tag distribution** — optionally mirror every phase tag into both repos for a uniform release matrix.
10. **Housekeeping** — decide whether `docs/` and `pv.html` (untracked in backend) should be committed or git-ignored.

---

*End of FCC Bulletin v1.0 Release Package. All statements herein reflect the actual state of the local repositories as of 2026-07-07; nothing is deployed.*
