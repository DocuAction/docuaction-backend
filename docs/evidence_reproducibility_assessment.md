# EVIDENCE REPRODUCIBILITY ASSESSMENT

**Date:** 2026-08-22
**Scope:** every source consulted by D1–D6, measured against the 1,984 persisted
evidence rows produced for the 43 verified entities.
**Method:** direct query of `tefca_dimension_evidence`, `review_records`,
`rce_source_intakes`, `tefca_ppef_snapshots` and `tefca_source_cache`. No
estimates.

---

## 1. PER-SOURCE ASSESSMENT

Percentages are the share of that source's persisted evidence rows carrying a
non-empty value.

| SOURCE | LOOKUP METHOD | LIVE / SNAPSHOT | SOURCE VERSION | SOURCE DATE | RAW RESPONSE | HASH | IDS SEARCHED | MATCH RESULT | QUERY TS | **REPRODUCIBLE IN 6 MONTHS** |
|---|---|---|---|---|---|---|---|---|---|---|
| **RCE** | local file, Area 1 | **SNAPSHOT** | YES — `field_map_version 1.0.0` + `schema_fingerprint` | YES — `received_at` | **YES** — `raw_line`, all 23,566 lines verbatim | **YES** — file SHA-256 + per-record SHA-256; **23,566 re-verified, 0 mismatches** | n/a | YES | YES | **YES** |
| **NPPES** | live REST, per entity | LIVE | ⚠ 50% — and the value is `"2.1"`, the **API version**, not a data version | **NO** (0%) | PARTIAL — 6 shaped fields, 100% | **NO** | 27% | 1% | 100% | **NO** |
| **CMS PPEF Enrollment** | live Data API, paged | LIVE | YES — dataset UUID `2457ea29-…7515`, 100% | 70% — `http_last_modified`, labelled transport metadata | YES (100%) | **NO** | 100% | 35% | 100% | **PARTIAL** |
| **PPEF Practice Location** | local snapshot store | SNAPSHOT — **0 ingested** | 0% | 0% | only the UNAVAILABLE record | **NO** | 0% | 0% | 0% | **N/A — never consulted** |
| **PPEF Reassignment** | local snapshot store | SNAPSHOT — **0 ingested** | 0% | 0% | 72% | **NO** | 0% | 0% | 0% | **N/A — never consulted** |
| **PPEF Secondary Specialty** | local snapshot store | SNAPSHOT — **0 ingested** | — | — | — | — | — | — | — | **N/A — 0 evidence rows; consumed by no dimension** |
| **PPEF Additional NPIs** | local snapshot store | SNAPSHOT — **0 ingested** | — | — | — | — | — | — | — | **N/A — 0 evidence rows; used transiently inside `_npi_alignment`, never persisted** |
| **CMS Revoked** | live Data API | LIVE | YES — dataset UUID `a6496a7d-…07c3`, **53%** | 53% | YES (100%) | **NO** | 53% | 0% | 53% | **PARTIAL** |
| **OIG LEIE** | full CSV download → in-process dict | LIVE, per worker | ⚠ 100% — but the value is the literal string `"CSV-UPDATED"` | **NO** (0%) | YES (100%) | **NO** | 100% | 0% | 100% | **NO** |
| **SAM.gov** | live REST | LIVE — **never reached** | 0% | 0% | 0% | **NO** | 0% | 0% | **0%** | **NO** |
| **USPS** | — | — | — | — | — | — | — | — | — | **NEVER QUERIED — 0 rows.** Declared in `ADDRESS_HIERARCHY` (`address_evidence.py:38,45`); no candidate is ever appended in `_dimension_address` |

### Structural gaps confirmed against the live schema

- **`tefca_dimension_evidence` has no hash column of any kind.** `original_values`
  is a *shaped projection*, not the raw response.
- **`tefca_source_cache`** carries `response_data` **and** `response_hash` — and
  holds **0 rows**. The evidence path does not use it.
- **`tefca_verification_checks`** carries `evidence_hash` **and** `response_data` —
  177 rows, but those come from the *registry verification* path, not from D1–D6.
- **`tefca_ppef_snapshots`** carries `sha256` of the exact CMS bytes — **0 rows**.

The mechanisms for hashing and preserving responses exist in three places in this
codebase. The D1–D6 path uses none of them.

---

## 2. THE SIX-MONTH QUESTION

> *"Why was Entity X classified B2 on August 22, 2026?"*

Worked against **REV-2026-000001 — UTMB - Health, B2, RULE-003 v2**.

| # | Link | Reconstructible? | Evidence |
|---|---|---|---|
| **1** | RCE assertion at that time | **YES — completely** | `rce_source_records.raw_line` for the exact delivered line; per-record SHA-256; intake SHA-256 `689472…9e8d`; the preserved file re-hashed and matched today; `field_map_version 1.0.0`; `schema_fingerprint 1cd655e9…` |
| **2** | External evidence available at that time | **PARTIAL** | Shaped values are in `original_values`, so *what each source said* is recoverable. Raw responses are stored nowhere. Four of eleven sources never answered |
| **3** | **Source versions used** | **NO** | NPPES: API version only. LEIE: a literal string. PPEF Enrollment / Revocation: dataset UUID against a source CMS publishes as current-only. SAM: nothing. USPS: never queried |
| **4** | D1–D6 outputs | **YES** | `tefca_dimension_evidence`, append-only, `generation_timestamp`; plus the frozen snapshot in `review_records.verification_results` |
| **5** | B1–B4 rule version | **YES** | `classification_rule = RULE-003`, `classification_rule_version = 2`; `review_rules` retains v1 (retired 2026-08-21) alongside v2; `classification_rationale` names the matched conditions |
| **6** | **Analyst determination** | **NO — none was made** | `reviewer_resolution` is NULL on **43/43** |
| **7** | **QA approval** | **NO — no mechanism exists** | `review_records` has no QA columns; `qa_approved_by/_at` exist only on `rce_issues` |
| **8** | Report delivered | **YES for the artefact, PARTIAL for the derivation** | `review_reports.report_html` stored verbatim (320 KB, self-contained); snapshot carries `template_version 1.0.0`, `b1_b4_rule_version 2`, `evidence_generation`, `data_payload_hash bfe40299…`. But `rce_source_file_sha256 = "cafe"` — a placeholder — and `review_cycle_id = NULL` on all five reports |

### Where the chain breaks

```
1 RCE assertion         ####################  SOLID  — hash-verified, re-checkable today
2 External evidence     ##########..........  shaped values kept; raw responses and hashes absent
3 Source versions       ###.................  <-- FIRST BREAK   NPPES and LEIE carry no version at all
4 D1-D6 outputs         ####################  SOLID  — append-only, generation-stamped
5 B1-B4 rule version    ####################  SOLID  — versioned rules, retired rows retained
6 Analyst determination ....................  <-- SECOND BREAK  0/43 resolved
7 QA approval           ....................  <-- THIRD BREAK   no mechanism exists
8 Report                ################....  artefact stored; source-file hash is a placeholder
```

**Break 1 is the one that cannot be repaired retrospectively.** Asked in February
2027 why UTMB was B2, DocuAction can produce the exact ONC line, the exact NPPES
values it compared, the D1–D6 dispositions and the rule that fired — but it
**cannot state which NPPES edition or which LEIE edition it consulted**. The
determination's inputs are describable but not verifiable.

**Breaks 2 and 3 are repairable going forward** and mean that today no
determination has actually been *made* by a human. All 43 are system
recommendations that no reviewer has confirmed and no QA has approved.

### Why CMS's publication model makes this urgent

CMS states that PPEF carries **current enrollment information and no historical
enrollment information**. The dataset UUID pins the *publication*, not a preserved
copy: once CMS publishes the next quarter, the rows behind a determination are
gone from the source. The same is true of NPPES and of OIG LEIE's `UPDATED.csv`,
neither of which publishes a version at all.

The ingestion machinery that would solve this **already exists and has never been
run**: `tefca_ppef_snapshots` records file name, resource version, SHA-256, schema
fields and record count, with a durable job table, a heartbeat and a reaper, and
80 tests. It holds 0 rows.

---

## 3. MINIMUM ADDITIONAL PROVENANCE BEFORE INTELLIGENT INGESTION

Seven items, ordered by how much of the break each closes. **None requires the
observation store.** Each strengthens what is already being written.

| # | Requirement | Why it is the minimum | Effort |
|---|---|---|---|
| **1** | **`evidence_hash` column on `tefca_dimension_evidence`** — SHA-256 of the canonicalised raw response, computed before shaping | Without it, "the source said X" is an assertion rather than a verifiable fact. `SourceResult.hash_payload()` already exists (`connectors.py:167`) and `tefca_verification_checks.evidence_hash` already proves the pattern in this codebase. **Highest value per line changed.** | 1 column, 1 call site |
| **2** | **`source_version_snapshots` table** — `(source, version_label, as_of, sha256, row_count, retrieved_at, storage_uri)`, referenced by every evidence row | Closes Break 1 for LEIE immediately — the downloaded bytes are already in memory at `connectors.py:368` and are discarded unhashed — and for NPPES once bulk loading exists. `tefca_ppef_snapshots` is the proven model to copy | 1 table + FK |
| **3** | **Provenance on the UNAVAILABLE branch** — `query_timestamp`, the identifier that *would* have been used, `rule_applied` | All 172 SAM rows carry 0% provenance. "We attempted at 22:14:35 with UEI=X and got no route" is materially different from "SAM is unavailable" | ~6 lines, `evidence_assembly.py:580-584` |
| **4** | **LEIE edition capture** — replace `dataset_version_anchor = "CSV-UPDATED"` with the file's SHA-256, byte length, row count and `Last-Modified`; move the cache out of the per-process dict | Today the OIG exclusion check — a federal control — cannot name the list it screened against. The cache is also per-worker and lost on restart | ~40 LOC |
| **5** | **`correlation_id` threaded intake → quality → curate → promote → verify → report** | The column already exists on `audit_logs` (migration `20260817`, backfilled) and is written only by the auth routes. Without it the eight links above are joined by inference rather than by key | 1 column per pipeline table |
| **6** | **Ingest PPEF, or state its absence in the determination** | Four of eleven sources have never been consulted. A determination silent about that reads as "checked and found nothing" | operational, not code |
| **7** | **Fix `rce_source_file_sha256 = "cafe"`** and require a non-null `review_cycle_id` | The one field tying a delivered report to the ONC bytes it describes is a test literal in all five stored reports | 2 lines + 1 validation |

### Sequencing note

Items 1–4 are what make a future observation store *worth building*. An
observation store that records shaped values without a response hash or a source
version reproduces today's gap at greater scale — it would hold far more
observations, each equally unverifiable.

**Recommendation: items 1, 3, 4 and 7 are small, independent, and should land
before any enrichment work begins.** Item 2 is the structural one and should
follow immediately. Items 5 and 6 are larger and can be sequenced with the
observation store itself.
