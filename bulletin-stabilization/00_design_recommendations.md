# 00 — Design Recommendations

**Phase 0, read-only.** Recommendations only; nothing implemented.

---

## 1. Headline recommendation: resequence the phases

The brief orders phases 1 → 10. Based on what the code actually is, that order builds
instrumentation last and would let us report a cost reduction we cannot evidence.

| Order | Phase | Why here |
|--:|---|---|
| **1st** | **Phase 5 — cost tracking** | Nothing measures cost today. Every other phase's ROI claim depends on this. Cheap, additive, zero behaviour change. |
| **2nd** | **Phase 1 — provider abstraction** | 11 collectors already exist; wrapping them behind one interface is refactor-with-tests, and it unlocks per-provider cost, health, and failover policy. |
| 3rd | **Phase 2 — source registry** | Mostly loading a 122-row CSV into an existing table. |
| 4th | **Phase 3 — Boolean profiles** | Needs the `NOT` parser fix; benefits from provider abstraction for dialect translation. |
| 5th | **Phase 6 — quality gate** | Needs Phase 2 source data to judge diversity. |
| 6th | **Phase 4 — validation/clustering** | Clustering already works; this is URL liveness + canonicalisation. |
| 7th | **Phase 7 — editor review** | UI-coupled; needs frontend work not scoped here. |
| 8th | **Phase 8 — format/exports** | **Blocked** on the FCC sample. |
| 9th | **Phase 9 — health + manual run** | Trivial once Phase 1 exists. |
| 10th | **Phase 10 — runbook** | Last, documenting what was built. |

---

## 2. Per-phase recommendations

### Phase 5 — Cost tracking (DO FIRST)
Create `bulletin_cost_logs`. Wrap the **two live Claude call sites** (`classify_articles`
`engine.py:1929`, `_summaries_for` `engine.py:2397`) in a `cost_tracker` that reads
`response.usage` and writes tokens in/out + computed USD. Add `/costs` and
`/costs/{run_id}`.

**Do not state a reduction target yet.** Run one week, publish the real baseline, then set
the target. My estimate is $0.10–$0.40/run today — likely already ~90 % below the quoted
$6.65 because `ingest_news` was disabled before this engagement. Claiming that as our
saving would be dishonest.

*Rollback:* drop the table, remove the wrapper. No behaviour change.

### Phase 1 — Provider architecture
Extract the 11 existing collectors into `providers/` behind `base_provider.py`. **Strangler
pattern:** `provider_manager` initially delegates to the existing functions unchanged; move
logic in one provider at a time, each with the run producing byte-identical article counts
before/after.

Implement the 8 interface methods, but be honest that several are **stubs for most
providers** — GDELT and RSS have no rate-limit header, no per-query cost, no incremental
sync. Return `None`/`NotImplemented` rather than inventing values.

Create `bulletin_providers`. **Perigon: build the adapter, leave it disabled** (`enabled=0`)
until a key exists in Key Vault — do not make it "primary" while it is 3 comment lines.

*Rollback:* feature-flag `BULLETIN_USE_PROVIDER_MANAGER`; default off until parity proven.

### Phase 2 — Source registry
**Extend `bulletin_source_registry`**, do not create `bulletin_sources` (see
`00_gap_analysis.md` §3). Additive nullable columns: `domain, country, state, language,
media_type, category, reliability_score, first_seen, last_seen, article_count,
health_status, created_at, updated_at`.

Seed from `Master_Source_Catalog.csv` (122 scored rows) — an idempotent loader, not
hand-typed lists. Retire `fcc_sources.py`'s Python lists **only after** the table is
authoritative, keeping the helper function signatures so callers don't change.

Staleness alert: a nightly check on `last_seen` older than 24 h for `enabled=1, priority>=X`
sources, reusing the existing SendGrid alert path in `scheduler.py`.

### Phase 3 — Boolean profiles
Sequence from `00_boolean_review.md` §7: migrate the 9 topics verbatim → add `NOT` to
`_boolean_matches` (test-first) → unify the three keyword systems → rename the duplicate
`is_fcc_relevant`.

**`NOT` is a parser change, and the parser is the filtering hot path.** It gets its own
commit and its own tests.

### Phase 4 — Collection pipeline
Already largely built. Real additions: HTTP liveness (HEAD with GET fallback, concurrency
capped, cached per URL per run), canonical-URL resolution, explicit language/country
assertion, and wire-lineage detection using `duplicate_risk` + publisher from the catalogue.

**Cost note:** liveness-checking every article adds latency, not Claude spend. Cap
concurrency and short-circuit on already-seen URLs.

### Phase 6 — Quality gate
Thresholds in config, not code. Make the gate **advisory first** (log + report), flip to
blocking only after a week of data shows the thresholds don't false-positive. A gate that
blocks the 8 AM bulletin on day one is an availability risk.

Extend `bulletin_run_log` rather than creating `bulletin_statistics`.

### Phase 7 — Editor review
Backend endpoints are cheap; the value is in the UI, which is **not in scope here** and
lives in a separate repo. Recommend splitting: backend actions (move/merge/pin/hide) this
sprint, UI as a tracked frontend task.

### Phase 8 — Format & exports
**Blocked** — see `00_sample_bulletin_analysis.md`. Interim: golden-file snapshots of
current HTML/DOCX/PDF and a no-regression test. Reword the success criterion from *"match
or exceed the FCC sample"* to something checkable until the sample exists.

### Phase 9 — Provider health & manual run
Falls out of Phase 1 almost free. Extend the existing `POST /run/{agency_id}` body with
optional `providers[]` / `profiles[]` — **additive, defaults preserve today's behaviour**.
Don't add `/generate` as a new endpoint when `/run` already exists.

### Phase 10 — Runbook
Write last. Fold in the operational gotchas found in Phase 0: the rate limiter (10 req /
5 s) that makes verification look broken, `ENABLE_SCHEDULER` differing prod/dev, and the
`CREATE TABLE IF NOT EXISTS`-at-boot pattern.

---

## 3. Cross-cutting recommendations

### 3.1 Do not create 8 tables — create 3
`bulletin_providers`, `bulletin_search_profiles`, `bulletin_cost_logs`. Extend
`bulletin_source_registry` and `bulletin_run_log`. Reuse `bulletin_articles`. Creating
duplicates would give the module two source registries and two run logs.

### 3.2 Use Alembic for new bulletin tables
The module has **zero migrations** — schema is created by `CREATE TABLE IF NOT EXISTS` at
startup. That has no rollback and no review. New tables should ship as Alembic revisions
even though it departs from local convention. Flagging explicitly because it is a
deliberate inconsistency.

### 3.3 `engine.py` at 3,618 lines is the main execution risk
Every phase edits it. Recommend the strangler approach throughout: new code in the new
sub-packages, `engine.py` functions become thin delegates, and each move is verified by
identical article counts on a real run. **Do not attempt a big-bang split.**

### 3.4 Protect the only tests you have
`test_bulletin_enhancements.py` (17 tests) is the entire backend test suite. Every phase
should extend it. Note `pytest` is **not installed** — the file runs standalone. Installing
pytest is a small, high-leverage first step.

### 3.5 Delete `engine.py.backup`
62 KB stale duplicate of the module's core file, untracked. It will eventually be edited by
mistake.

---

## 4. Recommended immediate next step

**Phase 5 (cost instrumentation) as a single small PR**, because it is additive, reversible,
touches two functions, and produces the measurement every subsequent claim depends on.

Before Phase 1 begins, I need decisions on:

1. **FCC SOW / sample bulletin / Appendices A & B** — supply, or confirm the in-repo specs
   are the contract of record.
2. **Table strategy** — confirm extend-don't-duplicate (§3.1).
3. **The $6.65 baseline** — confirm we measure before claiming reduction.
4. **8 AM vs 1 AM ET delivery** — the Blueprint and the scheduler disagree.
5. **Perigon** — is there a funded account? It cannot be "primary" while unimplemented.
6. **Section 508 / WCAG, security, performance** (spec §§10-12) — in or out of scope?
