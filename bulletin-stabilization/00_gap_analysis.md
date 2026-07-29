# 00 — Gap Analysis

**Phase 0, read-only.**

---

## 1. Gap vs the FCC SOW

**Cannot be produced.** The SOW is not in either repository (see
`00_requirements_matrix.md` — source-document status). Any "gap vs SOW" table I wrote
would be invented. Blocked pending the document.

The closest in-repo proxies — `FCC_Bulletin_Implementation_Specification.md` (self-labelled
*Authoritative*) and `Master_Implementation_Blueprint.md` — are analysed in §5.

---

## 2. Gap vs this brief — the honest summary

The brief's framing is that bulletin is a Claude-driven single-provider system needing
transformation into a multi-provider platform. **The code does not match that framing.**

| Brief's premise | Verified reality |
|---|---|
| "Claude does everything, ~$6.65/run" | `ingest_news` **disabled** (`engine.py:3113`); Claude limited to classify + summarise |
| "Support multiple providers" | **11 collectors already run** per cycle with failure isolation |
| "Perigon (primary)" | Perigon is **3 comment lines**; no client, no key, no dependency |
| "Create `bulletin_sources`" | `bulletin_source_registry` **already exists** in Postgres |
| "Create `bulletin_statistics`" | `bulletin_run_log` already carries 8 metrics + `coverage_json` |
| "Create `bulletin_raw_articles`" | `bulletin_articles` already exists |
| "80 %+ Claude cost reduction" | Most of that reduction **already happened** when `ingest_news` was disabled |

**This is good news** — far less to build than the brief assumes — but it changes what the
phases should do. The real work is **consolidation and instrumentation**, not construction.

### The genuinely missing pieces

| Gap | Severity | Phase |
|---|:--:|---|
| **No cost/token accounting anywhere** | **HIGH** — blocks every cost claim | 5 |
| **No provider abstraction** — 11 collectors inline in `engine.py` | **HIGH** — blocks failover policy, health, per-provider cost | 1 |
| **Boolean queries hardcoded** in 2 modules | **MEDIUM** — editors cannot change queries without a deploy | 3 |
| **No URL liveness / canonical validation** | **MEDIUM** — dead links reach the client bulletin | 4 |
| **No blocking quality gate** | **MEDIUM** — a thin bulletin can publish | 6 |
| **No 24 h source-staleness alert** | **MEDIUM** — silent coverage loss | 2 |
| **No editor move/merge/pin/hide** | **MEDIUM** | 7 |
| **`engine.py` is 3,618 lines** | **HIGH (structural)** — every phase edits the same file | all |
| **Zero Alembic migrations for bulletin** | **HIGH (ops)** — schema created by `CREATE TABLE IF NOT EXISTS` at boot | 1–6 |
| **`engine.py.backup` (62 KB) in the tree** | LOW | cleanup |

---

## 3. Table-name collisions — must resolve before Phase 1

The brief says *"Do NOT modify existing bulletin tables"* **and** proposes tables that
duplicate them. Both cannot hold.

| Proposed | Existing | Overlap | Recommendation |
|---|---|---|---|
| `bulletin_sources` | **`bulletin_source_registry`** | ~60 % | **Extend** the existing table (additive nullable columns). Two source tables would split the truth. |
| `bulletin_statistics` | **`bulletin_run_log`** | ~70 % | **Extend** `bulletin_run_log`; it already has `coverage_json`. |
| `bulletin_raw_articles` | **`bulletin_articles`** | high | **Reuse**; add a `stage` column if pre/post-filter separation is genuinely needed. |
| `bulletin_providers` | — | none | **Create** |
| `bulletin_search_profiles` | — | none | **Create** |
| `bulletin_cost_logs` | — | none | **Create** |
| `bulletin_generation_runs` | **`bulletin_run_log`** | high | **Reuse** |
| `bulletin_provider_logs` | **`bulletin_source_outcome`** | moderate | Decide: extend or create |

**Net: 3 new tables, not 8.** Creating all 8 would leave the module with two source
registries and two run logs — exactly the "dual stack" problem the wider assessment already
flags as a maintainability defect.

---

## 4. The cost baseline problem — the most important finding

**The "$6.65/run → $0.50/run, 80 % reduction" target cannot currently be evidenced.**

- No cost instrumentation exists (`grep cost_usd|tokens_in|usage.` → **zero hits**).
- The expensive path (`ingest_news` Claude `web_search` over every topic query) is
  **already disabled**.
- Live Claude usage is Haiku classification (≤75 calls/run at the 600 cap) plus Haiku
  summarisation — an order-of-magnitude estimate of **$0.10–$0.40/run**.

So the reduction may already be ~90 %, achieved before this engagement. **Phase 5 must be
resequenced to run FIRST**: instrument, measure a real baseline for one week, and only then
state a target. Otherwise we would claim credit for a pre-existing improvement and set a
target ($0.50) that is plausibly *higher* than today's actual spend.

---

## 5. Gap vs the in-repo authoritative spec

Two live discrepancies between the documents and the running system:

1. **Delivery time.** Blueprint §4 describes an *"8 AM ET lifecycle"*; the scheduler runs
   **1 AM ET** (`scheduler.py`). One is wrong.
2. **Coverage assurance.** Spec §8 marks it *"HONEST — binding"*; the endpoint exists but
   nothing blocks publication on failure.

The spec also carries requirements absent from your brief — Section 508 / WCAG 2.1 AA
(§10), security (§11), performance (§12). If the spec is the contract of record, those are
in scope and this brief's 10 phases do not cover them.

---

## 6. What must not be touched (verified integration points)

`app/tefca_registry/`, `app/platform_config/`, auth/user management, `app/core/email.py`,
`app/api/admin_users.py`, `app/Tefca/qa_monitor.py`, `app/main.py` beyond its 2 existing
`safe_load` lines, and the scheduler's timing/weekend semantics.

**Deployment caution specific to this module:** `bulletin_store.py` runs
`CREATE TABLE IF NOT EXISTS` at startup. Any new table added the same way ships silently
with no migration and no rollback path. Recommend Alembic for all new bulletin tables even
though the module has never used it — noted as a deliberate departure from local
convention, for reversibility.
