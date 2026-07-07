# FCC News Bulletin — Collection Strategy (design only, no code)

## Objective
Never miss an important FCC story, while keeping the bulletin **credible editorial/official only** (no social). Achieve breadth via many sources; keep precision via relevance-gating + clustering.

## 1. Ingestion methods (in preference order)
1. **Official government APIs** — Federal Register, Regulations.gov, Congress.gov, GovInfo, FCC ECFS. Deterministic, ToS-clean, highest reliability. Poll 15 min–hourly.
2. **Direct RSS/Atom** — trade, legal blogs, associations, company newsrooms, regional papers. Cheapest, ToS-clean. Auto-discover feed via `/feed`, `/rss`, `<link rel=alternate>`, sitemap.
3. **News-index API (GDELT DOC 2.0)** — free, global index of **editorial** outlets; use for *discovery* of FCC mentions in outlets we don't feed directly. (Indexes news articles, not social.)
4. **Press-release industry feeds** — PR Newswire / Business Wire / GlobeNewswire filtered to **Telecommunications / Broadcasting** categories only. Captures primary company/agency announcements pre-pickup. High duplicate_risk → dedup hard.
5. **Sitemap crawl** — only for feed-less station/local sites, and only where robots.txt allows.
6. **Paid/licensed APIs** — Communications Daily, TR Daily, Politico Pro, Law360, PolicyTracker, S&P/Kagan. **Licensed access, headline/metadata only. Never scrape paywalls.**

> **No social collectors.** X/Reddit/YouTube/BlueSky/etc. are out of scope and are not part of any pipeline path.

## 2. Freshness & cadence
- P1 government + FCC-beat trade: **15 min**.
- P2 trade/national policy: **15–30 min**.
- P3 national/tech/legal/newsrooms: **hourly**.
- P4 long tail/press wires: **hourly**, with an EOD sweep.
- Bulletin freshness window already enforced in Eastern Time per FCC business hours — retain.

## 3. Normalize → Dedupe → Relevance → Cluster
1. **Normalize** to one Article schema (canonical URL, outlet, publish-date parsed, hash).
2. **Dedupe** exact + near-exact (URL/title hash) — critical at 2,000 sources.
3. **Relevance filter:** always-collect (P1/P2 core) vs **keyword/entity-gated** (P3/P4) — see Priority Matrix.
4. **Cluster** same-event coverage → one primary + similar (this is what turns "N articles" into "M stories"). Tag leadership items (CHAIRMAN CARR: …). Both already exist in the engine — scale their thresholds for higher volume.

## 4. Coverage assurance ("never miss")
- **Primary-source backstop:** because FCC.gov + Federal Register + ECFS are always-collected, any official FCC action is captured **regardless of media pickup**. Media sources add context/analysis, not the ground-truth trigger.
- **Gap alerting:** if a day's collection yields zero items from a P1 source that normally publishes, raise an internal alert (source may be down) — coverage-integrity check.
- **Cross-check:** cluster-level "did any P1/P2 source cover topic X" audit.

## 5. Legal / ToS posture
- Respect `robots.txt`; prefer feeds/APIs over scraping.
- Paywalled = licensed metadata only.
- Press-release and newsroom content is first-party (low legal risk) but high duplicate_risk.
- Attribute source + link on every item.

## 6. Cost control
- Relevance filter + dedup + cluster **before** any LLM classification/summarization.
- Per-source pull caps; classify caps (patterns already in the engine).
- API-first (free gov APIs) minimizes scraping cost and fragility.
