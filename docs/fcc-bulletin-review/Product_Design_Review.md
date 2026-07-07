# FCC News Bulletin — Product Design Review

**Reviewer lens:** Senior Product Designer (Azure Portal / Bloomberg Terminal / Palantir / Sentinel / Power BI inspiration).
**Scope:** FCC News Bulletin module ONLY. Documentation only — no code, no UI, no backend changes.
**Grounded in:** `frontend/src/app/bulletin/page.js` (931 lines, single page, 5 tabs) + `backend/app/bulletin_intelligence/routes.py` + `bulletin_download_routes.py`.

---

## 0. What actually exists today (baseline)

| Listed screen | Reality |
|---|---|
| Daily Briefing | ✅ Tab — day presets (1/2/3/7 + counts), custom From–To range, Refresh Data, Collect News Now, Topic Index, articles grouped by topic, downloads (Word / Excel-QA / HTML) |
| Run History | ✅ Tab — run list; Preview / Open HTML / Download PDF; preview modal |
| 12-Month Archive | ✅ Tab — keyword search, topic + source-type filters, date range, per-article View |
| Analytics | ✅ Tab — 2 views: Coverage by Topic, Monthly Volume |
| Agency Management | ✅ Tab ("Agencies") — registered list, agency selector, Module Status; 4 "coming soon" agencies |
| **Collection Pipeline** | ❌ **No screen** — only "Collect News Now"/"Refresh Data" buttons + a spinner |
| **QA** | ❌ **No screen** — QA exists only as an **Excel download** |
| **Export** | ❌ **No screen** — downloads scattered across Daily Briefing + History |
| **Delivery** | ❌ **No screen** — email send/approve exist only as backend endpoints (`/send`, `/approve`, `/queue`) |

**Backend data the UI does NOT surface (missed value):** `GET /coverage/{agency}` (sources scanned, collected/rejected, duplicates removed, missing-category warnings), `GET /admin/last-window` (in/out-of-window counts, publish-date range), `GET /queue` (pending items), `POST /send` recipients + result, `POST /llm-visibility`, `archive/{agency}/stats`, `archive/{agency}/clips`.

**Structural verdict:** This is a **1-analyst tool**, not an enterprise intelligence console. It works for "look at today's articles and download a doc," but has **no operational visibility, no delivery control, no QA surface, and no coverage-assurance** — the exact things a COR, an Ops Manager, and an AI Engineer need. The single-page/inline-style architecture also caps accessibility and keyboard support.

---

## 1. Cross-cutting findings (apply to all screens)

- **CC-1 Stale approval model.** UI still shows `pending_approval` status + amber styling, but the pipeline moved to a live-feed model (everything is `delivered` on generation). The status chip now misrepresents reality. *(Critical — trust/accuracy.)*
- **CC-2 No global command bar / status header.** No persistent "last collection: 3h ago · 122 articles · scheduler: ✔ · connectors: ✔" strip. Azure/Sentinel always show system state up top.
- **CC-3 No global search.** Search exists only inside Archive. An analyst can't search "Carr" across today's briefing.
- **CC-4 Feedback is a spinner.** "Collect News Now" (~1–2 min) gives no progress, no per-source status, no ETA, no success toast with counts.
- **CC-5 Inline styles, emoji icons, color-only status.** Blocks theming, fails several WCAG criteria, and reads as prototype-grade to a government client.
- **CC-6 No keyboard model, no accessibility affordances** (see §12–13).
- **CC-7 Export is inconsistent** — Word/Excel disabled when a custom range is active; formats differ by tab; no single export surface.

---

## 2. Screen: DAILY BRIEFING

**Persona expectations**
- *FCC analyst:* "What broke today about the FCC, grouped by topic, with the leadership items on top, and a way to jump to the source." (Partially met.)
- *Newsroom editor:* dedupe visibility, similar-story grouping, paywall flags, ability to pull/hide a story before export. (Missing in-view.)
- *COR:* provenance — where did each item come from, is this the delivered set, what window. (Missing.)
- *Ops Manager:* is collection fresh, did the run succeed, article counts vs. yesterday. (Missing.)
- *AI Engineer:* relevance score, classification topic, dedupe cluster id, why an item was included. (Partially — score exists in data, not shown.)

**Current problems**
- Client-side `isValidArticle` filter (relevance<0.3, demo strings) **diverges** from backend filtering — the view can disagree with Run History and the delivered doc.
- Downloads **disable Word/Excel when a custom range is active** — confusing dead buttons.
- No per-article relevance/score, no cluster/"similar stories" grouping in-view (it exists in the Excel only).
- "Collect News Now" vs "Refresh Data" distinction is unclear to non-power users.
- No leadership section pinned (client wants FCC leadership on top).

**Missing features:** story clustering inline (primary + N similar), leadership/"General" section pin, per-article relevance & source-type chips, "new since last view" markers, inline exclude/flag, freshness timestamp, saved views.

**Recommended layout:** 3-zone Fluent layout — (1) sticky **command bar** (window selector, Collect, Export, freshness "updated 2h ago"); (2) left **Topic Index rail** (sticky, counts, leadership pinned); (3) main **story feed** as clustered cards (primary headline + collapsible similar coverage, source chips, relevance meter, paywall badge).

**Recommended workflow:** open → auto-load today's window → scan Topic Index → expand a cluster → (optional) exclude a junk item → Export/Send. Collect runs async with progress; feed updates on completion.

**Recommended metrics (tiles):** Articles in window · Distinct stories (clusters) · Leadership stories · Sources represented · % paywalled · Avg relevance.

**Recommended buttons:** Collect News Now (async), Refresh, Export ▾ (Word/PDF/Excel/HTML/CSV), Send to FCC…, Copy briefing link, Exclude/Restore item.

**Recommended filters:** window (Today/2d/3d/7d/custom), topic, source type, relevance ≥ slider, paywalled on/off, "leadership only," search-in-briefing.

**Recommended tables/lists:** clustered story cards (not a flat list); each card = title, outlet, time, topic chip, relevance meter, "+3 similar" expander, links.

**Recommended status indicators:** freshness dot (green <6h / amber <24h / red older), per-source-type chips, paywall badge, "clustered N→M" indicator (your 109→26).

**Recommended progress bars:** collection progress (per-source ticks), classification progress, "briefing built" step indicator.

**Recommended alerts:** "collection stale (>24h)," "a P1 source returned 0 today," "N low-confidence items need review."

**Recommended search:** in-briefing search box (title/outlet/entity), highlight matches.

**Recommended export:** unified Export ▾ that respects the active window for ALL formats (fix the disabled-on-range bug); add CSV + "copy shareable preview link" (the `/latest/fcc/preview` URL).

---

## 3. Screen: RUN HISTORY

**Persona expectations** — *Ops:* every run's status/time/counts/duration, retries, failures. *COR:* which run was delivered, to whom, when. *AI Eng:* per-run ingested→dedup→classified→in-briefing funnel + cost.

**Current problems:** thin list; only Preview/Open-HTML/Download-PDF. No status column that reflects reality (still shows approval states), no delivery info, no counts/duration, no diff vs prior run, no filters, no pagination controls surfaced.

**Missing features:** run funnel (ingested / after-dedup / in-briefing / rejected), duration + trigger (scheduled vs manual), delivery record (sent? to whom? opened?), error detail + retry, compare two runs, re-send, re-generate.

**Recommended layout:** dense table (Splunk/ServiceNow style) + right detail drawer on row-click (funnel, window stats, delivery, coverage report, download bundle).

**Recommended workflow:** filter by date/status → open run → inspect funnel + coverage → download/re-send → (if failed) view error → retry.

**Recommended metrics:** runs today, success rate, avg articles/run, avg duration, last failure.

**Recommended buttons:** per row — Preview, Export ▾, Re-send…, Retry, Compare; bulk — export selected.

**Recommended filters:** date range, status (delivered/error), trigger (auto/manual), min articles.

**Recommended tables:** columns = Run time · Trigger · Window · Ingested · In-briefing · Rejected · Duration · Delivery · Status · Actions.

**Recommended status indicators:** true status (Delivered/Failed/Running), delivery pill (Sent/Not sent/Bounced), trigger badge (Auto/Manual).

**Recommended progress bars:** funnel bar (ingested→briefing) per run; running-run live progress.

**Recommended alerts:** failed run banner with "retry"; "today's scheduled run missing" (ties to the scheduler watchdog you already have).

**Recommended search/export:** search by briefing id/date; export run history as CSV.

---

## 4. Screen: 12-MONTH ARCHIVE

**Persona expectations** — *analyst/editor:* fast full-text search, facets, saved searches, export result set. *AI Eng:* relevance & topic facets, source-type, dedupe.

**Current problems:** basic keyword + topic + source-type + date; no relevance sort, no result count/pagination affordance, no faceted counts, no saved searches, no bulk export of results, `/archive/stats` and `/clips` endpoints unused.

**Missing features:** faceted search (topic/source/outlet/paywall counts), relevance & date sort, result export (CSV/Excel), saved/named searches + alerting ("email me new 'satellite' items"), broadcast **clips** view (endpoint exists), outlet facet, entity search (commissioner/docket).

**Recommended layout:** Bloomberg-style — left facet rail (counts), center results (sortable, paginated), right preview pane.

**Recommended workflow:** query → refine via facets → sort → preview → export set / save search.

**Recommended metrics:** total archived, matches, coverage by month/topic (wire in `/archive/stats`).

**Recommended buttons:** Search, Save search, Export results ▾, Create alert.

**Recommended filters (facets):** topic, source type, outlet, paywalled, date, relevance≥, has-clip.

**Recommended tables:** results with outlet, date, topic, relevance, paywall, links; column sort.

**Recommended status indicators:** paywall badge, source-type chip, relevance meter, "clip available."

**Recommended alerts:** saved-search hit notifications.

**Recommended search/export:** the core of this screen — add relevance ranking, faceted counts, CSV/Excel export of the result set.

---

## 5. Screen: ANALYTICS

**Persona expectations** — *COR/Ops:* trend lines, SLA (did we deliver every business day?), volume vs. target (the "60 stories" goal), source mix, leadership coverage rate. *AI Eng:* precision proxies, dedupe ratio, rejection reasons.

**Current problems:** only 2 static views (Coverage by Topic, Monthly Volume). No trends over time, no delivery/SLA metrics, no source/outlet mix, no dedupe/coverage analytics though the data exists (`/coverage`, `/last-window`).

**Missing features:** delivery SLA calendar (business-day heatmap — delivered/missed), volume-vs-target line, story-cluster ratio trend (articles→stories), topic trend over time, top outlets, leadership-coverage rate, rejection-reason breakdown, connector/feed health trend.

**Recommended layout:** Power BI dashboard — KPI row + 4–6 cross-filtering charts + a delivery calendar.

**Recommended metrics:** on-time delivery %, avg stories/day vs target, dedupe ratio, topic distribution trend, top 10 outlets, leadership stories/day, rejected/collected ratio.

**Recommended buttons:** date range, export dashboard (PDF/PNG), drill-through to Archive.

**Recommended filters:** date range, topic, source type (cross-filter all charts).

**Recommended tables:** top outlets, top stories (by cluster size), missing-category warnings.

**Recommended status indicators:** SLA calendar cells (green delivered / red missed / grey weekend).

**Recommended alerts:** "delivery missed on <date>," "volume below target 3 days running."

**Recommended export:** dashboard export + underlying CSV.

---

## 6. Screen: AGENCY MANAGEMENT

**Persona expectations** — *Ops/admin:* configure agency (feeds, recipients, schedule, caps), enable/disable, see health. *COR:* who receives the bulletin, delivery schedule.

**Current problems:** largely read-only list + "coming soon" agencies + a Module Status block. No edit of distribution list, schedule, caps, feeds; recipients not visible; no per-agency health.

**Missing features:** edit distribution list (recipients), delivery schedule/time, output caps (the "150/60 stories" knobs), feed on/off, relevance thresholds, from-address; per-agency health (last run, scheduler, connectors, SendGrid sender status), audit of config changes.

**Recommended layout:** master–detail — agency list left, config form right (tabs: Recipients, Schedule, Sources, Thresholds, Health).

**Recommended workflow:** select agency → edit config → save → see health/next-run.

**Recommended metrics:** last delivery, next scheduled run, recipients count, active feeds, connector health.

**Recommended buttons:** Edit, Save, Add recipient, Test delivery, Enable/Disable feed.

**Recommended status indicators:** scheduler ✔/✖, SendGrid sender verified/unverified (ties to your `TEFCA_ALERT_FROM`-style issue), feed health.

**Recommended alerts:** "SendGrid sender unverified — sends will 403," "scheduler off."

---

## 7. Screen: COLLECTION PIPELINE *(does not exist — recommend building)*

**Why it matters:** *Ops/AI Eng* need to see the machine. Today it's a black box behind one button.

**Recommended layout:** Sentinel-style pipeline view — source registry table + live run panel.

**Recommended features:** per-source last-fetch time, item count, status (ok/slow/failed), rate-limit/robots notes; live run progress (ingest→dedup→classify→build) with per-stage counts; feed enable/disable; manual "collect now" with streamed log; connector health (NewsAPI/Tavily/GDELT/RSS/FCC.gov).

**Recommended metrics:** sources scanned, collected, rejected (+reasons), duplicates removed, in-briefing — **all already computed in `/coverage`**, just unsurfaced.

**Recommended status/progress/alerts:** per-source health dots; run progress bar with stage ticks; alert on "P1 source returned 0" / "feed failing 3 runs."

**Recommended tables:** Source · Type · Last fetch · Items · Status · Rate-limit · Enabled.

---

## 8. Screen: QA *(does not exist as a screen — only an Excel download)*

**Why it matters:** *Editor/COR* need to see and act on quality before delivery, not open a spreadsheet.

**Recommended features:** in-app QA review of the current briefing — clustered duplicate check, low-relevance flags, missing-category warnings (`/coverage`), paywall audit, stale-date audit, leadership-coverage check; per-item approve/exclude; "coverage assurance" panel ("did any source cover X").

**Recommended layout:** split view — issues list (left) + item detail (right) with fix actions.

**Recommended metrics:** dedupe ratio, low-confidence count, missing categories, stale-dated count, % paywalled.

**Recommended status/alerts:** red/amber issue chips; "N issues block a clean delivery" summary; one-click exclude.

**Recommended export:** keep the Excel QA sheet as an export *option*, but make the QA review native.

---

## 9. Screen: EXPORT *(does not exist as a screen — scattered buttons)*

**Recommendation:** a single **Export** surface (modal or page) reachable from any screen: pick **content** (current window / a run / archive result set) × **format** (Word / PDF / Excel-QA / HTML email / CSV / JSON) × **scope** (respects active filters — fix the range-disables-Word/Excel bug). Show a preview + file size; remember last choice. Add "copy shareable preview link" (`/latest/fcc/preview`).

**Government UX:** include a cover page with classification/handling marking option, generation timestamp, source count, and "AGT — for [agency]" attribution.

---

## 10. Screen: DELIVERY *(does not exist as a screen — backend-only)*

**Why it matters:** delivery is the contract deliverable and it's currently invisible/uncontrollable in the UI.

**Recommended features:** compose & send the briefing email (`/send`) with recipient list, subject, the summary-and-"VIEW FULL BRIEFING"-button format; **preview the exact email** before send; delivery log (who/when/result, incl. SendGrid 403 sender-verification failures surfaced honestly); schedule/queue; re-send; test-send to self; per-recipient status.

**Recommended layout:** compose pane (recipients, subject, preview) + delivery history table below.

**Recommended metrics:** last sent, recipients, success/bounce, opens (if tracked).

**Recommended status/alerts:** "sender not verified in SendGrid," "scheduled send at 00:01 ET," delivery success/failure toast.

---

## 11. Persona summary matrix (what each still needs)

| Persona | Biggest current gap |
|---|---|
| FCC analyst | Inline clustering + leadership pin + in-briefing search |
| Newsroom editor | QA/dedupe review + exclude before export |
| Government COR | Delivery visibility + SLA/coverage assurance + provenance |
| Operations Manager | Collection Pipeline health + Run History funnel + alerts |
| AI Engineer | Relevance/score/cluster surfaced + rejection-reason analytics + pipeline metrics |

---

## 12. Keyboard shortcuts (global — currently none)

`/` focus search · `g d/h/a/n/y` go to Daily/History/Archive/aNalytics/agencY · `c` collect now · `e` export · `s` send · `r` refresh · `j/k` next/prev story · `x` exclude item · `?` shortcuts cheat-sheet. (Bloomberg/Superhuman-grade nav.)

## 13. Accessibility (WCAG 2.1 AA — currently at risk)

- Replace **color-only status** with icon+text (status pills). *(Critical.)*
- Emoji icons → real icon set with `aria-label`.
- Semantic `<table>`/`<th scope>` for run/archive tables; roving `tabindex` for card lists.
- Focus states, focus trapping in the preview modal, `Esc` to close.
- Contrast audit of Fluent tints; date inputs labeled; charts get text/table alternatives.
- Respect `prefers-reduced-motion`; live-region announcements for async collect/send.

## 14. Government UX improvements

- **Provenance everywhere:** every story shows source + collected-at + "delivered set" marker.
- **Handling/marking** option on exports (CUI-style cover), generation timestamp, source count, contractor attribution.
- **No contract numbers in UI** (you already removed one from `/status` — keep that discipline).
- **Delivery record of authority:** immutable log of what was sent, to whom, when (COR audit).
- **Coverage assurance statement:** "All official FCC releases for <date> captured (primary-source backstop)."
- **Accessibility conformance** is itself a government requirement (Section 508) — prioritize §13.

---

## 15. Prioritized roadmap (Critical → Low) with effort

Effort: **S** ≈ ≤1 day · **M** ≈ 2–4 days · **L** ≈ 1–2 weeks (UI only; backend data mostly already exists).

### CRITICAL
| # | Improvement | Screens | Effort |
|---|---|---|---|
| C1 | Fix **stale `pending_approval`** status → reflect live-feed reality | Briefing, History | S |
| C2 | **Delivery screen** — preview email, send, recipient list, delivery log (surface `/send` + SendGrid sender status) | Delivery | M |
| C3 | **Accessibility pass** (status pills w/ text, semantic tables, focus/Esc, labels) — Section 508 | All | M |
| C4 | Fix **Export range bug** + unified Export ▾ (all formats respect active window) | Briefing, Export | S |

### HIGH
| # | Improvement | Screens | Effort |
|---|---|---|---|
| H1 | **Collection Pipeline screen** — surface `/coverage` (scanned/collected/rejected/dupes/missing-category) + per-source health + run progress | Pipeline | M |
| H2 | **Run History funnel + status + filters** (ingested→briefing, duration, trigger, delivery, retry) | History | M |
| H3 | **Global command/status bar** (freshness, scheduler, connectors, article count) + collect progress | All | M |
| H4 | **Inline story clustering + leadership pin + relevance chips** on Daily Briefing | Briefing | M |
| H5 | **Global + in-briefing search** | All | S–M |

### MEDIUM
| # | Improvement | Screens | Effort |
|---|---|---|---|
| M1 | **Analytics upgrade** — SLA delivery calendar, volume-vs-target, dedupe ratio, top outlets, trends | Analytics | M–L |
| M2 | **QA review screen** (native issues + exclude, not just Excel) | QA | M |
| M3 | **Archive facets + result export + saved searches** | Archive | M |
| M4 | **Agency config editing** (recipients, schedule, caps, feeds, health) | Agency | M |
| M5 | **Keyboard shortcuts** + cheat-sheet | All | S–M |

### LOW
| # | Improvement | Screens | Effort |
|---|---|---|---|
| L1 | Saved views / saved searches + alerting | Briefing, Archive | M |
| L2 | Broadcast **clips** view (endpoint exists) | Archive | S |
| L3 | LLM-visibility panel (endpoint exists) | Analytics | S |
| L4 | Theming / design-token cleanup (remove inline styles) | All | L |
| L5 | Export cover-page / handling markings | Export | S |

**Sequencing recommendation:** C1→C4 first (correctness + a government-critical delivery surface + 508), then H1–H3 (operational visibility — the biggest "enterprise" jump), then the Medium set. Nearly all of this is **UI work over data the backend already produces** — low backend risk.

---

*No code was written. No UI, React, or backend was modified. This is a design review only.*
