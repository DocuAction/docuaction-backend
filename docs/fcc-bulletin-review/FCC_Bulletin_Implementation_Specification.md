# FCC Bulletin — Implementation Specification (Authoritative)

**Status:** Build-ready spec. Developers implement directly from this; no product decisions required.
**Scope (FROZEN outside this list):** FCC News Bulletin module only — `frontend/src/app/bulletin/**` (may be componentized within the module) and `backend/app/bulletin_intelligence/**`. **Additive-only** elsewhere: new endpoints under `/api/v1/bulletin/*`, new tables prefixed `bulletin_*`. **Do not modify** TEFCA, healthcare, other agency modules, shared components (import only), existing DB schema (except new additive `bulletin_*` tables and new nullable columns on `bulletin_*` tables), existing non-bulletin APIs, or existing auth (import & apply only).
**No code in this document.**

---

## 0. Document control

### 0.1 Endpoint inventory (existing vs. additive)
**Existing (reuse):** `GET /health`, `GET /coverage/{a}`, `POST /refresh/{a}`, `GET/POST /agencies[...]`, `POST /run/{a}[/sync]`, `POST /admin/purge-articles`, `GET /admin/last-window/{a}`, `GET /latest/{a}[/preview]`, `GET /today/{a}`, `POST /collect/{a}`, `POST /send/{a}/{id}`, `GET /queue/{a}`, `GET /history/{a}`, `GET /briefings/{id}[/preview|/docx|/pdf|/excel]`, `GET /archive/{a}[/stats|/clips]`, `POST /llm-visibility/{a}`, `GET /download-options/{a}`, `GET /download-excel/{a}`.
**Additive (to build — §3):** run-status/progress, per-source outcomes, delivery log, audit query, source registry, coverage-assurance summary, QA actions, export-with-metadata.

### 0.2 Additive data model (spec only — no migrations here)
| Table (new, `bulletin_*`) | Purpose | Key columns |
|---|---|---|
| `bulletin_run_log` | one row per collection run | run_id, agency_id, trigger(auto/manual), started_at, finished_at, duration_ms, ingested, after_dedup, in_briefing, rejected, dupes_removed, cluster_count, status(running/completed/failed), error, coverage_json |
| `bulletin_source_outcome` | per-source result per run | run_id, source, type, tier, attempted, succeeded(bool), items, http_status, error, response_ms, retries |
| `bulletin_source_registry` | expected-source list (Coverage % denominator) | source_id, name, type, tier, importance_weight, enabled, method(rss/api/index), url, notes |
| `bulletin_delivery_log` | delivery record-of-authority | id, briefing_id, agency_id, sent_by, sent_at, recipients_json, subject, sendgrid_message_id, result, per_recipient_json |
| `bulletin_audit_log` | append-only audit (§9) | id, ts, actor, event_type, entity_type, entity_id, action, details_json, result |
**New nullable columns on existing `bulletin_briefings`:** `qa_status`(none/passed/passed_with_notes/blocked), `qa_by`, `qa_at`, `editorial_notes_json`, `run_id`.

### 0.3 Global status model (fixes stale `pending_approval`)
Display status is **derived**, not stored raw:
- `Failed` if `status == 'error'`.
- `Delivered` if `delivered_at` is set (non-empty).
- else `Live` (generated & viewable).
**`pending_approval` MUST NOT be displayed anywhere** (live-feed model). Any legacy value renders as `Live`.

### 0.4 Roles (reuse shared `require_role`; do not modify auth)
| Role | Can |
|---|---|
| `viewer` | read briefings, archive, analytics, run history, audit (read) |
| `editor` (reviewer) | + trigger collect/refresh, QA actions (exclude/note/pin/priority), request AI re-summary |
| `qalead` | + set QA status, send/deliver, re-send |
| `program_manager`/`admin` | + agency config, purge, registry edits |

### 0.5 Global UI states (every data view MUST implement)
- **Loading:** skeletons (never spinners-only); disable dependent actions.
- **Empty:** titled empty state + primary action.
- **Error:** typed message (not_found → "Coming soon"; 403 → "Insufficient permissions"; network → "Connection error — Retry"; 401 → redirect `/login`).
- **Success:** toast with concrete result (counts/time), ARIA live-announced.

---

## 1. Global conventions

- **Design tokens:** keep Fluent palette already in `page.js` (navy `#0B3C5D`, blue `#0078D4`, green `#107C10`, amber `#D83B01`, red `#A4262C`); migrate inline styles → tokenized classes during componentization (L4). No decorative UI.
- **Status = icon + text** (never color alone).
- **Timezone:** all operational times display **Eastern Time** with explicit "ET" label (SLA is ET). Store UTC.
- **API client:** shared bulletin fetch wrapper handling 401/403/404/network per §0.5; attaches auth token.
- **Freshness dot rule:** green `<6h`, amber `6–24h`, red `>24h` since last successful run.

---

## 2. Additive API contracts (to build; shapes are authoritative)

> Developers implement these under `/api/v1/bulletin/`. Request/response shapes below are binding. Auth per §0.4.

- `GET /runs/{agency}?limit&status&trigger&from&to` → list from `bulletin_run_log` (see Run History columns §4.4).
- `GET /runs/{agency}/{run_id}` → run detail incl. `source_outcomes[]`, `coverage`, funnel counts, delivery link.
- `GET /collect/{agency}/status` → `{running:bool, run_id, stage, stages:[{name,done,total}], started_at, eta_s}` (progress for async collect).
- `GET /coverage-assurance/{agency}?run_id` → §8 payload (honest; omits Coverage % until instrumented).
- `GET /sources/{agency}` / `PUT /sources/{agency}/{source_id}` → registry read/enable-disable (admin).
- `GET /delivery/{agency}?limit` / `GET /delivery/{agency}/{id}` → delivery log.
- `POST /qa/{agency}/{briefing_id}/action` → `{action:'exclude'|'restore'|'note'|'pin'|'priority'|'resummarize', article_id?, note?}`; writes audit + `editorial_notes_json`.
- `POST /qa/{agency}/{briefing_id}/status` → `{status:'passed'|'passed_with_notes'|'blocked', note?}`.
- `GET /audit/{agency}?event_type&actor&entity&from&to&limit` → audit query.
- `GET /export/{agency}/{briefing_id}?format=word|pdf|excel|html|csv` → artifact **with metadata block** (§7.6).
- **Auth hardening (approved C2):** add `require_role` to `POST /collect`, `POST /send`, `POST /run`, `POST /refresh`, `POST /admin/purge-articles`, registry/QA/delivery POSTs; add rate-limit to `/collect` and `/send`.

---

## 3. SCREEN SPECS

Each screen documents the 15 required dimensions. "Acceptance criteria" are testable and binding.

### 3.1 Morning Operations Console *(new — default landing)*
- **Purpose:** answer, before 8 AM ET, "did today's run happen, is it complete, quality-OK, and delivered?"
- **Roles:** all (read); actions gated per §0.4.
- **Current functionality:** none (new).
- **Approved improvements:** the 6-question console (Blueprint §4).
- **Exact UI behavior:** top status strip (freshness dot + last-run time ET + scheduler ✔/✖ + connector health); 6 KPI tiles (Run status, Coverage subset, Dedupe %, Avg relevance, QA status, Delivery status); Exceptions list (failed sources, low-confidence items, delivery errors) with deep-links; primary actions `Collect now`, `Open QA`, `Send…`. Auto-refresh every 60s; manual refresh.
- **Validation:** none (read); action buttons disabled while a run is `running`.
- **Error handling:** each tile degrades independently (a failed sub-fetch shows "—", not a page crash).
- **Loading/Empty/Success:** skeleton tiles; empty = "No run today yet — Collect now"; success toast on actions.
- **Accessibility:** tiles are headings + text (not color-only); Exceptions is a semantic list; `g o` shortcut.
- **Performance:** single aggregate call preferred; ≤1.5s to first meaningful paint on cached data.
- **Audit:** viewing = none; actions logged.
- **Acceptance criteria:** (a) shows today's run status within 2s; (b) never shows `pending_approval`; (c) Exceptions deep-link to the relevant screen; (d) if no run today, primary CTA triggers collect.

### 3.2 Daily Briefing
- **Purpose:** review the day's FCC stories, clustered, leadership-first, and export/send.
- **Roles:** viewer read; editor collect/QA; qalead send.
- **Current functionality:** day presets (1/2/3/7 + counts), custom From–To, Refresh/Collect, Topic Index, articles grouped by topic, downloads (Word/Excel/HTML).
- **Approved improvements:** inline **clustering** (primary + collapsible similar), **leadership pin** (General/leadership on top), per-article **relevance meter + source-type chip + paywall badge**, in-briefing **search**, **exclude/restore**, freshness timestamp, unified **Export ▾**, fix range-disables-Word/Excel bug.
- **Exact UI behavior:** window selector governs ALL exports (remove the disable-on-range). Story cards: title, outlet, time (ET), topic chip, relevance meter, "+N similar" expander, links; exclude toggles item and records audit. Topic Index rail sticky; clicking scrolls to section. Client-side `isValidArticle` divergence removed — **display exactly the backend's in-briefing set** (single source of truth).
- **Validation:** custom range requires start≤end; export requires ≥1 article.
- **Error handling:** §0.5; if collect in progress, show progress banner not a blank list.
- **Loading/Empty/Success:** skeleton cards; empty = "No articles in this window — widen range or Collect"; success toast on export/exclude.
- **Accessibility:** cards keyboard-navigable (`j/k`), `x` exclude, meters have text values, expander is a real button with `aria-expanded`.
- **Performance:** render ≤500 items virtualized; window change ≤1s on cached data.
- **Audit:** exclude/restore, export, send.
- **Acceptance criteria:** (a) briefing count matches Run History/delivered doc (no client/backend divergence); (b) all export formats honor the active window; (c) leadership stories appear first; (d) excluding an item removes it from subsequent export.

### 3.3 Collection Pipeline *(new)*
- **Purpose:** observability of collection (Sentinel-style).
- **Roles:** viewer read; editor collect; admin registry.
- **Approved improvements:** Blueprint §6.
- **Exact UI behavior:** (1) **Source Registry table** — Source·Type·Tier·Last fetch·Items·Outcome·Avg response·Retries·Enabled (toggle, admin); (2) **Live run panel** — stages Ingest→Dedup→Classify→Cluster→Build, each a progress bar + counts from `GET /collect/{a}/status`, streamed log, Cancel; (3) **Connector health** cards (RSS/NewsAPI/Tavily/GDELT/FCC.gov). Poll status every 3s while running.
- **Validation/Errors:** disable Collect while running; failed sources highlighted; status endpoint failure → "progress unavailable, run continues."
- **States:** empty registry = seed prompt; idle run panel = "No active run."
- **Accessibility:** progress bars have `aria-valuenow`; table semantic; outcome = icon+text.
- **Performance:** registry paginated server-side (scales to 2,000+).
- **Audit:** collect start/finish, per-source failure, registry enable/disable.
- **Acceptance criteria:** (a) a running collect shows live per-stage progress; (b) failed sources listed with reason; (c) toggling a source persists and affects next run.

### 3.4 Run History
- **Purpose:** every run's operational record.
- **Roles:** viewer read; editor retry; qalead re-send.
- **Approved improvements:** funnel, true status, filters, detail drawer, retry/compare.
- **Columns (exact):** Run time (ET) · Trigger (Auto/Manual) · Window · Ingested · After-dedup · In-briefing · Rejected · Dupes · Duration · Coverage (subset/pending) · QA Status · Delivery · Status · Actions.
- **Filters:** date range, Status (Completed/Failed/Running), Trigger, min in-briefing.
- **Sorting:** any numeric/date column; default Run time desc.
- **Search:** by run_id / date.
- **Detail drawer (row click):** funnel bar (ingested→briefing), `source_outcomes` table, coverage report, delivery record, download bundle, error+retry.
- **Metrics (header):** runs today, success rate, avg in-briefing, avg duration, last failure.
- **Actions:** per row Preview, Export ▾, Re-send…, Retry (failed), Compare(2); bulk export CSV.
- **Validation/Errors/States:** empty = "No runs yet"; failed run shows error + Retry.
- **Accessibility:** semantic table, `<th scope>`, drawer focus-trapped + `Esc`.
- **Performance:** server-side pagination; ≤1s per page.
- **Audit:** retry, re-send, export.
- **Acceptance criteria:** (a) status reflects reality (no approval states); (b) funnel numbers reconcile to coverage report; (c) failed run is retryable and logs an audit event.

### 3.5 12-Month Archive
- **Purpose:** search/retrieve historical coverage.
- **Approved improvements:** facets, relevance/date sort, result export, saved searches, clips view (endpoint exists), `/archive/stats` wired.
- **Filters (facets w/ counts):** topic, source type, outlet, paywalled, date range, relevance≥, has-clip.
- **Sorting:** relevance, date.
- **Search:** keyword (title/summary/outlet), entity (commissioner/docket).
- **Tables:** outlet·date·topic·relevance·paywall·links; column sort; pagination.
- **Actions:** Save search, Create alert, Export results (CSV/Excel).
- **States/Errors/Accessibility/Perf:** per §0.5/§1; server-side paging.
- **Audit:** export of results.
- **Acceptance criteria:** (a) facet counts match filtered results; (b) result export contains exactly the filtered set + metadata (§7.6); (c) saved search reproduces results.

### 3.6 Analytics
- **Purpose:** trends + SLA evidence for PM/COR.
- **Charts:** SLA delivery calendar (business-day heatmap: delivered/missed/weekend), volume-vs-target line, dedupe-ratio trend, topic-distribution stacked area, top-10 outlets bar, cluster-ratio (articles→stories) line.
- **Tables:** top outlets, missing-category warnings by day, largest clusters.
- **KPIs:** on-time delivery %, avg stories/day vs target, dedupe %, leadership stories/day, rejected/collected ratio, avg relevance.
- **Trend calculations (exact):** on-time = business-days with `delivered_at` before 08:00 ET ÷ business-days in range; dedupe % = dupes_removed ÷ ingested; cluster ratio = in_briefing ÷ cluster_count. Weekends/holidays excluded from SLA denominator.
- **Coverage/duplicate/performance reporting:** from `bulletin_run_log`.
- **Filters:** date range, topic, source type (cross-filter all).
- **States/Accessibility:** charts have data-table alternatives; calendar cells labeled.
- **Acceptance criteria:** (a) SLA % matches manual count of delivered-before-8ET business days; (b) all charts cross-filter from one date range; (c) export dashboard + CSV.

### 3.7 QA Review *(new)* — see §5.
### 3.8 Delivery *(new)* — see §6.
### 3.9 Export *(unified)* — see §7.
### 3.10 Agency Management
- **Approved improvements:** editable config — recipients, delivery schedule/time, output caps (stories target), feeds on/off, relevance thresholds, from-address; per-agency health (last run, scheduler, connectors, **SendGrid sender verified?**); config-change audit.
- **Validation:** valid emails; from-address must be flagged if not SendGrid-verified; schedule in ET.
- **Acceptance criteria:** (a) saving config persists + audits; (b) unverified sender shows warning before it can be used for delivery; (c) health reflects live scheduler/connector state.
### 3.11 Audit *(new)* — see §9.

---

## 4. COLLECTION lifecycle spec

- **Lifecycle:** `manual/scheduled trigger → run_log(running) → ingest (concurrent per source) → dedup → classify → cluster → build briefing → run_log(completed|failed)`. Persist `started_at/finished_at/duration`.
- **Per-source outcome:** for every registry source, record attempted/succeeded/items/http_status/error/response_ms/retries into `bulletin_source_outcome` (capture the existing `drop_reason` instead of debug-logging it).
- **Retry rules:** per-source: up to **2 retries** on timeout/5xx/429 with exponential backoff (1s/2s), no retry on 4xx; cycle-level: existing scheduler `_run_cycle_with_retry` (max 3) unchanged. Record retry counts.
- **Timeout handling:** per-source hard timeout (e.g., 20s) → mark failed, continue run (never block the cycle). Total-run soft budget surfaced.
- **Failure handling:** a failed source degrades Coverage, never the run. If a **P1 source** fails → run completes but raises a Coverage alert.
- **Progress reporting:** `GET /collect/{a}/status` streams stage/counts; UI polls 3s.
- **Coverage reporting:** reuse `_build_coverage_report`; extend with per-source outcomes + registry join (§8).
- **Source health indicators:** ok/slow(>threshold)/failed/disabled; last-fetch age.
- **Collection logs:** structured per-run log retrievable via run detail; audit events for start/finish/failure.
- **Acceptance criteria:** run always terminates in completed/failed with a persisted funnel; every registry source has an outcome row; P1 failure raises an alert.

---

## 5. QA workflow spec

- **Workflow:** run completes → QA queue auto-populates issues → editor resolves → set QA status → (delivery may reference QA status). Live-feed publish is preserved (QA does not block viewing; it gates *delivery* if agency requires).
- **Auto-checks (computed):** duplicate clusters; low relevance (<0.4) / low quality; **missing summary / URL / publication date**; **stale publication date** (reuse existing stale-date logic); missing-category warnings; paywall audit; leadership-coverage present.
- **Approval workflow:** `qalead` sets `qa_status ∈ {passed, passed_with_notes, blocked}`; stamped on briefing (`qa_by/qa_at`) + audit.
- **Duplicate handling:** show cluster; keep primary, collapse similar; editor may re-pick primary.
- **Manual overrides:** exclude/restore article, edit nothing destructive (exclude only), add editorial note (stored `editorial_notes_json`), pin leadership, mark priority, request AI re-summary (records audit; does not auto-overwrite without editor confirm).
- **Subscription labels:** display `is_paywalled` as "Subscription Required" badge; count in QA summary.
- **Priority/leadership handling:** pinned leadership + priority items sort first and are flagged in export.
- **Acceptance criteria:** (a) every auto-check lists offending items with a fix action; (b) QA status stamps briefing + audit; (c) excluded items are absent from exports/delivery; (d) `qa_status='blocked'` prevents delivery when the agency requires QA.

---

## 6. DELIVERY workflow spec

- **Artifacts:** Word (`/docx`), Excel-QA (`/excel`), HTML email, PDF (`/pdf`) — all present; ensure each carries the metadata block (§7.6).
- **Email generation:** summary + "VIEW FULL BRIEFING" button (existing `send_briefing_email`), from the agency from-address.
- **Preview:** render the **exact** email + artifact list before send.
- **Validation (pre-send, all must pass or warn):** artifacts generated; email renders; recipients valid & non-empty; **SendGrid sender verified** (surface 403 condition honestly); QA status acceptable (if required).
- **Delivery confirmation:** on send, persist `bulletin_delivery_log` (recipients, subject, sendgrid_message_id, result, per-recipient); show success/failure toast with the real result (including a 403 sender error verbatim).
- **Delivery log & history:** table of all sends per agency; filter by date/result; re-send with reason.
- **Rollback behavior:** email cannot be "unsent"; define rollback as (a) mark delivery `superseded`, (b) generate a corrected briefing, (c) send a labeled correction referencing the superseded delivery id — all audited. Artifact regeneration is idempotent per briefing_id.
- **Acceptance criteria:** (a) preview equals what is sent; (b) unverified sender blocks send with a clear message (no silent 403); (c) every send writes a delivery-log row + audit; (d) correction flow links to the superseded delivery.

---

## 7. EXPORT spec (unified)

- **Surface:** single Export ▾ reachable from Daily Briefing, Run History, Archive.
- **Content × format:** {current window | a run | archive result set} × {Word, PDF, Excel-QA, HTML, CSV}. All honor active filters/window (fix the range bug).
- **7.6 Mandatory metadata block on EVERY export** (header/cover): Generation timestamp (ET), Run ID / Briefing ID, Article count, Duplicate count + Duplicate %, Coverage confidence *(or "pending instrumentation" — never a fabricated %)*, Version (module version), QA status, Generated by (user). Government option: handling/marking line + contractor attribution.
- **Acceptance criteria:** (a) metadata block present and accurate on all 5 formats; (b) Coverage field shows a real value or the explicit "pending" label — never a fabricated %; (c) exported set == on-screen filtered set.

---

## 8. COVERAGE ASSURANCE spec (HONEST — binding)

Three explicit buckets; **never compute a metric that cannot be proven.**

**8.1 Available NOW (surface from `_build_coverage_report`/run data):** sources scanned & source_count; stories collected; duplicates removed + **Duplicate %**; in-briefing; rejected; subscription_stories; missing-category warnings; **avg relevance** (`relevance_score`); **avg article quality** (`quality_score`); cluster count; **primary-source backstop statement** ("All FCC official releases for <date> captured" — true because FCC.gov/Fed Register/ECFS are always collected).

**8.2 Future (compute ONLY after instrumentation):** **Coverage %** = successful ÷ expected sources; **Coverage confidence** = importance-weighted coverage; sources succeeded/failed with failed-source list; avg response time; **avg AI confidence** (requires a real per-summary confidence signal, not `relevance_score` relabeled).

**8.3 Required instrumentation (dependencies for 8.2):** (i) `bulletin_source_registry` = the *expected* set (denominator); (ii) `bulletin_source_outcome` = per-source success/fail/timing (numerator); (iii) a genuine AI-confidence signal if "avg AI confidence" is to be shown.

**Guardrail (must enforce in UI + exports):** until 8.3 exists, the Coverage % field renders **"Coverage %: pending instrumentation"** — not a number, not an estimate, not 100%. Showing a fabricated Coverage % to the Government is prohibited.
- **Acceptance criteria:** (a) no Coverage % appears anywhere until registry + outcomes exist; (b) once instrumented, Coverage % is reproducible from `source_outcome` vs `source_registry`; (c) primary-source backstop statement only shows when the FCC.gov sources actually returned this run.

---

## 9. AUDIT spec

Append-only `bulletin_audit_log`. **Events (each with actor, ts, entity, details, result):** collection start/finish; per-source failure; retry; export; download; email send; delivery result; QA action (exclude/restore/note/pin/priority); QA status change; AI re-summary; manual edit; agency config change; registry enable/disable; purge; every warning/failure.
- **UI:** Audit screen (filter by date/actor/event/entity) + inline history on run/briefing/delivery.
- **Integrity:** append-only (no update/delete via app); reads role-gated (`viewer`+).
- **Acceptance criteria:** (a) every state-changing action produces exactly one audit row; (b) audit is immutable through the app; (c) filters return correct subsets.

---

## 10. ACCESSIBILITY spec (Section 508 / WCAG 2.1 AA — required)

- Status = **icon + text** everywhere (no color-only).
- Real icon set with `aria-label`; remove emoji-as-icon.
- Semantic tables (`<table>/<thead>/<th scope>`); card lists use roving `tabindex` + arrow keys.
- Visible focus rings; modals focus-trapped, `Esc` closes, focus returns to trigger.
- All inputs labeled; date pickers labeled; required fields announced.
- Contrast ≥ 4.5:1 (audit Fluent tints); charts have data-table alternatives.
- `prefers-reduced-motion` respected; async collect/send announced via ARIA live region.
- Keyboard map (global): `/` search · `g o/d/c/h/a/n/q/y` navigate (ops/daily/collect/history/analytics/aNalytics.../qa/agencY) · `c` collect · `e` export · `s` send · `j/k` next/prev · `x` exclude · `?` cheat-sheet.
- **Acceptance criteria:** automated axe scan 0 critical/serious; full keyboard operation of every workflow; screen-reader pass on Daily Briefing, Run History, Delivery.

---

## 11. SECURITY spec

- **Authentication:** all `/api/v1/bulletin/*` require a valid JWT except explicitly-public read endpoints (`/health`, `/status`, `/latest/*/preview`); apply shared auth (import `require_role`; do not modify auth module).
- **Authorization:** per §0.4 role matrix; state-changing/costly endpoints (`/collect`,`/send`,`/run`,`/refresh`,`/admin/purge-articles`, registry/QA/delivery POSTs) require ≥`editor`/`qalead`/`admin` as specified.
- **Input validation:** validate agency_id against registered agencies; date params ISO; page_size bounded (≤500); reject unknown formats; sanitize any user-entered editorial notes (stored, rendered as text).
- **Rate limiting:** `/collect` and `/send` per-user/token limited (e.g., collect ≤ N/hour) + reuse existing cycle-lock; return 429 with retry-after.
- **Secrets:** SENDGRID_API_KEY, NEWSAPI/TAVILY keys via env only; never in responses/logs/exports; from-address configurable but sender must be SendGrid-verified.
- **Logging/audit:** no PII/secret leakage in logs; audit per §9; delivery/audit views role-gated (recipient PII).
- **No procurement data in UI/API** (keep the removed-contract-number discipline).
- **Acceptance criteria:** (a) unauthenticated call to `/collect`/`/send`/`/admin/*` → 401/403; (b) rate limit enforced (429); (c) no secret/PII in any response, log, or export; (d) audit captures every privileged action.

---

## 12. PERFORMANCE spec

- Server-side pagination + virtualization for Archive/Run History/registry (scales to 2,000+ sources).
- Async collect + progress (no synchronous long request blocking the UI); poll `/collect/status`.
- Cache generated Word/PDF/Excel per briefing_id (regenerate only on change).
- Relevance-filter + dedup + cluster **before** LLM steps (preserve existing caps).
- **Targets:** console FMP ≤1.5s (cached); window switch ≤1s; archive page ≤1s; collect progress updates ≤3s cadence.
- **Acceptance criteria:** load tests meet targets at 500 in-window / 5,000 archive / 2,000 registry rows.

---

## 13. IMPLEMENTATION PLAN (features → tasks)

Task fields: **Deps · Effort (S≤1d/M2–4d/L1–2w) · Risk · Testing · Acceptance · Rollback.** Feature IDs map to the approved roadmap.

**C1 — Honest status** · Deps: none · S · Risk low · Test: unit(status-derivation), UI(no `pending_approval`) · AC §0.3 · Rollback: revert display mapping.
**C2 — API auth + rate-limit** · Deps: shared auth (import) · M · Risk med (lockout) · Test: integration(401/403/429), regression(existing public reads still work) · AC §11 · Rollback: feature-flag auth per endpoint.
**C3 — Delivery screen + log + validation** · Deps: C6(audit), delivery_log table · M · Risk med(SendGrid) · Test: integration(send/validate/403), UI(preview==sent) · AC §6 · Rollback: hide screen; sends still via existing `/send`.
**C4 — 508 pass** · Deps: none · M · Risk low · Test: axe, keyboard, SR · AC §10 · Rollback: n/a (additive).
**C5 — Coverage Assurance (available subset)** · Deps: coverage report(exists) · M · Risk low · Test: unit(dedupe%/avg calc), UI(no Coverage %) · AC §8.1 · Rollback: hide panel.
**C6 — Audit trail (table + view)** · Deps: `bulletin_audit_log`(additive) · M · Risk low · Test: integration(one-row-per-action), immutability · AC §9 · Rollback: stop-writes flag; table inert.
**H1 — Morning Ops Console** · Deps: C1/C3/C5 · M · Risk low · Test: UI(states), perf · AC §3.1 · Rollback: route to Daily Briefing.
**H2 — Per-source outcome instrumentation** · Deps: registry(H3a) · M+BE · Risk med · Test: integration(outcome rows per run) · AC §4 · Rollback: outcomes optional; run unaffected.
**H3 — Source Registry + honest Coverage %** · Deps: H2 · L+BE · Risk med(denominator correctness) · Test: unit(coverage=succeeded/expected), integration · AC §8.2 · Rollback: keep "pending" label.
**H4 — Collection Pipeline screen** · Deps: H2, `/collect/status` · M · Risk low · Test: UI(live progress), a11y · AC §3.3 · Rollback: hide screen.
**H5 — Run History funnel + status + retry** · Deps: run_log timing · M+BE · Risk low · Test: integration(funnel reconciles), UI(retry) · AC §3.4 · Rollback: basic list fallback.
**H6 — Export metadata + unified export + range fix** · Deps: C5/C6 · M · Risk low · Test: unit(metadata block all formats), UI(range honored) · AC §7 · Rollback: per-format flag.
**M1 QA Review** (Deps C6; M) · **M2 Analytics upgrade** (Deps H5; M–L) · **M3 Archive facets/export/saved** (M) · **M4 Agency config editing** (Deps C2; M+BE) · **M5 AI-confidence + inline clustering** (M+BE) · **M6 Keyboard shortcuts** (Deps C4; S–M) — each with the same field structure; AC per §3/§5.
**L1–L5** (saved views, clips, LLM-visibility, componentization, cover-markings) — additive, low risk.

**Global rollback discipline:** every feature ships behind a module-level feature flag; additive tables/columns are nullable and inert until their feature is enabled; no destructive migrations.

---

## 14. TEST PLAN

Per feature, define all six layers (examples binding for Critical/High):

| Layer | What (examples) |
|---|---|
| **Unit** | status-derivation (C1); dedupe%/avg-relevance/coverage math (C5/H3); SLA on-time calc (M2); export-metadata builder (H6) |
| **Integration** | auth 401/403/429 (C2); collect→run_log→source_outcome rows (H2); send→delivery_log + audit (C3); every action→exactly-one audit row (C6) |
| **UI (E2E)** | never renders `pending_approval` (C1); export honors active window (H6); delivery preview == sent (C3); pipeline shows live progress (H4) |
| **Accessibility** | axe 0 critical; full keyboard workflow; screen-reader pass; contrast audit (C4, all) |
| **Performance** | console ≤1.5s cached; archive ≤1s @5k; registry @2k; collect status ≤3s cadence (§12) |
| **Regression** | existing endpoints/exports unchanged; TEFCA/healthcare/other modules untouched (diff scope check: only `bulletin_intelligence/**` + `bulletin/**` + additive `bulletin_*` tables); scheduler/watchdog still fires; live-feed publish preserved |

**Coverage-assurance test (critical, honesty):** assert the UI/exports show **no numeric Coverage %** until `bulletin_source_registry` + `bulletin_source_outcome` are populated; once populated, Coverage % must equal `succeeded/expected` computed independently in the test.

**Exit criteria for the build:** all Critical + High acceptance criteria pass; axe clean; regression scope diff shows only in-scope files; no fabricated metric present anywhere.

---

*No code, React, Python, SQL, or API changes were produced. This is the authoritative specification. Await approval before implementation.*
