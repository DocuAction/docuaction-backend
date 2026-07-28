# FCC Bulletin v1.0 — Coverage Gap Report (Internal UAT)

**Prepared:** 2026-07-08
**Scope:** Missed-story root-cause analysis against the FCC PWS (Solicitation 7571MN26Q00027) and the live collection architecture.
**Baselines used:** UAT Baseline `4c39a1d` (194-source registry active; July 07 run = 150 articles); `app/bulletin_intelligence/engine.py`, `pws.py`, `fcc_sources.py`, `fcc_feeds_extended.py`; source-research corpus (`docs/fcc-source-research/`) incl. `dead_sources.csv`.

---

## 0. Honesty note — what this report is, and is not

**There is no instrumented ground-truth "missed-story" log in v1.0.** The system does not yet run a comparison against an external reference feed (e.g. Talkwalker/Meltwater). This is explicit in code — `pws.py` reports:

```python
"low_confidence_stories": None,     # pending per-article confidence instrumentation
"potential_missing_stories": None,  # pending Talkwalker comparison
```

Therefore I **cannot** honestly enumerate specific article headlines that were missed on a given day — no such dataset exists. Fabricating one would violate the honesty guarantees the PWS-coverage module was built around.

What I **can** do — and what follows — is a **structural gap analysis**: trace every place the live pipeline can drop an FCC-relevant story, map it to the six root causes requested, and identify which PWS source classes the architecture will *systematically* under-serve. Every finding below is anchored to code or to the source-research data, not to speculation.

**To convert this into a data-driven miss report** you need the one missing instrument: capture a reference set (Google News "FCC" + Talkwalker export) for N business days and diff it against `/archive`. That work item is listed in §5.

---

## 1. The collection architecture as actually wired

Understanding the causes requires the real topology (not the design docs):

| Layer | What actually runs | File / evidence |
|---|---|---|
| **RSS (bulk)** | ~450 configured feeds: `FCC_RSS_FEEDS` (~40 ungated FCC/trade) + `MAJOR_OUTLET_FEEDS` (~60, FCC-gated) + `EXTENDED_FCC_OFFICIAL` (26) + `EXTENDED_OUTLET_FEEDS` (320) | `engine.py:576-916`, `fcc_feeds_extended.py` |
| **News-index APIs** | GDELT DOC 2.0 (exact-phrase "Federal Communications Commission"), NewsAPI (if key), Tavily (if key) | `engine.py:1103-1274`, `2827-2833` |
| **Primary/gov APIs** | Federal Register (**only `search_queries[:2]` × 5 results**), Congress.gov (**only if `CONGRESS_KEY`**), FCC.gov digest + GovInfo hearings | `ingest_regulatory` `engine.py:1495-1581`; `_ingest_primary_source_articles` |
| **Broadcast** | GDELT TV closed-captions (CNN/Fox/MSNBC/CSPAN/Bloomberg) | `engine.py:2843-2845` |
| **Social** | BlueSky / YouTube / Reddit **collected** but excluded from the rendered briefing | `engine.py:2846-2868`; render `news = [... source_type != "social"]` |

### The single most important architectural finding

**The 194-source registry does NOT drive collection.** `engine.py` never references the registry (`grep` for `load_source_registry`/`registry` in `engine.py` → zero hits). The registry is loaded via `POST /sources` and consumed **only** by `pws.py` for *coverage reporting and classification*. Collection runs off the hardcoded feed lists above.

Consequences:
- A source can be **"in the 194 registry" and never collected** (registry ≠ feed list).
- The collected feed set (~450) and the registry (194) are **disjoint** — neither is a subset of the other.
- `/pws-coverage` `required_source_coverage` measures registry names against *run outcomes* keyed by outlet string; because feeds and registry use different naming, coverage % is structurally understated even when the story was captured under a different outlet label.

This is the root cause behind the majority of "the source is approved but we still missed it" cases.

---

## 2. The six root-cause gates (where a story dies)

Mapped to the exact code path, in pipeline order:

| # | Requested cause | Where it lives | How a story is lost |
|---|---|---|---|
| **1** | Source in 194-registry? | `pws.py` registry vs `engine.py` feeds | Registry membership ≠ collection. If the outlet isn't in a hardcoded feed / not surfaced by GDELT/NewsAPI, it's never fetched — *regardless of registry status*. |
| **2** | RSS/feed unavailable | `_process_feed` non-200 → `continue` (`engine.py:989`) | Dead feed → silent skip. `dead_sources.csv` lists **~800 dead feeds**, including PWS-named sources (Reuters, AP, Inside Radio, Law360, Bloomberg Telecom) and every legacy `fcc.gov/rss/*` URL. No "P1 source silent today" alert exists (Gap G9). |
| **3** | API failed to return it | GDELT exact-phrase query; FR `[:2]` queries; Congress key-gated | GDELT only matches the literal phrase "Federal Communications Commission" — stories that say "the FCC" but not the full name are invisible to GDELT. Federal Register only runs the first 2 agency search queries (≤10 docs). ECFS / Regulations.gov / GovInfo dockets **not wired** (Gap G2). |
| **4** | Filtered by Boolean logic | `_is_fcc_relevant_v2` gate on all `MAJOR_OUTLET_FEEDS` (`engine.py:1050`) | 3-tier gate: generic tech terms (broadband/5G/wireless/**satellite**/space) **do not pass on their own** — they require an explicit FCC token in title or truncated (`[:400]`) summary. FCC-adjacent stories that don't name the FCC (Starlink launch, a carrier M&A, a spectrum deal) are dropped here. RSS summaries are often empty → gate sees title only → more false negatives. |
| **5** | Rejected by AI relevance | `classify_articles` + briefing filter (`engine.py:2935-2938`) | Briefing drops `topic=="other" and relevance_score < 0.4`. Relatively permissive (classifier failure defaults everything to 0.7/kept), so this is a **minor** cut vs. the pre-AI gate #4. |
| **6** | Blocked by deduplication | `deduplicate` (`engine.py:1599-1614`) + clustering | Two filters: exact `hash(url+title)` **and** `title[:60]` near-dup. Distinct stories sharing a 60-char headline prefix (bureau notices, "[Federal Register] Amendment of Part…", "FCC Announces…") collapse to one. Clustering then keeps **one primary per cluster** — related-but-distinct coverage is merged. Over-collapse risk rises with volume. |
| **+** | *(Architectural, not in your list)* Volume caps | classify cap 600; **briefing cap 150** (`engine.py:2938`) | July 07 delivered **exactly 150** — the cap was binding. On a heavy news day the lowest-scored relevant stories are silently truncated. |

---

## 3. Coverage Gap Report

Each row: the missing source/class → the dominant root cause (from §2) → recommended fix → effort → fix type. **Effort:** S = <½ day, M = ~1–3 days, L = ~1–2 weeks.

### 3A. Highest-signal gaps (close first)

| Missing source / class | Root cause | Recommended fix | Effort | Fix type |
|---|---|---|---|---|
| **Registry↔collection are disjoint** (194 approved sources not used for fetching) | #1 | Build a registry→feed loader: iterate `enabled` registry rows with an `rss_url`, feed them into `ingest_rss` as gated outlet feeds; align outlet naming so `/pws-coverage` measures truthfully | **M** | Registry update + Existing API tuning |
| **Communications Daily / TR Daily** (the definitive FCC beat; PWS "FCC-specific") | #1 (no feed exists; paywalled) | Only genuine "buy" candidate — licensed **metadata/headline** access. Nothing in our free architecture reproduces the FCC-docket beat depth | **M** | New third-party API *(justified — see §4)* |
| **Wire services: AP, Reuters, Bloomberg, Dow Jones** (all 4 PWS wires) | #2 (feeds dead) + #3 | Drop dead direct feeds; rely on GDELT/NewsAPI for AP/Reuters pickup; add **Google News site-scoped** queries (`site:reuters.com FCC`, etc.) — free, no key | **S** | RSS addition + Existing API tuning |
| **Federal Register under-collected** (only 2 queries × 5) | #3 | Raise `per_page`, iterate all agency `search_queries`, add the FCC-agency RSS (already listed) as always-collect. FR is the ground-truth backstop for every FCC action | **S** | Existing API tuning |
| **ECFS / Regulations.gov / GovInfo dockets not wired** | #3 (Gap G2) | Add ECFS + Regulations.gov as first-class always-collect API sources (free, ToS-clean). Captures rulemakings/comment deadlines that never reach media | **M** | Existing API tuning (new gov endpoints, no purchase) |
| **FCC-adjacent stories that don't name "FCC"** (Starlink/NGSO, carrier M&A, spectrum deals) | #4 | Add an **entity-gated** tier to `_is_fcc_relevant_v2`: named carriers/regulated entities (SpaceX/Starlink, T-Mobile, EchoStar…) + a regulatory verb pass, without requiring the literal "FCC" | **M** | AI prompt / gate-logic adjustment |
| **Inside Radio** (PWS FCC-specific; radio) | #2 (feed 404) | Find current feed or Google-News-scope `site:insideradio.com`; else website collector | **S–M** | RSS addition / Website collector |
| **No "P1 source silent today" alert** (Gap G9) | #2 | Coverage-integrity check: if a normally-publishing P1/P2 source yields 0 items, raise an internal alert. Turns silent feed death into a signal | **M** | Existing API tuning (instrumentation) |
| **Briefing cap truncation** (150) on heavy days | #6/caps | Make cap adaptive or raise for high-volume days; log truncated count to `/coverage` so a cap-hit is visible, not silent | **S** | Existing API tuning |

### 3B. Category gaps (close second)

| Missing source / class | Root cause | Recommended fix | Effort | Fix type |
|---|---|---|---|---|
| **Company newsrooms** (AT&T, Verizon, T-Mobile, Comcast, Charter, EchoStar/DISH, SpaceX, Amazon Kuiper, Motorola Solutions) — all dead (Gap G4) | #2 | Re-discover current newsroom feeds (many moved to `/newsroom/rss`); add ~15 as P3 entity-gated, dedup-heavy | **M** | RSS addition |
| **Associations** (CTIA, NAB, NCTA, USTelecom, INCOMPAS, WISPA, SIA) — mostly dead (Gap G8) | #2 | Re-discover feeds / press pages; add as P2. Carry ex-parte + comment signals | **M** | RSS addition / Website collector |
| **Legal / court** (Law360 dead+paid; no CourtListener/appellate) (Gap G6) | #2 | Add **CourtListener** API (free) for FCC appellate opinions + keep CommLawBlog/Wiley/Kelley Drye free blogs. Skip Law360 (paid) | **M** | New third-party API *(free — CourtListener)* + RSS addition |
| **Financial / telecom M&A** (Bloomberg Telecom dead; no S&P Global/Kagan; Dow Jones absent) | #2/#4 | Cover via entity-gated general + Google News M&A queries first; only consider paid S&P/Kagan if a measured gap persists | **S** | RSS addition / Existing API tuning |
| **TeleGeography** (international, submarine cable) — dead, still configured | #2 | Remove dead feed; add **Submarine Networks** (PWS FCC-specific) + keep SpaceNews/Via Satellite | **S** | Registry update / RSS addition |
| **State PUCs / regional-local** (Gap G7) — the ~700 dead regional feeds in `dead_sources.csv` | #2 | Do **not** re-add individually (mostly dead Gannett/Arc templates). Cover local-impact FCC stories via Google News geo/entity queries instead | **S** | Existing API tuning |
| **Local broadcast TV / public-radio stations** (all dead) | #2 | Rely on GDELT TV (already wired) + trade press (TVNewsCheck/Radio World); station sites not worth per-site collectors | **N/A** | No action (covered indirectly) |

---

## 4. Architecture vs PWS — underserved category scorecard

Rated against the PWS classifications in `pws.py` and the Appendix B list in `fcc_sources.py`:

| PWS category | Coverage | Verdict | Gap driver |
|---|---|---|---|
| **FCC / government primary** | FCC.gov feeds + Federal Register (thin) | 🟡 Adequate-but-thin | FR under-collected; ECFS/Regulations.gov/GovInfo not wired (#3) |
| **Telecom trade** | Fierce×3, RCR, Light Reading, Telecompetitor, Telecoms.com, Total Telecom, Mobile World Live, Inside Towers, Telecompaper | 🟢 **Strong** | Only gap: Comms Daily/TR Daily (paid beat) |
| **Broadcast TV** | TVNewsCheck, TV Technology, RBR, GDELT TV captions | 🟢 Good | B&C + Multichannel ceased publication (not our fault); Variety/THR not fed |
| **Radio** | Radio World, Radio Ink, RBR, Current (public radio) | 🟡 Adequate | **Inside Radio** (PWS-named) has no working feed (#2) |
| **Satellite / space** | SpaceNews, Via Satellite | 🟡 Adequate sources, **weak gate** | Space stories dropped by #4 when they don't name FCC; Submarine Networks missing |
| **Wire services** | *No working direct feed* — AP/Reuters/Bloomberg/DJ all dead | 🔴 **Underserved** | #2 — caught only indirectly via GDELT/NewsAPI/Google News |
| **Financial / business** | WSJ/CNBC feeds; no Bloomberg, no S&P/Kagan, no Dow Jones | 🔴 **Underserved** | #2 — telecom-financial/M&A depth missing |
| **Legal / regulatory-court** | CommLawBlog only | 🔴 **Thin** | #2 — Law360 paid/dead; no appellate/CourtListener |
| **Associations / think tanks** | USTelecom, Benton, Public Knowledge, EFF, Free Press | 🟡 Partial | #2 — CTIA/NAB/NCTA/WISPA feeds dead (G8) |
| **Company newsrooms** | *None working* | 🔴 **Absent** | #2 — all newsroom feeds dead (G4) |
| **Technology press** | Verge, Ars, Wired, TechCrunch, Engadget, CNET, Axios, Techmeme | 🟢 **Strong** | — |
| **Major newspapers** | NYT, WaPo, WSJ, The Hill, NPR (+ Google News) | 🟢 Good | LA Times / USA Today / FT not directly fed |
| **International** | TeleGeography (**dead**) | 🔴 **Broken** | #2 — replace with Submarine Networks + keep space feeds |

**Underserved, in priority order:** (1) Wire services, (2) Company newsrooms, (3) Legal/court, (4) Financial/M&A, (5) International, (6) the *satellite/space gate* (source exists, filter blocks it).

---

## 5. Recommendations (respecting "don't buy APIs unless necessary")

**Nearly every gap is closeable with our existing free architecture** — the dominant root cause is dead feeds (#2) and the registry-not-driving-collection disconnect (#1), not missing paid data.

### Do first — free, high-leverage
1. **Wire the registry into collection** (§3A row 1) — the biggest single truthfulness + coverage win. `M`.
2. **Add Google-News site-scoped queries** for the dead PWS wires (Reuters/AP/Bloomberg) and Inside Radio — recovers "wire service" coverage with zero purchase. `S`.
3. **Fix Federal Register depth + wire ECFS/Regulations.gov** — free gov APIs; closes the #3 primary-source gaps. `S–M`.
4. **Add the entity-gated relevance tier** so FCC-adjacent space/M&A/carrier stories stop dying at gate #4. `M`.
5. **Add CourtListener** (free) for FCC appellate coverage. `M`.
6. **Coverage-integrity alert** (P1-silent-today) + **log cap-hits** so future misses are *visible*. `M`.
7. **Prune dead feeds** (`dead_sources.csv`) and re-discover current newsroom/association feed URLs. `M`.

### Only genuine purchase justification
- **Communications Daily / TR Daily** — this is the one class our free architecture **cannot** reproduce: the daily FCC-docket beat. Licensed *metadata/headline* only. Everything else on the PWS is reachable free. Recommend **evaluating** this after items 1–7 are live and a measured miss-rate justifies it — not before.
- **S&P Global / Kagan (telecom financial)** — do **not** buy yet; test whether entity-gated general + Google News M&A queries close the financial gap first.

### The instrument that makes this measurable (do in parallel)
- **Build the missing miss-detector**: for ~10 business days, capture a reference set (Google News "FCC"/"Federal Communications Commission" + one Talkwalker/Meltwater export) and diff against `/archive`. This turns §2's *structural* causes into a *quantified* per-day miss rate and validates which fixes actually moved the number. Until this exists, "missed stories" cannot be reported from data — only reasoned from architecture (as done here).

---

## 6. Bottom line

- **The pipeline's breadth is real** (~450 feeds, GDELT, NewsAPI, Tavily) and telecom-trade/tech-press coverage is strong.
- **The misses are structural, not volume**: (a) the 194-registry doesn't drive fetching, (b) ~800 configured/known feeds are dead with no silent-death alert, (c) the FCC-relevance gate is too literal for FCC-adjacent space/M&A stories, and (d) a hard 150 cap truncates heavy days silently.
- **~90% of the gap closes with free work** already scoped in the source-research docs (G2/G4/G6/G8/G9). The **only** defensible new purchase is Communications Daily/TR Daily, and only *after* the free fixes and *after* a measured miss rate justifies it.
- **v1.0 has no miss-measurement instrument** — building the reference-diff harness is the prerequisite for ever reporting "missed stories" from data rather than from architecture.

*Analysis is code- and data-grounded (engine.py, pws.py, dead_sources.csv, UAT Baseline). No specific missed-headline list is asserted because no ground-truth capture exists in v1.0 — that gap is itself the top measurement recommendation.*
