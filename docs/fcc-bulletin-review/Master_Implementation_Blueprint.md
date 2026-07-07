# FCC News Bulletin — Master Product Review & Implementation Blueprint

**Roles:** Enterprise Architect · Principal UX · Government Solutions Architect · AI Platform Architect · Product Manager.
**Scope:** FCC News Bulletin module ONLY. **Design/documentation only — no code, no UI, no SQL, no migrations.**
**Mission:** operate the FCC Daily News Briefing to a **before-8:00 AM ET** SLA every business day, at the operational/audit grade of Bloomberg Government / Sentinel / ServiceNow.
**Builds on:** the approved `Product_Design_Review.md` (screen-by-screen detail) — this document adds the operational, coverage-assurance, audit, security, performance, and ROI-annotated roadmap layers.

---

## 1. Executive Product Review

The bulletin **works** as a single-analyst tool: collect → view by topic → download a doc. It is **not yet a government intelligence platform**: it has no operational visibility, no delivery control surface, no audit trail, no coverage-assurance instrumentation, and it can't *prove* completeness to a COR.

**The single most important insight:** ~60% of the "prove nothing was missed" data **already exists** in `_build_coverage_report` and the run pipeline — it's simply **not surfaced**. The remaining ~40% (per-source success/failure, retries, response times, expected-source denominator, audit trail, AI-confidence) is **genuinely uninstrumented** and must be built. Critically, **the ~40% cannot be faked** — a Coverage % shown without real per-source tracking is a false assurance on a federal contract and must not ship.

**Three-line verdict:**
1. **Surface what exists** (cheap, high-impact): coverage report, window stats, cluster ratio, relevance/quality, delivery record.
2. **Instrument what's missing** (the real work): per-source outcomes, expected-source registry → honest Coverage %, audit trail, AI-confidence.
3. **Harden for government** (non-negotiable): authenticate the API, Section 508, delivery record-of-authority, honest metrics.

---

## 2. Screen-by-Screen Review (condensed — full detail in `Product_Design_Review.md`)

| Screen | Exists? | Top strength | Top weakness | #1 fix |
|---|---|---|---|---|
| Daily Briefing | ✅ | topic index, window presets w/ counts, 3 export formats | client-side filter diverges from backend; no clustering/relevance in-view; stale `pending_approval` | inline clustering + freshness + honest status |
| Collection | ❌ (button only) | one-click collect | black box; no progress, no per-source result | Collection Pipeline screen (§6) |
| Run History | ✅ | run list + preview/PDF | no funnel/status/duration/delivery/retry | run funnel + true status + retry |
| 12-Month Archive | ✅ | keyword + topic + source + date | no facets/relevance sort/result export/saved search | facets + result export |
| Analytics | ✅ | topic + monthly volume | 2 static charts; no SLA/trends/dedupe | SLA delivery calendar + trends |
| Agency Management | ✅ | registered list + module status | read-only; recipients/schedule/caps not editable | config editing + per-agency health |
| Collection Pipeline | ❌ | — | none | build (§6) |
| QA Review | ❌ (Excel only) | QA data exists | not in-app; no per-item action | build (§7) |
| Export | ❌ (scattered) | 4 formats work | range-disables-Word/Excel bug; no metadata stamp | unified Export + metadata (§8/Export) |
| Delivery | ❌ (backend only) | send + summary email exist | invisible/uncontrollable; SendGrid failures hidden | build (§8) |

Per-screen dimensions (strengths/weaknesses/missing functionality/gov-usability/accessibility/enterprise-UX/operational-risk/performance/scalability/audit/security/metrics/filters/actions/dashboards/reports/exports/search/keyboard/508) are enumerated in the companion review; this blueprint focuses on the operational + cross-cutting layers below.

---

## 3. Missing Features Matrix — Data Availability (the spine)

Legend: **E** = exists (surface it) · **P** = partial · **M** = missing (instrument it). "Where" cites the real code.

### Collection
| Metric | Status | Where / gap |
|---|---|---|
| Collection start / finish time | **P** | `generated_at` exists; explicit start/finish not persisted → add run timing |
| Collection duration | **M** | not persisted → derive from start/finish |
| Sources scanned / count | **E** | `_build_coverage_report.sources_scanned`, `source_count` |
| Sources succeeded | **P** | inferable (returned articles) but not tracked per-source |
| Sources failed / which API/RSS failed | **M** | feeds dropped with logged `drop_reason` (debug log only) — **not aggregated/persisted** |
| Retry count | **P** | scheduler `_run_cycle_with_retry` (max 3) at cycle level; per-feed retries not counted |
| Avg response time | **M** | not measured |
| **Coverage %** | **M** | **no `expected_sources` denominator exists** → requires Source Registry (§5). DO NOT fabricate. |

### Processing
| Metric | Status | Where / gap |
|---|---|---|
| Articles collected | **E** | `stories_collected` |
| Accepted / rejected | **E** | `in_briefing`, `rejected` |
| Duplicates removed / **Duplicate %** | **E** | `duplicates_removed` (÷ collected = %) |
| Cluster count (109→26) | **P** | computed at render (`_cluster_stories`) — not persisted as a field → surface |
| AI summaries generated | **P** | summaries built; count derivable, not stored as metric |
| **AI confidence** | **P** | `relevance_score` + `quality_score` exist; no distinct summary-confidence |
| Categories generated | **E** | `topic_counts` |
| Stories pending QA | **N/A** | no QA gate now (live-feed) → define in QA design (§7) |

### Editorial QA
| Metric | Status | Where / gap |
|---|---|---|
| Subscription labels | **E** | `is_paywalled` / `subscription_stories` |
| Missing categories | **E** | `missing_category_warnings` |
| Leadership stories | **E** | `_leadership_prefix` / chairman tracking |
| Duplicate warnings | **E** | cluster "similar" members |
| Missing summaries / URLs / pub-dates | **M** | data present per article; **not audited into flags** → compute in QA view |
| Stale publication dates | **P** | engine already parses/flags stale dates (prior fix) → surface |
| Priority / editorial notes / manual overrides | **M** | no concept yet → new |

### Delivery
| Metric | Status | Where / gap |
|---|---|---|
| Word / Excel / HTML / PDF generated | **E** | download endpoints |
| Delivered / timestamp / recipients | **E** | `delivered_at`, `delivery_recipients` |
| Email rendered / validation | **P** | `send_briefing_email` builds summary; no explicit validation step |
| SendGrid status | **P** | send returns 403 reason; **not persisted/surfaced** |
| Delivery history / log | **M** | only current briefing's `delivered_at` — **no persistent delivery log** |

### Coverage Assurance
| Metric | Status |
|---|---|
| Sources scanned / completed | **E / P** |
| Sources failed · Expected sources · Coverage % · Coverage confidence | **M** (see §5) |
| Duplicate % · Avg relevance · Avg article quality · Missing-category alerts | **E** (`duplicates_removed`, `relevance_score`, `quality_score`, `missing_category_warnings`) |
| Avg AI confidence | **P** |

### Audit Trail
| Metric | Status |
|---|---|
| Any bulletin audit log | **M** | only `bulletin_articles` + `bulletin_briefings` tables exist — **no audit table** → §9 |

**Takeaway:** the "surface-it" column (E/P) is a fast, high-value win; the M column is the real engineering (and the honesty-critical part).

---

## 4. Government Operations Review — the 8 AM ET lifecycle

A **Morning Operations Console** (default landing for COR/PM/Ops/QA Lead) must answer, at a glance, before 8 AM ET:

1. **Did today's run happen?** (scheduled 00:01 ET + watchdog) — green/amber/red with time.
2. **Is it complete and fresh?** freshness dot + collection duration + last-updated.
3. **Is coverage assured?** Coverage % (once instrumented), failed sources, missing-category alerts.
4. **Is quality acceptable?** dedupe %, avg relevance/quality, items needing review.
5. **Is it delivered?** Word/HTML/Excel ready, email sent, to whom, at what time, SendGrid OK.
6. **What needs a human?** exceptions list (failed sources, low-confidence items, delivery errors).

**Persona monitoring map:**
| Persona | Primary view | Must see |
|---|---|---|
| FCC COR | Ops Console + Coverage Assurance | completeness proof, delivery record, on-time SLA |
| Federal PM / Contracting Officer | Analytics SLA calendar | business-day delivery %, exceptions |
| Operations Manager | Collection Pipeline + Run History | source health, failures, retries, duration |
| QA Lead / Morning Editor | QA Review | issues, dedupe, missing fields, leadership pin |
| Media Analyst | Daily Briefing + Archive | clustered stories, search, relevance |
| Help Desk | Run History + Audit | what happened, when, by whom |
| Executive Leadership | Analytics | trends, SLA, volume vs target |

---

## 5. Coverage Assurance Design (CRITICAL — and honesty-gated)

**Goal:** prove to the Government the bulletin is complete — with numbers the system actually measures.

**Definitions (must be computed, never hardcoded):**
- **Expected sources** = the enabled set in a **Source Registry** (built from the source-catalog research). This is the denominator; it does not exist yet.
- **Successful sources** = registry sources that returned ≥1 item (or a valid empty-but-reachable response) this run.
- **Failed sources** = registry sources that errored/timed out (needs per-source outcome tracking — currently only `drop_reason` in debug logs).
- **Coverage %** = successful ÷ expected.
- **Coverage confidence** = importance-weighted coverage (P1 gov/trade sources weighted heaviest), so a missed local paper ≠ a missed FCC.gov.

**Primary-source backstop (already true, make it explicit):** FCC.gov Daily Digest + Federal Register + ECFS are always-collected, so **every official FCC action is captured regardless of media pickup**. Surface this as an assurance statement: *"All FCC official releases for <date> captured (primary-source backstop)."* This is a real, defensible completeness claim today.

**Honesty guardrail (non-negotiable):** until per-source outcome tracking + the registry exist, **do not display a Coverage %.** Show what's real now (sources scanned, dedupe %, avg relevance/quality, missing-category alerts, primary-source backstop) and label Coverage % "pending instrumentation." A fabricated coverage number is a false statement to a federal client.

**Panel contents (phase in as instrumented):** Coverage % + confidence (gauge), expected/scanned/succeeded/failed (with failed-source list), duplicate %, avg relevance, avg quality, avg AI confidence, missing-category alerts, primary-source backstop ✔.

---

## 6. Collection Pipeline Design

**Purpose:** turn the black box into an observable pipeline (Sentinel/Splunk inspiration).

**Views:**
1. **Source Registry table** — Source · Type · Tier · Last fetch · Items · **Outcome (ok/slow/failed)** · Avg response · Retries · Enabled. *(Requires the new per-source outcome tracking + registry.)*
2. **Live run panel** — stages Ingest → Dedup → Classify → Cluster → Build, each with a progress bar + live counts; streamed log; cancel.
3. **Connector health** — RSS / NewsAPI / Tavily / GDELT / FCC.gov — up/down, last success, error.

**Data:** `_build_coverage_report` gives scanned/collected/rejected/dupes/in-briefing today; the per-source outcome + timing is the new instrumentation (capture the existing `drop_reason` into a persisted per-source result instead of a debug log).

**Alerts:** "P1 source returned 0 today," "feed failing 3 consecutive runs," "collection running >X min," "collection did not start" (ties to the existing scheduler watchdog).

---

## 7. QA Workflow Design

**Purpose:** an in-app editorial QA gate before delivery (today QA = open an Excel).

**Workflow:** run completes → **QA queue** auto-populates issues → editor resolves/accepts → "QA Passed" stamp → delivery unlocked. (Optional gate; keep live-feed publish but record QA state.)

**Auto-checks (mostly computable from existing data):** duplicate clusters, low-relevance/low-quality items, missing summary/URL/pub-date, stale dates, missing-category warnings, paywall audit, leadership-coverage present. **Actions:** exclude/restore item, add editorial note, pin leadership, mark priority, request AI re-summary (records an audit event). **Output:** QA status (Passed/Passed-with-notes/Blocked) stamped on the run and on exports.

---

## 8. Delivery Workflow Design

**Purpose:** make the contract deliverable visible and controlled (today it's backend-only `/send`).

**Compose & send:** recipient list (from agency config), subject, **exact email preview** (summary + "VIEW FULL BRIEFING" button), test-send-to-self, send, schedule.
**Validation (pre-send):** artifacts generated (Word/HTML/Excel ✔), email renders, recipients valid, **SendGrid sender verified** (surface the 403-on-unverified condition honestly — this bit you before), QA status acceptable.
**Delivery record-of-authority (audit):** persistent log — run id, artifacts, recipients, subject, sent-at, SendGrid message id/result, per-recipient status, opens if tracked. Re-send with reason. This is the COR's proof of delivery.

---

## 9. Audit Trail Design

**Gap:** no bulletin audit table exists. **Proposal (additive — does NOT modify existing schema):** a new append-only `bulletin_audit_log` (design only): `id, ts, actor, event_type, entity (briefing/run/source), action, before/after or details(JSON), result`.

**Events to record:** collection start/finish, retry, per-source failure, export, download, email send, QA action (exclude/note/approve), AI regeneration, manual edit, config change, delivery result, warnings/failures.

**UI:** an **Audit** view (filter by date/actor/event/entity) + inline "history" on each run/briefing. Immutable/append-only (follow the same append-only discipline used elsewhere in the platform — without touching those modules).

**Government value:** this is the artifact a COR/IG relies on; append-only auditability is a Moderate-baseline expectation.

---

## 10. Accessibility Review (Section 508 / WCAG 2.1 AA)

Current risks (from the real UI): color-only status, emoji icons, non-semantic tables, no keyboard model, modal without focus trap, inline styles, unlabeled date inputs, charts without text alternatives.

**Requirements:** status = icon+text pills; real iconography with `aria-label`; semantic `<table>`/`<th scope>`; roving `tabindex` on card lists; visible focus + `Esc`/focus-trap in modals; labeled forms; contrast audit of Fluent tints; chart data tables; `prefers-reduced-motion`; ARIA live-regions announcing async collect/send. **508 is a contractual requirement, not a nicety** — treat as Critical.

**Keyboard model:** `/` search · `g d/h/a/n/y` navigate · `c` collect · `e` export · `s` send · `j/k` next/prev · `x` exclude · `?` cheat-sheet.

---

## 11. Security Review

| # | Finding | Severity | Note |
|---|---|---|---|
| S1 | **Bulletin API is unauthenticated** — the `/api/v1/bulletin` router has no auth dependency (unlike TEFCA's `require_role`). `/collect` (triggers costly LLM run), `/send` (emails the client), `/admin/purge-articles` (only a static confirm token) are callable by anyone reaching the host. | **Critical** | Add auth/role-gating to state-changing + costly endpoints; keep only read-only public where intended. |
| S2 | Public `/status` previously leaked a contract number (already removed) | resolved | Keep the no-contract-data discipline in UI + API. |
| S3 | Cost-DoS: unauthenticated `/collect` can be triggered repeatedly → LLM spend | High | Auth + rate-limit + the existing cycle-lock. |
| S4 | Paid-source licensing / scraping ToS (Communications Daily, Politico Pro, Law360) | High | Licensed metadata-only; never scrape paywalls. |
| S5 | SendGrid sender verification (agtbi.com 403s) | Medium | Surface + use a verified sender; don't silently fail. |
| S6 | Recipient PII in delivery config/logs | Medium | Access-control the delivery/audit views. |
| S7 | TrustedHost/CORS already enforced upstream | mitigant | Good; not a substitute for endpoint auth. |

---

## 12. Performance & Scalability Review

- **P1** Single 931-line page loads Archive with `page_size=500` and filters **client-side** → sluggish; add server-side pagination + virtualization. (Medium)
- **P2** Synchronous `/collect` (~1–2 min) can exceed the edge/proxy timeout → poll `/latest` pattern (already used); make async + progress the norm. (Medium)
- **P3** Word/PDF/Excel re-rendered on every request → cache per briefing id. (Low)
- **P4** LLM cost scales with volume; classify caps + relevance-filter-before-LLM already exist — preserve and tune for the "60 stories / more sources" goal. (Ongoing)
- **P5** At 2,000+ sources: ingestion concurrency, dedup/cluster cost, and per-source tracking storage grow — design the Source Registry + outcome table for that scale now. (High, design-time)

---

## 13. Risk Assessment

| Risk | Likelihood | Impact | Mitigation (exists?) |
|---|---|---|---|
| Missed morning delivery (SLA breach) | Medium | **High** (contract) | Scheduler + watchdog + retry exist; add Ops Console alerting + delivery validation |
| Cannot prove completeness to COR | High (today) | High | Coverage Assurance (§5) — but only after honest instrumentation |
| **Displaying a fabricated Coverage %** | Medium | **High** (false federal claim) | Guardrail: don't show until measured (§5) |
| Unauthenticated endpoints abused | Medium | High | Auth (§11 S1) |
| Delivery fails silently (SendGrid) | Medium | High | Delivery validation + log (§8) |
| 508 non-compliance flagged | High | Medium–High | Accessibility pass (§10) |
| Single-page maintainability at feature scale | High | Medium | Componentize during build (design-token cleanup) |

---

## 14. Gap Analysis (summary)

- **Surface-existing (fast wins):** coverage report, window stats, cluster ratio, relevance/quality, delivery record, missing-category + subscription + leadership flags.
- **Instrument-new (real work):** per-source outcomes/retries/timing, Source Registry + expected-sources → honest Coverage %, AI-confidence, audit trail, delivery log.
- **Harden (must):** endpoint auth, Section 508, honest metrics, delivery validation.
- **New screens:** Ops Console, Collection Pipeline, QA Review, Delivery, Export, Audit.

---

## 15. Implementation Roadmap (ROI-annotated)

Effort: **S** ≤1d · **M** 2–4d · **L** 1–2w (frontend unless noted; "+BE" = new backend instrumentation).

### CRITICAL
| # | Item | Business value | Government value | Effort | Dependencies | Risk | ROI |
|---|---|---|---|---|---|---|---|
| C1 | Fix stale `pending_approval` → honest status | trust | accurate status to COR | S | none | none | Very high |
| C2 | **Authenticate bulletin API** (gate `/collect`,`/send`,`/admin/*`) | prevents abuse/cost | security baseline | M +BE | shared auth (reuse, don't modify) | low | Very high |
| C3 | **Delivery screen + validation + log** (surface `/send`, SendGrid status, recipients, delivery record) | operational control | proof of delivery (record of authority) | M +BE(log) | audit table (C6) | med | Very high |
| C4 | **Section 508 pass** (status text, semantic tables, focus, labels) | fewer support issues | contractual 508 | M | none | low | High |
| C5 | **Coverage Assurance panel — honest subset** (scanned, dedupe %, avg relevance/quality, missing-category, primary-source backstop) | credibility | "nothing missed" evidence (real) | M | coverage report (exists) | low | Very high |
| C6 | **Audit trail** (additive `bulletin_audit_log` + Audit view) | traceability | IG/COR auditability | M +BE | none | low | High |

### HIGH
| # | Item | Business | Government | Effort | Deps | Risk | ROI |
|---|---|---|---|---|---|---|---|
| H1 | **Morning Ops Console** (the 8 AM answer-at-a-glance) | fast decisions | daily assurance | M | C1/C3/C5 | low | Very high |
| H2 | **Per-source outcome instrumentation** (success/fail/retry/response time) | reliability | failed-source transparency | M +BE | source registry | med | High |
| H3 | **Source Registry + expected-sources → honest Coverage %** | completeness | true Coverage % + confidence | L +BE | H2, catalog | med | Very high |
| H4 | **Collection Pipeline screen** | ops visibility | monitoring | M | H2 | low | High |
| H5 | **Run History funnel + true status + retry** | debuggability | run assurance | M +BE(timing) | none | low | High |
| H6 | **Export metadata stamp** (run id, gen time, counts, dupe %, coverage confidence, version, QA status, generated-by) + unified Export + fix range bug | deliverable quality | provenance on every artifact | M | C5/C6 | low | High |

### MEDIUM
| # | Item | Business | Government | Effort | Deps | Risk | ROI |
|---|---|---|---|---|---|---|---|
| M1 | **QA Review screen** (native issues + actions + QA stamp) | editorial speed | quality gate | M | C6 | low | High |
| M2 | **Analytics upgrade** (SLA delivery calendar, trends, dedupe ratio, top outlets, volume vs target) | reporting | SLA evidence | M–L | H5 | low | High |
| M3 | **Archive facets + result export + saved searches** | analyst productivity | research/FOIA support | M | none | low | Med |
| M4 | **Agency config editing** (recipients, schedule, caps, feeds, health) | self-service | delivery control | M +BE | C2 | low | Med |
| M5 | **AI confidence + inline clustering/relevance chips** | transparency | quality insight | M +BE | none | low | Med |
| M6 | Keyboard shortcuts + cheat-sheet | power-user speed | efficiency | S–M | C4 | low | Med |

### LOW
| # | Item | Effort | ROI |
|---|---|---|---|
| L1 | Saved views/searches + alerting | M | Med |
| L2 | Broadcast clips view (endpoint exists) | S | Low |
| L3 | LLM-visibility panel (endpoint exists) | S | Low |
| L4 | Componentization / design-token cleanup (retire inline styles) | L | Med (maintainability) |
| L5 | Export cover-page / handling markings | S | Med (gov) |

**Sequencing:** **C1–C6 first** (honesty + security + delivery + 508 + real coverage subset + audit) → **H1 Ops Console** (the daily-operations centerpiece) → **H2–H3** (the honest Coverage % is the flagship government feature, but only after instrumentation) → H4–H6 → Medium → Low. Most Critical/High items are **frontend over existing data + a few additive backend instruments**; none require touching TEFCA, healthcare, other modules, existing schema, or existing APIs (new endpoints/tables are additive).

---

*No code, UI, CSS, SQL, or migrations were produced. This is a design blueprint only. Await approval before any implementation.*
