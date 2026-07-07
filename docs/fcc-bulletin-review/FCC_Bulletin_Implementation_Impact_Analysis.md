# FCC Bulletin — Implementation Impact Analysis (Final Approval Gate)

**Purpose:** for every feature in the approved spec, state exactly what already exists vs. what must be built — to avoid duplication, unnecessary refactoring, and regressions.
**Method:** grounded in the real code (line numbers verified). Frozen: TEFCA, healthcare, decision intelligence, case management, shared framework, authentication (import-only), other agency modules.
**No code produced.**

---

## 0. Codebase baseline (verified)

**Frontend:** one file — `frontend/src/app/bulletin/page.js` (931 lines). Components: `DailyBriefingTab` (L95), `HistoryTab` (L404), `ArchiveTab` (L558), `AnalyticsTab` (L701), `AgenciesTab` (L794); shell tab list (L848: Daily Briefing / Run History / 12-Month Archive / Analytics / Agencies). `STATUS_STYLE` incl. `pending_approval` at L36–40. Archive loads `page_size=500` client-side.

**Backend module** `backend/app/bulletin_intelligence/`:
- `routes.py` — `APIRouter(prefix="/api/v1/bulletin")` **no auth dependency**; endpoints incl. `/coverage/{a}`, `/latest`, `/today`, `/collect`, `/send`, `/queue`, `/history`, `/briefings/{id}[/preview|docx|pdf|excel]`, `/archive[/stats|/clips]`, `/admin/last-window`, `/admin/purge-articles`, `/agencies`.
- `bulletin_download_routes.py` — `/download-options`, `/download-excel`, `/briefings/{id}/excel`, `_render_excel_workbook`.
- `engine.py` — `run_daily_cycle` (L2799), `_build_coverage_report` (L782: sources_scanned, source_count, stories_collected, duplicates_removed, in_briefing, rejected, subscription_stories, missing_category_warnings, social_collected), `_cluster_stories` (L2028), `_leadership_prefix` (L1898), `deliver_briefing` (L2499), `send_briefing_email` (L3154), `get_latest_briefing` (L3088), `get_today_briefing` (L3099), `get_briefing_history` (L3058), `get_editorial_queue` (L3044), `hydrate_from_store` (L331), paywall detection (L1909), `is_paywalled` field.
- `clustering.py` — `quality_score`, `cluster_stories`; `scoring.py`, `editorial_rules.py`, `health_monitor.py`, `story_repository.py`, `pdf_generator.py`.
- `bulletin_store.py` — **only** `bulletin_articles` + `bulletin_briefings` tables.
- `scheduler.py` — `start_scheduler`, hourly watchdog, `_run_cycle_with_retry` (MAX_CYCLE_ATTEMPTS=3), `ENABLE_SCHEDULER` gate.

**Pre-existing note (out of scope for these features):** the module contains social ingest (`bluesky_ingest.py`, `reddit_ingest.py`, `youtube_ingest.py`, `gdelt_tv_ingest.py`, `fcc_social_accounts.py`). This contradicts the "editorial/official only" catalog research (Gap G1) but is **not part of any spec feature below**; retiring social is a separate decision — **do not touch as part of this build.**

---

## 1. Feature Inventory

Status key: **✅ Implemented · ◧ Partial · ⊘ Exists-but-Hidden/UI-not-exposed · ⟳ Exists-needs-refactor · ✗ Missing.**
Action: **Surface · Extend · Refactor · New.**

| Feat | Feature | Status | Existing files / functions / endpoints | Existing DB | Reusable logic | Gap | Action |
|---|---|---|---|---|---|---|---|
| **C1** | Honest status (kill `pending_approval`) | ⟳ | FE `page.js` `STATUS_STYLE` L36; BE already sets `status='delivered'` on generation | briefings.status/delivered_at | derive-status rule | FE still styles `pending_approval` | **Refactor** (FE display only) |
| **C2** | API authentication + rate limit | ✗ | `routes.py`/`bulletin_download_routes.py` (no auth); shared `require_role` (import) | — | shared auth (reuse) | no auth on any bulletin endpoint | **New** (apply shared auth) |
| **C3** | Delivery screen + log + validation | ◧/⊘ | BE `send_briefing_email` L3154, `deliver_briefing` L2499; `POST /send` | briefings.delivered_at/recipients | send logic, SendGrid 403 surfaced | no delivery log table, no screen, no pre-send validation | **Extend** (send→write log) + **New** (`bulletin_delivery_log`, screen) |
| **C4** | Section 508 pass | ✗ | FE `page.js` (inline styles, emoji, color-only) | — | Fluent tokens | not 508-conformant | **New/Refactor** (FE) |
| **C5** | Coverage Assurance (available subset) | ⊘ | BE `_build_coverage_report` L782; `GET /coverage/{a}` | — | full coverage dict already computed | not shown in UI | **Surface** (FE panel) |
| **C6** | Audit trail | ✗ | none | — | pattern from platform (do NOT touch TEFCA) | no table/writes/view | **New** (`bulletin_audit_log`) |
| **H1** | Morning Ops Console | ✗ | composes `/latest`,`/today`,`/coverage`,`/health`(scheduler) | briefings | existing endpoints | no console screen | **New** (FE) + **Surface** (data) |
| **H2** | Per-source outcome instrumentation | ✗ | `engine.py` drop_reason ~L990 (debug log only); `health_monitor.py` | — | drop_reason capture, ingest loop | not persisted/aggregated per source | **Extend** (persist) + **New** (`bulletin_source_outcome`) |
| **H3** | Source Registry + honest Coverage % | ✗ | feed lists in `engine.py`/`fcc_feeds_extended.py`; catalog research docs | — | existing feed lists seed registry | no registry, no expected-sources, no % | **New** (`bulletin_source_registry` + calc) + **Extend** (seed) |
| **H4** | Collection Pipeline screen | ✗ | — (needs `/collect/status`) | — | H2 data | no screen, no progress endpoint | **New** (FE + status endpoint) |
| **H5** | Run History funnel + status + retry | ◧ | `run_daily_cycle` returns funnel; `get_briefing_history` L3058; FE `HistoryTab` L404 | briefings only | funnel counts computed | per-run not persisted (no run_log); FE lacks funnel/status/retry | **New** (`bulletin_run_log`) + **Extend** (persist) + **Refactor** (FE) |
| **H6** | Export metadata + unified + range fix | ⟳ | `/docx`,`/pdf`,`/excel`,`/download-*`; `_render_excel_workbook`; `pdf_generator.py`; FE range-disable L331 | — | generators exist | no metadata block; FE disables Word/Excel on range | **Extend** (generators) + **Refactor** (FE) |
| **M1** | QA Review screen | ◧ | `get_editorial_queue` L3044 (legacy), coverage warnings, `is_paywalled`, `_cluster_stories`, `_leadership_prefix` | — | all QA checks computable | no screen/actions/qa_status | **Surface** (checks) + **New** (screen, `qa_*` cols) |
| **M2** | Analytics upgrade (SLA/trends) | ◧ | FE `AnalyticsTab` L701 (2 charts) | needs run_log | topic/volume already charted | no SLA/trend/dedupe; depends H5 | **Extend** (FE) + **Surface** (run_log) |
| **M3** | Archive facets/export/saved | ⊘ | FE `ArchiveTab` L558; `/archive`,`/archive/stats`,`/archive/clips` | — | search + stats + clips endpoints exist | facets/result-export/saved not in UI | **Surface** (stats/clips) + **Extend** (FE) |
| **M4** | Agency config editing | ◧ | `POST/GET /agencies`; `AgencyConfig` (distribution_list, delivery_time, caps) | agencies (in-mem/registered) | config model exists | no update endpoint; FE read-only | **Extend** (add PUT) + **Refactor** (FE) |
| **M5** | AI confidence + inline clustering/relevance chips | ◧ | `relevance_score`, `quality_score`, `_cluster_stories` | — | scores + clusters exist | distinct AI-confidence missing; not surfaced inline | **Surface** (cluster/relevance) + **New** (confidence) |
| **M6** | Keyboard shortcuts | ✗ | — | — | — | none | **New** (FE; dep C4) |
| **L1** | Saved views/searches + alerting | ✗ | — | — | — | none | **New** |
| **L2** | Broadcast clips view | ⊘ | `GET /archive/{a}/clips` | — | endpoint exists | not surfaced | **Surface** |
| **L3** | LLM-visibility panel | ⊘ | `POST /llm-visibility/{a}` | — | endpoint exists | not surfaced | **Surface** |
| **L4** | Componentization / token cleanup | ⟳ | `page.js` (931-line monolith) | — | — | inline styles, one file | **Refactor** (in-module) |
| **L5** | Export cover/handling markings | ✗ | generators (H6) | — | generators | none | **Extend** |

**Reuse headline:** **C5, L2, L3 are pure Surface** (endpoints already return the data). **C1, H6, L4 are Refactor** (no new logic). The genuinely new engineering is concentrated in **C2 (auth), C6 (audit), H2/H3 (source instrumentation + honest Coverage %), and the new screens (H1/H4/M1/C3-delivery)** — and the five additive `bulletin_*` tables.

---

## 2. Code Impact Analysis

### 2.1 Files to MODIFY (in-module only)
| File | For | Change type | Regression risk |
|---|---|---|---|
| `frontend/src/app/bulletin/page.js` (→ split into components during L4) | C1,C4,C5,H1,H4,H5,H6,M1,M2,M3,M5,M6 | additive components + display fixes | **Low–Med** (single shared file; mitigate by componentizing behind flags) |
| `backend/.../routes.py` | C2 (auth), H5/H2/C3/C6/M4 (new endpoints appended), M3 surface | append endpoints + add `Depends(require_role)` | **Med** (auth changes affect all callers — see 2.4) |
| `backend/.../bulletin_download_routes.py` | C2, H6 (metadata) | auth dep + metadata in exports | Low–Med |
| `backend/.../engine.py` | H2 (persist source outcomes), H5 (persist run funnel/timing), H6 (metadata), M5 (confidence) | **additive** capture inside `run_daily_cycle`/ingest; return-dict additions | **Med** (touches the live collection path — must be additive & flag-guarded) |
| `backend/.../bulletin_store.py` | C6,H2,H3,H5,C3 (new table DDL + save fns) | additive DDL (`CREATE TABLE IF NOT EXISTS`) + writers | **Low** (new tables inert until used) |
| `backend/.../scheduler.py` | audit hook on run start/finish (C6) | additive log call | Low |
| `backend/.../pdf_generator.py`, excel `_render_excel_workbook`, docx builder | H6/L5 metadata block | additive header/cover | Low |

### 2.2 NEW files (all in-module / additive)
- FE components: `OpsConsole`, `CollectionPipeline`, `QaReview`, `Delivery`, `Audit`, `Export` (+ `CoverageAssurance`, `RunDetailDrawer`, shared `bulletin/lib/api.js`).
- BE: `source_registry.py` (registry + coverage-% calc), `audit.py` (bulletin audit writer), optional `run_log.py` helpers. New `bulletin_*` tables via `bulletin_store.py` DDL (§0.2 of spec).

### 2.3 Files that REMAIN UNTOUCHED (guaranteed)
- **All frozen modules:** TEFCA (`app/Tefca/**`), healthcare, decision intelligence, case management, shared components, `app/core/security.py` (import only), `app/main.py`, other agency modules.
- **In-module untouched:** social ingest (`bluesky/reddit/youtube/gdelt_tv/fcc_social_accounts`), `boolean_filter.py`, `fcc_boolean_search.py`, `fcc_keywords_extended.py`, `fcc_sources.py`, `story_repository.py`, `scoring.py`, `editorial_rules.py`, `clustering.py`, `cspan_fcc_ingest.py`, `gdelt_doc_ingest.py`, `test_bulletin_enhancements.py` (extend tests, don't rewrite).

### 2.4 Highest regression risks + mitigations
1. **C2 auth (Med):** adding auth to endpoints the current UI calls **unauthenticated** will 401 the app until the FE sends a token. *Mitigation:* land FE token-wiring first (or same PR); keep read-only public endpoints (`/health`,`/status`,`/latest/*/preview`) open; feature-flag auth per endpoint; regression test existing calls.
2. **engine.py collection path (Med):** H2/H5 edit `run_daily_cycle`. *Mitigation:* additive-only (append to return dict / write outcome rows in a try/except that never breaks the cycle — mirror the existing best-effort persistence pattern); flag-guard; golden-run regression comparing article counts pre/post.
3. **page.js monolith (Med):** many features touch one file. *Mitigation:* componentize (L4) early so features are isolated files; feature flags per tab.
4. **New tables (Low):** additive, nullable, `IF NOT EXISTS`; inert until their feature is enabled.

---

## 3. Implementation order (each phase independently testable, deployable, reversible)

Every phase ships behind a **module feature flag**; additive tables are inert until enabled; no destructive migrations; each phase has its own regression scope-diff (only `bulletin_intelligence/**` + `bulletin/**` + additive `bulletin_*` tables).

**Phase 0 — Foundations (safe, no user-visible risk)**
- L4 (componentize `page.js` into `bulletin/components/*`, no behavior change) + shared `bulletin/lib/api.js`.
- `bulletin_store.py`: add the 5 additive tables (`IF NOT EXISTS`), inert.
- *Reversible:* revert component split; tables unused.

**Phase 1 — Correctness & pure surfacing (highest ROI, lowest risk)**
- **C1** honest status · **C5** Coverage-Assurance panel (Surface `/coverage`) · **L2/L3** clips + LLM-visibility panels · **H6-partial** fix Export range bug.
- *No backend logic change.* Independently deployable; revert = hide components.

**Phase 2 — Security (gate before exposing actions)**
- **C2** auth + rate-limit (BE) + FE token wiring. Deploy together.
- *Reversible:* per-endpoint auth flag off.

**Phase 3 — Audit + Delivery (record of authority)**
- **C6** audit table + writers + Audit view · **C3** delivery log + Delivery screen + pre-send validation (Extend `send_briefing_email` to write log).
- *Reversible:* stop-writes flag; delivery still works via existing `/send`.

**Phase 4 — Run instrumentation**
- **H5** `bulletin_run_log` + persist funnel/timing (Extend `run_daily_cycle`) + Run History refactor · then **H2** per-source outcomes.
- *Reversible:* instrumentation is best-effort; collection unaffected if disabled.

**Phase 5 — Operational surfaces**
- **H1** Ops Console (composes Phase 1–4 data) · **H4** Collection Pipeline (needs `/collect/status`) · **M1** QA Review.

**Phase 6 — Honest Coverage % (only after H2)**
- **H3** Source Registry (seed from existing feed lists) + Coverage % = succeeded/expected. **Until this phase, Coverage % renders "pending instrumentation" — never a number.**

**Phase 7 — Enrichment**
- **M2** Analytics upgrade · **M3** Archive facets/export/saved · **M4** Agency config editing · **M5** AI-confidence + inline clustering · **M6** keyboard · **H6** export metadata block · **L1/L5**.

---

## 4. Safe-implementation adherence (self-check)

| Rule | How this plan complies |
|---|---|
| Never replace working functionality | Phase 1 is pure Surface/Refactor; collection/scheduler/send left intact and only additively extended |
| Prefer extending existing | C3/H2/H5/H6/M4 = Extend; existing functions reused, not rewritten |
| Prefer exposing existing | C5/L2/L3/M3 = Surface (endpoints already return data) |
| Prefer additive | 5 new tables (`IF NOT EXISTS`), new endpoints appended, new FE components; nothing removed |
| Avoid unnecessary rewrites | Only L4 componentization (behavior-preserving) touches structure; monolith not logically rewritten |
| Minimize churn | Feature flags + per-phase scope-diff; social/other in-module files untouched |
| Frozen modules | TEFCA/healthcare/DI/CM/shared/auth/agency-modules never modified (auth imported only) |

---

## 5. Approval gate

- **Pure-surface / refactor (near-zero risk):** C1, C5, L2, L3, H6-rangefix, L4 — recommend approving Phase 0–1 to start immediately.
- **New engineering (needs the additive tables + care on the collection path):** C2, C3, C6, H2, H3, H5 — recommend approving as sequenced Phases 2–6.
- **Honesty gate:** no Coverage % ships before Phase 6; enforced in spec §8 + a regression test.
- **Regression guarantee:** every phase verified by a diff showing only in-scope files changed.

**Confirmed:** no duplicate functionality is proposed (existing coverage report, clustering, leadership, paywall, send, scheduler are reused, not rebuilt); no unnecessary refactoring (only behavior-preserving componentization); frozen modules untouched.

---

*No code was written. This is the final impact analysis. Awaiting implementation approval — recommend authorizing Phase 0–1 first.*
