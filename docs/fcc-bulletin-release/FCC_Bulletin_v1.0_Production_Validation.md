# FCC Bulletin v1.0 — Production Validation

**Prepared:** 2026-07-08
**Method:** One **real** in-memory Development collection cycle. Keyless providers (RSS across all configured feeds incl. the Google News fallback, GDELT) fetched **live**. No database writes, no briefing persistence, no production mutation (`DATABASE_URL` was unset for the run). All analysis functions run on the live-collected set.
**Honesty statement:** Every number below is measured from the actual run (`validation_result.json`). Where a provider's key is not present in this environment, it is reported as **NOT EXERCISED** — never estimated, never fabricated.

---

## 0. Scope reality — read this first

The mandate was "run one complete Development collection with **all** providers enabled." That could **not** be fully honored in this environment, for a verifiable reason:

| Provider | Key required | Present locally? | Exercised? |
|---|---|---|---|
| RSS (incl. Google News fallback) | none | — | ✅ **Yes (live)** |
| GDELT | none | — | ✅ **Yes (live)** |
| NewsAPI.ai | `NEWSAPI_AI_KEY` | ❌ **No** (Railway only) | ❌ No |
| NewsAPI.org | `NEWSAPI_KEY` | ❌ No | ❌ No |
| Tavily | `TAVILY_API_KEY` | ❌ No | ❌ No |

**Consequence:** the central subject of this release — **NewsAPI.ai** — could not be exercised here, because its key lives only in Railway (Dev/Prod), and this workstation must not run against the production database. Therefore **this is a PARTIAL validation**, and per the safety rule I am **not** asserting a full multi-provider comparison or tagging Production Ready on local evidence alone. The complete run must execute in **Railway Development**. Exact criteria + command are in §7.

What *was* validated for real: the collection pipeline end-to-end, the two fixed missing sources, the false-positive fix (which the run actively improved), deduplication, provider analytics/stamping, the registry editorial queue, and all three exports with no regressions.

---

## 1. Run summary (real)

| Metric | Value |
|---|---|
| Providers exercised | RSS (live), GDELT (live) |
| Total articles collected | **142** (RSS 142; GDELT 0 in-window this cycle) |
| After deduplication | **134** |
| Duplicates removed | **8 (5.6%)** |
| FCC-relevant after dedup (deterministic 3-tier gate) | **76 / 134 = 56.7%** |
| Corporate-noise rejected (run-time, pre-fix filter) | 5 (+1 found & fixed — see §4) |
| RSS fetch time (~450 feeds, concurrent) | **105.5 s** |
| GDELT fetch time | **15.2 s** |

> GDELT returned **0 in-window items** this cycle (exact-phrase "Federal Communications Commission", 24 h). That is a real, expected sparsity of the GDELT DOC index for that narrow query/window — not a code fault. It surfaces more on high-volume FCC news days and via the keyed providers.

---

## 2. Provider Performance Report (real)

Only exercised providers carry real numbers. Keyed providers are honestly blank.

| Provider | Collected | Unique | Duplicates | Accepted | Rejected | Avg Relevance | Unique % | Response time |
|---|---|---|---|---|---|---|---|---|
| **RSS** | 142 | 134 | 8 | 129 | 5 | 0.75 | 94.4% | *not instrumented (null)* |
| **GDELT** | 0 | 0 | 0 | 0 | 0 | — | — | 15,178 ms (fetch) |
| **NewsAPI.ai** | — | — | — | — | — | — | — | **NOT EXERCISED (no key)** |
| **NewsAPI.org** | — | — | — | — | — | — | — | **NOT EXERCISED (no key)** |
| **Tavily** | — | — | — | — | — | — | — | **NOT EXERCISED (no key)** |

- Per-provider **response time** is reported `null` — per-ingester timing is not yet instrumented (honest; listed as pending work). The RSS/GDELT figures above are whole-phase fetch times, not per-source.
- `average_relevance` 0.75 is the RSS prior (Claude classification was intentionally **not** run — it costs real API spend and is unnecessary for these structural checks; the FCC-relevance % in §1 is computed deterministically instead).

---

## 3. Coverage Comparison Report (NewsAPI.ai vs others)

**Status: PENDING MEASUREMENT — NewsAPI.ai not exercised locally.**

The comparison engine ran and produced a real (empty) result because NewsAPI.ai collected 0 articles without its key:

```
target_provider: NewsAPI.ai
target_collected: 0          other_collected: 142
unique_to_target: 0          duplicate_with_others: 0
additional_fcc_stories: 0    stories_missed_by_others: 0
```

This is **not** evidence that NewsAPI.ai adds nothing — it is evidence that **it was not run**. A truthful coverage comparison requires the Railway Dev run (§7). The comparison code is verified working (unit-tested with a synthetic multi-provider set: 1 duplicate + 1 unique correctly separated).

---

## 4. False-positive validation — and a real finding

**Control case (exact UAT example):** `"T-Mobile Names New Chief Marketing Officer in Executive Appointment"` → **REJECTED** ✅

**Live finding:** the run collected a real item — `"T-Mobile Bringing on Former AT&T Exec Chris Sambar"` — that **slipped through** the original filter (the "bringing on / Exec" phrasing wasn't a marker). This is precisely what validation is for.

**Action taken:** strengthened the corporate-announcement markers (exec-move phrasings: `bringing on`, `brings on`, `exec`, `svp/evp/vp for`, `rises to`, `chief revenue/marketing`, …). Re-verified against the **real collected items**, 9/9 correct:

| Real collected headline | Verdict |
|---|---|
| FCC Sees T-Mobile Grain Spectrum Swap Boosting D2D | ✅ Kept (FCC nexus) |
| **T-Mobile Bringing on Former AT&T Exec Chris Sambar** | ❌ **Rejected (now fixed)** |
| T-Mobile gets FCC approval for Grain Management spectrum swap | ✅ Kept (FCC nexus) |
| Top EchoStar Executive Hamid Akhavan Resigns | ❌ Rejected |
| Boyd Rises To Regional VP For Cumulus | ❌ Rejected |
| SBS … Bankruptcy Exit Waits On FCC | ✅ Kept (FCC nexus) |

The nexus guard held: every FCC-tied story that mentions a company was kept. (Because a code change was made *after* the collection, the run's `corporate_rejected=5` reflects the pre-fix filter; the fix is unit-verified and will apply on the next full run.)

---

## 5. Missing-Source verification (real)

| Source | Collected this run | Evidence | Verdict |
|---|---|---|---|
| **Radio Insight** | **12 items** | e.g. "Mason's Observations…", "KDAO-FM Rewinds To The 90s" (radio-trade; downstream relevance gate handles non-FCC ones) | ✅ **Now collected** (was never wired) |
| **Inside Radio** | **3 items, all FCC-relevant** | "…Bankruptcy Exit Waits On FCC", "Cumulus…Waits On FCC Approval", "Saga Seeks FCC Probe Of LPFM Applicant" | ✅ **Now collected** via gated Google News fallback — working exactly as intended |
| **Fierce Network** | **0 in-window** | Standalone probe earlier: `fierce-network.com/rss/xml` → **200, 25 items** (reachable). 0 items fell inside the business-day freshness window this cycle. | ⚠️ **Reachable & wired, 0 in-window this run** |

**Honest note on Fierce Network:** the live `fierce-network.com` feed is confirmed reachable and wired; the permanently-403 `fiercewireless.com` duplicates are the dead ones. A 0-item cycle is consistent with the freshness window excluding Fierce's items for that day OR a transient fetch failure under concurrent load. Disambiguating requires the per-feed health instrumentation (pending work item). It cannot be asserted as "collected every run" from this single cycle — reported truthfully as reachable, not yet observed in-window.

---

## 6. Duplicate Analysis + Missed Story Report

**Duplicate analysis (real):** 8 of 142 removed (5.6%) by the exact-hash + `title[:60]` dedup within the keyless set. **Cross-provider** dedup (canonical-URL, the new `provider_analysis` path) could not be exercised because only one real provider (RSS) ran — cross-provider overlap needs ≥2 keyed providers. Verified working in unit tests.

**Missed Story Report:** an *absolute* missed-story list cannot be produced honestly — there is still **no external reference feed** (Talkwalker/Meltwater) wired to diff against. Per the safety rule I will not fabricate one. What the run *does* show: the **registry editorial queue** (real) discovered 23 outlets carrying FCC-adjacent stories —

| Verdict | Count |
|---|---|
| Already in registry | 12 |
| Needs review | 8 |
| Potential approval (≥2 FCC stories) | 3 |

— advisory only, **no auto-import** (`auto_import: false`). The absolute miss-rate measurement remains the reference-diff harness recommended in the Coverage Gap Report.

---

## 7. Export regression (real) — PASS

All three exports built from the live-collected set, no errors:

| Export | Result | Evidence |
|---|---|---|
| **HTML** | ✅ | 182,419 bytes; valid `<!DOCTYPE`; **"Back to Top" ×107** (per story) |
| **Word (.docx)** | ✅ | 73,267 bytes; valid document |
| **Excel (.xlsx)** | ✅ | 120 rows; **`Provider` column present** (col 11); existing columns unchanged |

US date format ("July 7, 2026", no leading zero) verified in helpers and applied in header/exports. **No regressions.**

---

## 8. Go / No-Go — and how to complete it

### Verdict: ⛔ **NOT tagged "FCC Bulletin v1.0 Production Ready"** on this local run.

Because the honest success criteria are not all met **here**:

| Criterion | Local result |
|---|---|
| Real collection runs, no crash | ✅ |
| Radio Insight & Inside Radio collected | ✅ |
| T-Mobile exec announcement rejected | ✅ (+ a slipped case found & fixed) |
| Word/Excel/HTML — no regressions | ✅ |
| All reports from real data only | ✅ (gaps labeled, nothing faked) |
| **NewsAPI.ai exercised & compared** | ❌ **not possible without the key** |
| **Full multi-provider comparison** | ❌ blocked by the above |

Tagging Production Ready now would assert the new provider works in production without ever having run it — which the safety rules forbid.

### To complete validation (Railway Development, where all keys exist)

1. Confirm `NEWSAPI_AI_KEY`, `NEWSAPI_KEY`, `TAVILY_API_KEY`, `ANTHROPIC_API_KEY` are set in **Development**.
2. Trigger one collection cycle (`POST /collect` for agency `fcc`) — or run the same harness pointed at the Dev DB.
3. Confirm from that run's `coverage.provider_analytics` + `coverage.provider_coverage_comparison`:
   - NewsAPI.ai `articles_collected > 0` and appears alongside RSS/GDELT/NewsAPI.org/Tavily;
   - NewsAPI.ai `additional_fcc_stories ≥ 1` (it adds coverage);
   - existing providers still return their normal volumes (no regression).
4. Confirm the T-Mobile-class item is absent from the delivered briefing and Fierce Network appears on a normal news day.
5. If all pass → apply the tag on the validated commit:
   `git tag FCC-BULLETIN-V1.0-PRODUCTION-READY && git push --tags`

---

## 9. Executive Summary

- A **real** Development collection ran: **142 articles** from live RSS across ~450 feeds; deduplicated to 134 (5.6% dup); 56.7% FCC-relevant by the deterministic gate. No crashes; **all three exports (HTML/Word/Excel) built clean with the new Provider column and no regressions.**
- **Two of the three previously-missed sources are now collected for real** — Radio Insight (12 items) and Inside Radio (3 items, all genuinely FCC-related via the gated Google-News fallback). **Fierce Network's feed is confirmed reachable and wired** but had 0 in-window items this single cycle (freshness/transient — needs per-feed health instrumentation to confirm steady-state).
- The **false-positive fix works and got better**: the exact T-Mobile UAT example is rejected, and the run **caught a second exec-hire that had slipped through** — now fixed and re-verified against the real data.
- **NewsAPI.ai itself was not exercised** because its key is Railway-only and this box must not touch production. So the **Provider Comparison / Coverage-after / cross-provider dedup reports are honestly PENDING** — the code is in place and unit-verified; they need the Dev run to populate.
- **Release decision: hold the Production-Ready tag** until the Railway Dev run in §7 passes. Every fix is verified; the one thing missing is exercising the new provider in an environment that has its key — which is a deployment/credentials step, not a code gap.

*No coverage %, no per-provider comparison, and no missed-story list was invented. The blocked items are labeled blocked, with the exact steps to unblock them.*
