# 00 — Boolean Search & Source Review

**Phase 0, read-only.**

---

## 1. There are THREE competing keyword systems, not one

This is the central finding. Boolean/keyword logic is spread across three modules that
disagree, and a fourth deterministic scorer:

| # | Module | Structure | Used for | Sections |
|--:|---|---|---|--:|
| 1 | **`fcc_boolean_search.py`** | `FCC_SEARCH_TOPICS` — 9 topics, each `{label, boolean}` with real Boolean syntax | Query generation for search providers | 9 |
| 2 | **`boolean_filter.py`** | `SECTION_KEYWORDS` — flat keyword lists, no operators | Post-hoc section assignment | 9 |
| 3 | **`engine.py:2082-2164`** | `get_category()` / FCC-org classifier | Category assignment | ~n |
| 4 | **`engine.py:757-833`** | `fcc_relevance_points()` / `fcc_category()` | Deterministic relevance scoring + boost | — |

Plus `fcc_keywords_extended.py` and `editorial_rules.py` (client editorial overrides).

**A topic can be defined one way for retrieval (1) and classified another way for display
(2/3).** That is a silent source of miscategorised articles, and it means "change the FCC
Daily query" currently requires edits in up to three files.

---

## 2. `fcc_boolean_search.py` — the query bank

9 topics: `FCC_NEWS` (label *General*), `CONSUMERS`, `MEDIA_BROADCASTING`, `SPACE_POLICY`,
`PUBLIC_SAFETY_CYBER`, `WIRELESS_SPECTRUM`, and three more.

Query shape is genuine Boolean with field qualifiers:

```
(FCC OR "Federal Communications Commission") AND (...)
title:TCPA OR title:robocalls OR title:robocall
```

**Evaluator** — `_boolean_matches` (`engine.py:2019`), a hand-written recursive-descent
parser supporting:

| Operator | Supported |
|---|:--:|
| `AND` | ✅ |
| `OR` | ✅ |
| Parentheses | ✅ |
| Quoted phrases | ✅ |
| `title:` field qualifier | ✅ |
| **`NOT` / `-` exclusion** | ❌ **NOT SUPPORTED** |

**`NOT` is a stated Phase 3 requirement and the parser cannot express it.** This is the
single most consequential gap in the Boolean layer: without negation you cannot write
*"spectrum AND NOT sports-spectrum"*, which is exactly the class of false positive the
US/FCC-focus requirement needs. Adding `NOT` means extending the parser **and** re-testing
all 9 topics.

Note the parser matches against **already-collected article text** — it is a *filter*, not
a provider query. Where a provider supports server-side Boolean (Perigon does), the same
string would need translating to that provider's dialect. No such translator exists.

---

## 3. `boolean_filter.py` — section assignment

14 dense lines, no operators — flat keyword membership with a hardcoded precedence list:

```python
for s in ["space_policy","public_safety","ai_ml","international","consumers",
          "media_broadcasting","wireless_spectrum","business_tech","fcc_news"]:
```

Consequence: an article mentioning both *satellite* and *spectrum* is always
`space_policy`, never `wireless_spectrum`. Deterministic and fast, but the precedence is
invisible to editors and untunable without a code change.

`is_fcc_relevant()` here is a thin wrapper — `assign_section(...)[0] != "other"` — and is
**not** the relevance gate actually used in the pipeline. That is `_is_fcc_relevant_v2`
(`engine.py:589`), a 3-tier filter. Two functions with near-identical names and different
behaviour is a live trap for the next maintainer.

---

## 4. `fcc_sources.py` — source tiering

187 lines of Python lists: `MAJOR_DAILIES`, `OTHER_DAILIES`, `WIRES`, `TRADES`,
`TECH_BLOGS`, `FCC_SPECIFIC`, `SUBSCRIPTION_SOURCES`, combined into `ALL_SOURCES`, with
helpers `is_priority_source`, `is_subscription_source`, `get_source_tier`.

Complemented by `fcc_feeds_extended.py` (360 lines of RSS feeds) and — separately — the
research CSVs.

### The research catalogue is far richer than the code

`docs/fcc-source-research/Master_Source_Catalog.csv` — **122 sources**, already scored on
exactly the fields Phase 2 asks for:

```
publication_name, website, rss_feed, official_api, coverage_type, coverage_area,
editorial_focus, publisher, content_frequency, fcc_relevance, subscription_required,
free_or_paid, reliability_score, authority_score, primary_topics, duplicate_risk,
search_capability, notes
```

Sample: `Reuters … reliability_score 10, authority_score 10, duplicate_risk High, "Wire; often syndicated"`.

Plus `Government_Source_Catalog.csv` (27), `Telecom_Source_Catalog.csv` (36),
`Company_Newsroom_Catalog.csv` (24), `Press_Release_Source_Catalog.csv` (8) and a
`Source_Priority_Matrix.md`.

**Phase 2 is largely a data-loading exercise, not a research exercise.** The `duplicate_risk`
and wire-service annotations directly serve the Phase 4 republished-story requirement.

---

## 5. What works well — preserve

- Real Boolean grammar with phrases and `title:` qualifiers (rare and valuable).
- Deterministic, testable evaluation — no LLM in the filtering path.
- 3-tier relevance (`_is_fcc_relevant_v2`) plus a points-based booster.
- Domain exclusion (`_is_excluded_domain`) — the client's techdirt.com rule is honoured.
- Google-News URL unwrapping (`_resolve_google_news_url`) before dedup.
- 17 unit tests covering boolean sections, scoring, clustering, quality gates — the only
  test coverage in the backend, and it passes.

## 6. What needs improvement

| Issue | Severity | Phase |
|---|:--:|---|
| **`NOT` unsupported** by the evaluator | **HIGH** | 3 |
| Three competing keyword systems | **HIGH** | 3 |
| Queries hardcoded across 2–3 files | HIGH | 3 |
| No provider-dialect translation (Perigon server-side Boolean) | MEDIUM | 1+3 |
| Section precedence hardcoded and invisible | MEDIUM | 3 |
| `boolean_filter.is_fcc_relevant` vs `engine._is_fcc_relevant_v2` name collision | MEDIUM | 3 |
| 122-source catalogue not loaded into `bulletin_source_registry` | MEDIUM | 2 |
| `fcc_sources.py` lists vs research CSV — two source truths | MEDIUM | 2 |

---

## 7. Recommendation for Phase 3

1. **Migrate the 9 `FCC_SEARCH_TOPICS` into `bulletin_search_profiles` verbatim first** —
   same strings, DB-backed, with the code falling back to the hardcoded dict if the table
   is empty. Zero behaviour change, fully reversible, and it makes every later change a
   data edit.
2. **Add `NOT` to `_boolean_matches`** as a separate, test-first change, re-running the 17
   existing tests plus new negation cases.
3. **Then** unify sections: make `boolean_filter.SECTION_KEYWORDS` derive from the profile
   table so retrieval and display agree.
4. Rename one of the two `is_fcc_relevant` functions.

Steps 1 and 2 are independently shippable and independently revertible.
