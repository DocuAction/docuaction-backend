# FCC News Bulletin — Source Architecture & Methodology

**Status:** Research deliverable (no code, no DB, no API changes). Scope: FCC News Bulletin module only.
**Objective:** the most comprehensive FCC-relevant **editorial + official** source library, so the Daily Bulletin never misses an important FCC story.

## Guiding principle — editorial & official ONLY

Per contract this is a **news bulletin**, not social listening. **Excluded entirely:** X/Twitter, Facebook, Instagram, TikTok, Reddit, Threads, BlueSky, Mastodon, Telegram, Discord, LinkedIn posts, YouTube comments, influencer/hashtag/engagement content. No social collector is designed or recommended. Sources are limited to the 23 approved editorial/official categories.

## Two-part model (how "comprehensive" is achieved honestly)

1. **Curated high-value core (~120, this catalog):** nationals, telecom/broadband/broadcast/radio/satellite trade, legal, government/primary, company newsrooms, press-release rails — hand-verified, worth manual RSS/API wiring. This is where ~80% of FCC signal lives (esp. **Telecom trade + Government**).
2. **Programmatic long tail (to 2,000+):** regional/local papers, business journals, and licensed TV/radio stations pulled from **real public directories** — not hand-typed. See `scale_path` in `Master_Source_Catalog.json` and `State_By_State_Inventory.md`. Nothing fabricated; unverifiable fields = `TBD`.

## 18-field capture schema

`publication_name, website, rss_feed, official_api, coverage_type, coverage_area, editorial_focus, publisher, content_frequency, fcc_relevance, subscription_required, free_or_paid, reliability_score(1-10), authority_score(1-10), primary_topics, duplicate_risk, search_capability, notes`

- **fcc_relevance:** High / Medium / Low (FCC-specificity of the outlet).
- **reliability_score:** 10 = primary/wire; 8-9 = established trade/national; 6-7 = reputable regional/law-firm blog; ≤5 = aggregator/PR wire.
- **authority_score:** institutional weight on FCC matters (FCC-beat specialists and primary sources score highest).
- **duplicate_risk:** likelihood the item is syndicated/repeats a wire → drives clustering priority.

## Collection architecture (design only)

```
        ┌───────────────── SOURCE REGISTRY (from Master_Source_Catalog) ─────────────────┐
        │  per-source: method · fcc_relevance · gated? · cadence · enabled                │
        └───────┬───────────────┬───────────────┬───────────────┬───────────────┬────────┘
                ▼               ▼               ▼               ▼               ▼
            RSS/Atom       Official APIs    News-index API    Sitemap crawl   Press-release
          (trade/legal/  (FCC ECFS/LMS,   (GDELT DOC —      (feed-less        industry feeds
           newsrooms/     Fed Register,    editorial index)  station/paper     (PRN/BW/GNW,
           regional)      Regs.gov,                          sites)            filtered)
                          Congress, GovInfo)
                └───────────────┴───────────────┬───────────────┴───────────────┘
                                                ▼
                          NORMALIZE + DEDUPE  (canonical URL, hash, publish-date)
                                                ▼
                          FCC RELEVANCE FILTER  (always-collect core vs keyword/entity-gated tail)
                                                ▼
                          CLUSTER same-event  (primary + similar)  +  LEADERSHIP tagging
                                                ▼
                          BRIEFING / QA EXCEL / DELIVERY  (sections, freshness window)
```

**Method by category (recommended):**

| Category | Primary method | Cadence |
|---|---|---|
| Government / Federal agencies | **Official APIs** (Fed Register, Regs.gov, Congress, GovInfo, ECFS) + RSS | 15 min – hourly |
| Telecom / broadband / broadcast / radio / satellite trade | **Direct RSS** | 15–30 min |
| National newspapers | RSS + GDELT DOC index | 15–30 min |
| Regional / local / business journals | Auto-discovered RSS + directory ingest | hourly |
| Legal / court | RSS + JD Supra aggregation; CourtListener/PACER for opinions | hourly–daily |
| Company newsrooms | RSS (verify) / newsroom sitemap | hourly–daily |
| Press-release services | **Industry-filtered** RSS (Telecom/Broadcast categories only) | 15–30 min |

**Principles:** registry-driven (enable/disable = data); **API-first for government**; **RSS-first for trade**; scraping only where robots.txt/ToS allow (last resort, prefer GDELT for discovery); **paywalled sources (Communications Daily, TR Daily, Politico Pro, Law360, PolicyTracker) require licensed access — headline/metadata only, never scrape**; relevance-filter + cluster **before** any LLM step for cost control.
