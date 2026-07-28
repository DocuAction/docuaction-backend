# 02 — The Three Keyword Systems (documented, deliberately not merged)

**Phase 2 deliverable.** Per the agreed scope: document the conflict, make profiles
database-driven, **do not merge the systems yet**.

---

## 1. The conflict

Four independent pieces of logic decide "is this article relevant, and what is it
about". They can disagree about the same article.

| # | Module | Structure | Governs | Operators | Editable without deploy? |
|--:|---|---|---|---|:--:|
| **1** | `profiles/boolean_profiles.py` → `fcc_boolean_search.FCC_SEARCH_TOPICS` | 9 topics, real Boolean strings | **Section matching** (`_boolean_section`) | AND, OR, `NOT`, parens, quoted phrases, `title:` | **YES** (as of Phase 2) |
| **2** | `boolean_filter.py` → `SECTION_KEYWORDS` | 9 flat keyword lists, no operators | `assign_section()`, `boolean_filter.is_fcc_relevant()` | none | no |
| **3** | `engine.get_category()` (`engine.py:2142`) | FCC-org classifier | Category assignment | none | no |
| **4** | `engine.fcc_relevance_points()` / `fcc_category()` (`engine.py:757`) | Deterministic point scoring | Relevance boost + category | none | no |

Plus `fcc_keywords_extended.py` and `editorial_rules.py` layered on top.

### Which one actually wins

For the **section shown in the bulletin**, system **1** wins — `_boolean_section()`
is what `_section_of()` consults, evaluated in `_BOOL_MATCH_ORDER`:

```
INTERNATIONAL · SPACE_POLICY · AI_MACHINE_LEARNING · PUBLIC_SAFETY_CYBER
CONSUMERS · MEDIA_BROADCASTING · BUSINESS_TECH · WIRELESS_SPECTRUM · FCC_NEWS
```

Specific topics are checked before broad catch-alls, so an undersea-cable story is
`INTERNATIONAL` rather than being swallowed by the broad `FCC AND telecom` in
`FCC_NEWS`.

For **whether an article survives at all**, system **4** plus `_is_fcc_relevant_v2()`
governs — *not* `boolean_filter.is_fcc_relevant()`.

### The name collision — a live trap

```
boolean_filter.is_fcc_relevant()   # thin wrapper: assign_section(...)[0] != "other"
engine._is_fcc_relevant_v2()       # the 3-tier filter the pipeline ACTUALLY uses
```

Two near-identically named functions with different behaviour. A maintainer editing
the first to change relevance would see **no effect on the bulletin**. Renaming is
recommended but was **not** done in Phase 2 — it touches call sites in
`youtube_ingest.py` and the test file.

### Why this matters in practice

A topic can be defined one way for **retrieval/section matching** (system 1) and
classified another way for **display category** (systems 3/4). Changing "what counts
as Spectrum" today means editing up to three files, and getting it wrong in one place
produces silently miscategorised articles.

---

## 2. What Phase 2 changed

**System 1 only.** Its 9 queries now live in `bulletin_search_profiles` and are
editable through the API. Systems 2, 3, and 4 are untouched.

Behaviour is unchanged by default:

- `PROFILES` is seeded at import from `FCC_SEARCH_TOPICS`.
- DB reads are gated behind `BULLETIN_PROFILES_DB_ENABLED` (**off**).
- With the flag on and the table seeded, verified **9/9 keys, all queries byte-identical**,
  and section assignment matched on every sample.

### Pre-existing gap found while seeding

**`AI_MACHINE_LEARNING` has an empty Boolean query** (`boolean_len = 0`). It can
therefore never match, which makes the "AI & Telecom" topic effectively dead in
section matching — despite appearing in the brief's list of FCC profiles.

An early version of `refresh_from_db` filtered empty queries out, silently reducing
9 profiles to 8. That was corrected: empty rows are preserved so the DB path is at
exact parity with the fallback, and the slot stays visible for an operator to fill.

**Recommendation:** write a query for `AI_MACHINE_LEARNING` (now a DB edit, not a
deploy). Left unwritten deliberately — inventing the client's AI/telecom search terms
is an editorial decision, not an engineering one.

---

## 3. `NOT` operator (Phase 2, step 2)

`_boolean_matches()` gained negation. Grammar, precedence **NOT > AND > OR**:

```
or   := and (OR and)*
and  := not_ (AND not_)*
not_ := (NOT | !) not_ | atom
atom := "(" or ")" | token
```

So `a AND NOT b` parses as `a AND (NOT b)` — what an editor writing
`spectrum AND NOT sports` expects. `NOT` is right-associative, so `NOT NOT x` folds
back to `x`.

**22/22 parser tests pass**, including the motivating case:

| Expression | "…spectrum auction for 5g wireless" | "…college sports spectrum broadcast…" |
|---|:--:|:--:|
| `spectrum AND NOT sports` | **True** (kept) | **False** (excluded) |

**Backward compatibility verified, not assumed:** all 9 production queries were
tokenised and checked for a bare `NOT`/`!` — **zero hits**, so no existing query
changes meaning. Section matching on a real sample set is unchanged.

One behavioural note worth recording: a bare unquoted `not` in a query is now an
operator. To match the literal word, quote it — `"not"`.

---

## 4. Recommended merge sequence (future phase — NOT done)

1. Rename `boolean_filter.is_fcc_relevant` → `assign_section_relevance` to end the
   collision (touches `youtube_ingest.py`, `test_bulletin_enhancements.py`).
2. Derive `SECTION_KEYWORDS` (system 2) from the profile table so retrieval and
   display cannot diverge.
3. Fold `get_category()` (system 3) into the profile table as a second query column.
4. Keep `fcc_relevance_points()` (system 4) separate — it is a *scorer*, not a
   classifier, and merging it would conflate two different jobs.
5. Move `_BOOL_MATCH_ORDER` into the table's `priority` column (currently metadata
   only — engine precedence still governs).

Each step is independently shippable and independently revertible. None should be
attempted without first having cost and coverage measurement in place to detect a
regression in what the bulletin actually publishes.
