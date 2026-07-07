# FCC Bulletin v1.0 — System Architecture

**Module:** FCC News Bulletin
**Prepared:** 2026-07-07
**Scope:** This document describes the FCC Bulletin module only (`app/bulletin_intelligence/**`, `frontend/src/app/bulletin/**`). Shared platform concerns (global auth, TEFCA, other agency modules) are referenced only where the Bulletin depends on them.

> Honesty note: where an internal algorithm's exact implementation is not fully asserted by this document, its **observable behavior and intent** are described rather than invented detail. Nothing here claims a metric, model behavior, or test result that was not verified.

---

## 1. Executive Overview

The FCC Bulletin is an AI-assisted media-intelligence module that collects FCC-relevant news from RSS/feeds, deduplicates and classifies it, generates a daily intelligence briefing, and makes that briefing available in the web app and via Word/Excel/HTML export and email delivery. It maintains a rolling archive and exposes analytics.

v1.0 modernizes the module across 8 phases (0–7): it was componentized, hardened with **optional** authorization/audit/instrumentation, given operational dashboards, and fitted with an **honest** coverage-assurance model. Every capability added in this release is **feature-flagged and defaults OFF**, so the production behavior is unchanged until each is deliberately enabled.

**Operating model:** briefings are generated "live" (status `delivered` on generation; no approval gate). A daily scheduled cycle runs ~1 AM ET (when the scheduler is enabled); briefings can also be collected on demand from the UI.

---

## 2. High-Level Architecture

```
        ┌────────────────────────── Browser ──────────────────────────┐
        │  Next.js App (Vercel)  /bulletin                             │
        │  page.js shell → tab components → fetch API                  │
        └───────────────┬──────────────────────────────────────────────┘
                        │ HTTPS  (JWT attached to gated calls when present)
                        ▼
        ┌────────────────────────── FastAPI (Railway) ────────────────┐
        │  /api/v1/bulletin/*  routes.py                               │
        │   ├─ auth.py (flag-gated require_role)                        │
        │   ├─ audit.py (flag-gated)                                    │
        │   ├─ engine.py  ── run_daily_cycle (collect→process→brief)    │
        │   │     └─ instrumentation.py (flag-gated record_run)         │
        │   ├─ clustering.py, bulletin_download_routes.py               │
        │   └─ bulletin_store.py  (async persistence)                  │
        │  scheduler.py  ── 1 AM ET cycle + watchdog (ENABLE_SCHEDULER) │
        └───────────────┬───────────────────────┬─────────────────────┘
                        │                        │
                        ▼                        ▼
                 PostgreSQL                External: RSS/feeds,
                 (bulletin_* tables)       Anthropic (Claude), email
```

- **Frontend:** Next.js 16 App Router (React 18), deployed to Vercel (`app.docuaction.io`).
- **Backend:** FastAPI (Python 3.12), deployed to Railway (`api.docuaction.io`, service `docuaction-backend`).
- **Data:** PostgreSQL (async via SQLAlchemy/asyncpg).
- **External:** RSS/Atom feeds (FCC + major outlets), Anthropic Claude (classification/summarization), email (via `httpx` HTTP call — the `sendgrid` library is **not** installed).

---

## 3. Frontend Architecture

### 3.1 Components

| File | Role |
|---|---|
| `page.js` | Thin shell (`BulletinIntelligencePage`): agency/tab state, hero header, tab nav, tab routing. |
| `lib/constants.js` | `API` base, `T` palette, label/color maps, helpers (`deriveStatus`, `authHeaders`, `fmtDate`, `isValidArticle`), style functions. |
| `components/shared.js` | Presentational primitives `Badge`, `StatCard`. |
| `components/DailyBriefingTab.js` | Day-range briefing view + Word/Excel/HTML downloads. |
| `components/HistoryTab.js` | Run history + preview/PDF/Excel + live/demo triggers. |
| `components/ArchiveTab.js` | 12-month archive search/filters + broadcast clips. |
| `components/AnalyticsTab.js` | Archive stats, LLM visibility, (flag) Operations Analytics. |
| `components/AgenciesTab.js` | Registered agency cards. |
| `components/CoverageAssurance.js` | (flag) Honest coverage panel over `/coverage`. |
| `components/OpsConsole.js` | (flag) Morning Operations Console. |
| `components/CollectionPipeline.js` | (flag) Run log + per-source outcomes. |
| `components/QaDashboard.js` | (flag) Coverage-level QA checks. |
| `components/DeliveryDashboard.js` | (flag) Delivery state from history. |

`AppLayout` (shared component) wraps the page for the global sidebar/shell.

### 3.2 Pages

Single route: `/bulletin` (App Router `page.js`). All views are tabs within this one page (no sub-routes). Access control is via the shared `AppLayout`/module gating.

### 3.3 Feature Flags

Central module `config/featureFlags.js` exports `BULLETIN_FLAGS` and `flag(name)`. Flags are read synchronously at render; flipping a flag reveals its UI. See §10.

### 3.4 UI Flow

1. Page mounts → fetches `/health` and `/agencies`.
2. User selects agency + tab.
3. Tab component fetches its data from `/api/v1/bulletin/*`.
4. Read operations render tables/cards; downloads use `window.open` (GET); collection actions use `POST` (with JWT attached when present) and then poll `/archive` or `/history` for completion.
5. Flag-gated tabs/panels appear only when their flag is on and degrade to "Not Available" when data is absent.

---

## 4. Backend Architecture

### 4.1 Services (modules)

| Module | Responsibility |
|---|---|
| `routes.py` | HTTP API surface (`/api/v1/bulletin/*`). |
| `engine.py` | Collection + processing engine (`run_daily_cycle` and helpers). |
| `clustering.py` | Related-story clustering. |
| `bulletin_download_routes.py` | Word/Excel/HTML export endpoints. |
| `bulletin_store.py` | Async PostgreSQL persistence + schema (`init_store`). |
| `scheduler.py` | Daily 1 AM ET cycle, watchdog, retry. |
| `auth.py` | Flag-gated `require_role` guards (Phase 2). |
| `audit.py` | Flag-gated audit logging (Phase 3). |
| `instrumentation.py` | Flag-gated run/source instrumentation (Phase 4). |

### 4.2 Collection Engine

`engine.py::run_daily_cycle(agency_id, ...)` orchestrates the lifecycle (see §5). Feeds are defined in-engine: `FCC_RSS_FEEDS` (ungated, always attempted) and `MAJOR_OUTLET_FEEDS` (gated). RSS retrieval is concurrent. A cost/concurrency guard (`_running_cycles`) prevents overlapping cycles per agency. The date window is computed in Eastern Time to match FCC business hours.

### 4.3 AI Processing

Claude models (Haiku + Sonnet) are used to classify articles into FCC topic categories, assign relevance (a 3-tier relevance model), generate summaries, and support related-story clustering; paywall/subscription flags are captured. *(Exact prompt/model routing lives in the engine; this document describes the processing role, not the prompt internals.)*

### 4.4 QA

QA in v1.0 is **coverage-level**: the engine's coverage report surfaces missing-category warnings, duplicate counts, subscription-required counts, and in-briefing/rejected counts. The QA Dashboard (flag) presents these. Per-item QA actions are not yet implemented (see §14).

### 4.5 Delivery

`send_briefing_email`/`deliver_briefing` render and send the briefing by email over an HTTP call (`httpx`). Delivery can be automatic (cycle `auto_deliver`) or manual (`POST /send`). Per-recipient delivery logging is not yet persisted (the `bulletin_delivery_log` table exists without a writer).

### 4.6 Audit

`audit.py::audit(...)` (flag-gated, best-effort, never raises) appends immutable rows to `bulletin_audit_log` for collection/delivery/manual events; read via `GET /audit/{agency}`.

### 4.7 Instrumentation

`instrumentation.py::record_run(...)` (flag-gated, best-effort) persists one `bulletin_run_log` row (funnel + timing) per cycle and per-source outcome rows derived from the coverage report's succeeded sources. Read via `GET /runs/{agency}` and `/runs/{agency}/{run_id}`.

---

## 5. Collection Workflow (lifecycle)

1. **Trigger** — scheduler (~1 AM ET, `ENABLE_SCHEDULER=true`) or API (`POST /collect`, `POST /run`, `POST /refresh`).
2. **Concurrency guard** — abort if a cycle for the agency is already running.
3. **Fetch feeds** — FCC RSS (always) + major outlets (if enabled), concurrently, over the Eastern-Time lookback window.
4. **Parse & date-filter** — extract ISO/Atom publish dates; treat stale/unverifiable dates as stale (not stamped `now`).
5. **Ingest** — assemble the article pool.
6. **Deduplicate** — remove near-duplicates.
7. **AI classify/summarize** — topic + relevance tier + summary; flag paywall/subscription.
8. **Cluster** — group related stories.
9. **Assemble briefing** — apply caps, FCC category structure, leadership/chairman prefix.
10. **Coverage report** — compute `_build_coverage_report` (sources scanned, collected, in-briefing, rejected, dupes, subscription, missing categories).
11. **Persist** — write articles + briefing to `bulletin_store`.
12. **Instrument/audit (optional)** — `record_run` + `audit` if their flags are on (best-effort).
13. **Deliver (optional)** — email now (auto) or later (manual `POST /send`).

---

## 6. Processing Workflow

| Stage | What happens | Output |
|---|---|---|
| Ingestion | Feeds fetched/parsed; date-windowed | Raw article pool |
| Deduplication | Near-duplicate removal | Unique set |
| AI summarization | Claude generates concise summaries | Per-article summary |
| Classification | Topic + 3-tier relevance; paywall flags | Labeled articles |
| QA | Coverage report; missing-category / subscription / dupe signals | QA signals |
| Export | Word / Excel (QA sheet) / HTML email render on demand | Downloadable artifacts |
| Delivery | Email send (auto or manual) | Delivered briefing |

Funnel counters (ingested → after-dedup → in-briefing → rejected) are captured in the coverage report and, when instrumentation is on, persisted to `bulletin_run_log`.

---

## 7. Coverage Assurance Design

**Purpose:** answer "did we cover the sources we were supposed to?" **honestly**, without inventing a number.

**Inputs**
- **Expected-source registry** (`bulletin_source_registry`, seeded via `POST /sources`): the enabled sources that *should* be collected, each with an `importance_weight`.
- **Per-source outcomes** (`bulletin_source_outcome`, from the latest run): sources that **succeeded**, with item counts.

**Computation (`GET /coverage-assurance/{agency}`)**
```
expected  = [s in registry where enabled]
outcomes  = source_outcomes for the latest run
succeeded = { o.source for o in outcomes if o.succeeded }

if expected and outcomes:
    coverage_pct        = 100 * |{ s in expected : s.name in succeeded }| / |expected|
    coverage_confidence = 100 * Σ importance_weight(covered) / Σ importance_weight(expected)
    status = "measured"
else:
    coverage_pct = null
    coverage_confidence = null
    status = "pending_instrumentation"
```

**Honesty guarantees**
- Coverage % is computed **only** when both the registry and per-source outcomes exist.
- The registry ships **empty**, so by default the endpoint returns **`pending_instrumentation`** with `coverage_pct = null`. The UI shows **"Not Yet Instrumented"** — never `0%`, never an estimate.
- Current per-source instrumentation records **succeeded** sources only; per-source **failure** capture is pending (§14), so even once a registry is seeded, "coverage confidence" should be read as a floor, not a certified completeness score.
- Primary-source backstop: FCC.gov, Federal Register, and ECFS are always collected, so official FCC actions are captured regardless of media pickup.

---

## 8. Database Design (bulletin_* only)

**Pre-existing (unchanged):** `bulletin_articles` (article_id PK, agency_id, published_at, ingested_at, data JSON), `bulletin_briefings` (briefing_id PK, agency_id, generated_at, data JSON).

**Added in v1.0 (all additive, `CREATE TABLE IF NOT EXISTS`):**

| Table | Key | Purpose | Writer |
|---|---|---|---|
| `bulletin_run_log` | run_id | Per-cycle funnel + timing + coverage JSON | `save_run_log` (Phase 4) |
| `bulletin_source_outcome` | id | Per-source outcome for a run (succeeded/items) | `save_source_outcomes` (Phase 4) |
| `bulletin_source_registry` | source_id | Expected sources (Coverage % denominator) | `save_source_registry` (Phase 6) |
| `bulletin_delivery_log` | id | Per-recipient delivery record | **none yet (pending C3)** |
| `bulletin_audit_log` | id | Immutable event trail | `save_audit` (Phase 3) |

**Relationships**
```
bulletin_run_log (1) ────< (many) bulletin_source_outcome     [run_id]
bulletin_source_registry  ── (compared by name to succeeded outcomes for Coverage %)
bulletin_delivery_log     ──> bulletin_briefings              [briefing_id]  (writer pending)
bulletin_audit_log        ── standalone (entity_type/entity_id reference briefings, archive, etc.)
bulletin_articles / bulletin_briefings  ── keyed by agency_id
```

Indexes: agency (run_log, articles, briefings), run_id (source_outcome), briefing_id (delivery_log), entity/event (audit_log). No existing table/column/index was altered or dropped.

---

## 9. API Architecture

**Base:** `/api/v1/bulletin`. Router: `routes.py` (+ download routes).

**Endpoint classes**
- **Public reads:** `/health`, `/agencies`, `/coverage/{a}`, `/coverage-assurance/{a}`, `/latest/{a}`, `/today/{a}`, `/history/{a}`, `/archive/{a}*`, `/briefings/{id}/preview|pdf|excel`, downloads.
- **State-changing / costly (optionally gated):** `/collect`, `/run`, `/run/sync`, `/refresh`, `/send`, `/briefings/{id}/approve`, `/agencies` (POST), `/admin/purge-articles`, `/llm-visibility`.
- **v1.0 read endpoints (optionally gated):** `/audit/{a}`, `/runs/{a}`, `/runs/{a}/{run_id}`, `/sources/{a}` (GET), `/sources/{a}` (POST, admin).

**Authentication** — reuses the shared `app.core.security` (`require_role`, JWT bearer). The Bulletin never reimplements auth.

**Authorization** — flag-gated per-endpoint role floors (via `auth.py::guard`): contributor (collect/run/refresh/llm/audit/runs/sources-read), qalead (send/approve), admin (agencies/purge/sources-write). When `BULLETIN_AUTH_ENABLED` is off, `guard()` returns `[]` (no dependency) → identical to today.

**Rate limiting** — `auth.py::rate_limit` (flag-gated, in-memory per-process) on `/collect` and `/send`; 429 when the per-client hourly cap is exceeded.

**Audit** — gated handlers emit best-effort audit events (§4.6).

---

## 10. Feature Flag Matrix

**Frontend** (`config/featureFlags.js`):

| Flag | Default | Purpose | Depends on |
|---|---|---|---|
| `honestStatus` | false | Derived Live/Delivered/Failed status | briefings present |
| `coverageAssurance` | false | Coverage panel on Daily Briefing | `/coverage` (recent run) |
| `unifiedExport` | false | Word/Excel on custom ranges | — |
| `opsConsole` | false | Operations tab | recent run (better w/ instrument) |
| `collectionPipeline` | false | Pipeline tab | `BULLETIN_INSTRUMENT_ENABLED` |
| `qaReview` | false | QA tab | recent run |
| `delivery` | false | Delivery tab | history |
| `audit` | false | (reserved; no UI wired) | backend audit |
| `clipsView` | false | (reserved; clips already in Archive) | — |
| `llmVisibilityPanel` | **true** | LLM panel (already live) | — |
| `analyticsUpgrade` | false | Operations Analytics section | `BULLETIN_INSTRUMENT_ENABLED` |

**Backend** (env, read at process start):

| Env var | Default | Purpose | Depends on |
|---|---|---|---|
| `BULLETIN_AUTH_ENABLED` | false | Enforce `require_role` | shared auth + logged-in user |
| `BULLETIN_RATE_LIMIT_ENABLED` | false | Rate-limit collect/send | — |
| `BULLETIN_RATE_MAX_PER_HOUR` | 20 | Rate threshold | rate limiting on |
| `BULLETIN_AUDIT_ENABLED` | false | Write audit rows | — |
| `BULLETIN_INSTRUMENT_ENABLED` | false | Persist run/source data | — |

---

## 11. Deployment Architecture

| Layer | Platform | Notes |
|---|---|---|
| Frontend | Vercel | `app.docuaction.io`; env `NEXT_PUBLIC_API_URL` → api.docuaction.io |
| Backend | Railway | service `docuaction-backend`; `api.docuaction.io`; requires `SECRET_KEY`, `DATABASE_URL`; host must be in `ALLOWED_HOSTS`/CORS allowlist |
| Database | PostgreSQL | prod Postgres (public URL); tables auto-create via `init_store` |
| Email | `httpx` HTTP call | **`sendgrid` library is not installed**; delivery uses a direct HTTP request |
| Scheduler | in-process | `ENABLE_SCHEDULER=true` runs the 1 AM ET cycle + watchdog + retry |
| Env vars (v1.0) | — | see §10 backend table (all default off) |

Deploy order: backend (schema auto-creates, flags off = no change) → frontend (flags off = original UI) → enable capabilities incrementally.

---

## 12. Security Architecture

- **Authentication:** shared JWT bearer (`app.core.security`); Bulletin imports, never reimplements.
- **Authorization:** flag-gated `require_role` floors per endpoint (§9). **Default OFF → endpoints are currently unauthenticated** (matches pre-v1.0 behavior; v1.0 adds the capability, not the enforcement).
- **Secrets:** none in code; all via env. `SECRET_KEY`/`DATABASE_URL` required app-wide.
- **Logging:** application logs; audit rows store event metadata only (actor = `"api"`, no tokens/PII).
- **Rate limiting:** flag-gated, in-memory per-process (soft guard; not distributed).
- **Audit:** flag-gated append-only trail.
- **Known limitations:** downloads/preview remain public by design; audit has no per-user attribution yet; no formal security review/pen-test performed; auth not enabled by default.

---

## 13. Performance Architecture

| Area | Characteristic | Likely bottleneck |
|---|---|---|
| Collection | Concurrent RSS fetch; cycle ~1–2 min (observed operationally) | Slow/erroring feeds; network latency |
| Processing | Per-article AI classification/summarization | **AI call throughput/latency** (dominant cost) |
| Dedup/cluster | In-memory over the cycle's pool | Large pools; O(n²)-style comparisons if unbounded |
| Exports | On-demand render (Word/Excel/HTML) | Large briefings; synchronous render |
| Delivery | HTTP email send | Provider latency; large recipient lists |
| Instrumentation/audit | Best-effort async writes | Negligible; never blocks the cycle |

Guards in place: per-agency concurrency guard, output caps (per-feed and render caps), Eastern-Time date window, best-effort (non-blocking) instrumentation.

---

## 14. Future Roadmap

**Near-term (unlock "measured" coverage)**
1. Seed the expected-source registry (`POST /sources`) with the researched catalog.
2. Per-source **failure**/timing instrumentation (wrap each ingest source: status/error/response-ms/retries).
3. Delivery log (C3): write `bulletin_delivery_log` on send; build compose/preview UI.

**Quality / hardening**
4. Per-user audit attribution (pass authenticated user into gated handlers).
5. Per-item QA actions (exclude/approve/flag stale-date/missing-URL).
6. Distributed rate limiting (shared store) for multi-instance.
7. Formal 508 audit (contrast, keyboard/arrow-key tablist, focus, screen-reader); reconcile the static "Section 508: Compliant" chip with results.
8. Automated test suite (unit: `deriveStatus`, coverage math, guard/rate-limit; API: new endpoints).

**Technical debt**
9. `audit`/`clipsView` FE flags are reserved without dedicated UI.
10. Reconcile per-repo tag distribution; decide on `docs/` + `pv.html` tracking (see release package §13 / cleanup recommendation).

---

*End of FCC Bulletin v1.0 System Architecture. Statements reflect the actual state of the module as of 2026-07-07. Nothing is deployed.*
