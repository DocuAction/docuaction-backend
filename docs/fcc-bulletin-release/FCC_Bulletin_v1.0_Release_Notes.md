# FCC Bulletin v1.0 — Release Notes

**Date:** 2026-07-08
**Scope:** FCC Bulletin Intelligence module only. All changes additive and backward-compatible.
**Branch:** `feature/fcc-newsapi-ai-validation` (`f344653`). Not yet merged/tagged/deployed.

---

## Highlights

FCC Bulletin v1.0 completes the modernization of the FCC Daily News Monitoring service: a modern operations UI, a 194-source approved registry, multi-provider collection with the new **NewsAPI.ai** collector, full **provider tracking & analytics**, editorial quality improvements, export polish, and a correctness fix for the daily scheduler's self-healing watchdog.

## Features

- **NewsAPI.ai collection provider** — added as an additional collector (Event Registry). Auto-detected via `NEWSAPI_AI_KEY`; skips gracefully when absent. Runs through the same normalize → boolean → AI relevance → dedup → editorial → categorize pipeline as every other provider (no gate bypass).
- **Provider tracking** — every article now carries `provider`, `provider_url`, `source_name`, `collection_method`, and `collection_time`.
- **Provider analytics** — per-provider articles collected / unique / duplicates / accepted / rejected / average relevance / unique %, surfaced in the coverage report (per-provider response time reported as pending — not instrumented).
- **Coverage comparison** — after each run, NewsAPI.ai vs. all other providers by canonical URL: unique, duplicate, additional-FCC, and stories-only-this-provider-had.
- **Registry editorial queue** — discovered outlets are classified against the 194-source registry (Already Exists / Duplicate / New / Potential Approval / Needs Review) as an advisory queue. Never auto-imports.
- **194-source approved registry** — completed and active.
- **Coverage Assurance & PWS Coverage** — honest coverage surfacing; no fabricated percentages.

## Bug fixes

- **Scheduler event-loop error (critical)** — fixed `Watchdog tick error: There is no current event loop in thread 'ThreadPoolExecutor-0_0'`. Root cause: the hourly watchdog and the Mon–Sat delivery / Monday rollup / Sunday preview jobs were synchronous, so `AsyncIOScheduler` dispatched them to a worker thread where `asyncio.get_event_loop()` raises on Python 3.10+. Fix: those jobs are now coroutines awaited on the event loop. All jobs preserved (same ids/triggers/names).
- **False positives (UAT)** — corporate announcements (e.g. a T-Mobile executive appointment, earnings, product launches) are now rejected unless tied to a real FCC nexus (FCC, spectrum, licensing, rulemaking, enforcement, merger requiring FCC approval, broadcast ownership, telecom regulation). Reversible via `BULLETIN_EDITORIAL_STRICT`.
- **Missing sources (UAT)** — **Radio Insight** feed wired (was never configured); **Inside Radio** added via an FCC-gated Google-News fallback (its direct feed is bot-blocked/paywalled); **Fierce Network** confirmed reachable and already wired (the dead `fiercewireless.com` duplicate is superseded by `fierce-network.com`).

## UI improvements

- Modernized FCC Bulletin UI with Operations, Pipeline, QA, Delivery, and PWS Coverage tabs plus the Coverage Assurance panel (honest "Not Available" when no run).
- Consistent **US date formatting** — "July 7, 2026" (no leading zero; never "7 July 2026").
- **Back to Top** link rendered after every story with consistent spacing.
- Similar Stories grouped as primary + related coverage.

## Provider integrations

Active collectors: **RSS** (incl. Google News fallback), **GDELT**, **NewsAPI.org**, **NewsAPI.ai (new)**, **Tavily**, plus government sources **Federal Register** and **ECFS/Congress**. NewsAPI.ai is additive — existing providers are unchanged.

## Export improvements

- **Word / Excel / HTML** exports maintained; no regressions.
- **Excel** gains an optional **Provider** column (appended last; existing columns unchanged).
- US date formatting applied to export headers and per-article dates.

## Known limitation

The v1.0 code is complete and verified locally; a real **deployed** NewsAPI.ai collection and the deployed provider/coverage metrics remain **Pending Measurement** until the release is deployed. See the Deployment Runbook and Go/No-Go report.

## Compatibility

Additive and backward-compatible. New `Article` fields default blank and serialize into the existing JSON-in-TEXT storage — no database migration. No shared API, auth, or scheduler contract changed.
