# FCC News Bulletin — Gap Analysis (research only)

Compares the **current** bulletin collection (as observed in the engine) against the target source library in this catalog. **No code changes here** — findings only.

## What the current pipeline already has (strengths)
- Ungated **FCC RSS feeds** + gated **major-outlet feeds**; concurrent RSS ingest.
- **GDELT DOC** (editorial index), **NewsAPI**, **Tavily** discovery.
- **Primary sources:** FCC.gov digest/headlines, congressional hearing transcripts (GovInfo).
- **3-tier relevance filter**, FCC category structure, **chairman/leadership tracking**, related-story **clustering**, paywall flags, Eastern-Time freshness window.
- Per-feed + classify caps for cost control.

## Gaps vs. target library

| # | Gap | Impact | Recommendation (design) |
|---|---|---|---|
| G1 | **Social collectors present** (BlueSky / YouTube / Reddit ingest exist in engine) | **Contract violation** — bulletin must be editorial/official only | Disable/retire social ingest paths; they are out of scope. *(Flag for the code phase — not changed here.)* |
| G2 | **Government primary APIs under-used** — Federal Register, Regulations.gov, Congress.gov, GovInfo, ECFS not all wired as first-class API sources | Risk of missing rules/dockets/comment deadlines that never hit media | Add these as always-collect API sources (P1) |
| G3 | **Missing trade specialists** — e.g., Communications Daily, TR Daily, Broadband Breakfast, Multichannel/Next TV, TVNewsCheck, Radio World, Via Satellite, SpaceNews, Urgent Communications, Inside Towers, PolicyTracker | These carry the **densest FCC signal**; absence = missed stories | Add P1/P2 trade set (licensed where paid) |
| G4 | **No company newsrooms** — AT&T/Verizon/T-Mobile/Comcast/Charter/DISH-EchoStar/SpaceX-Starlink/Amazon Kuiper/Motorola Solutions | Miss first-party announcements (deals, launches, filings) before pickup | Add ~24 newsroom feeds (P3), dedup-heavy |
| G5 | **Press-release wires not industry-filtered** | Either missed or noisy | Add PRN/BW/GNW **filtered to Telecom/Broadcast** only |
| G6 | **Legal/court thin** — Law360, CommLawBlog, Wiley, Kelley Drye, SCOTUSblog, appellate opinions (CourtListener) | Miss FCC litigation / appeals (Chevron-era remands, forbearance suits) | Add legal set (P2/P3) |
| G7 | **State PUCs / regional-local absent** | Miss local-impact FCC stories (station renewals, BEAD/RDOF awards, siting) | Bulk-ingest via directories (USNPL, FCC LMS/Radio Query, NARUC) |
| G8 | **Associations/think tanks partial** — CTIA/NAB/NCTA/USTelecom/INCOMPAS/SIA/Benton/Public Knowledge/ITIF | Miss ex parte/comment signals + policy framing | Add association newsroom feeds (P2) |
| G9 | **Coverage-integrity alerting** — no "P1 source silent today" check | Silent collection failure could drop a day's official items | Add gap-alert on P1 sources (design in Collection_Strategy §4) |
| G10 | **Freshness of paid feeds** — reliance on free discovery may lag Communications Daily/Politico Pro | Late on breaking FCC beats | License the 2–3 premium FCC-beat sources; metadata-only |

## Priority of closure
1. **G1** (compliance — retire social) and **G2** (government APIs) first.
2. **G3/G6/G8** (trade + legal + associations) — biggest "never miss" wins.
3. **G4/G5** (newsrooms + filtered wires).
4. **G7** (long-tail directories) — largest volume, gated + dedup.
5. **G9/G10** (integrity + premium licensing).

## Net
The current system is a solid base but (a) **includes social it must not**, and (b) **misses the highest-signal FCC trade, government-API, legal, newsroom, and local sources**. Closing G1–G3 alone materially reduces "missed story" risk; the full catalog + directory ingest reaches the comprehensive 2,000+ target — all editorial/official.
