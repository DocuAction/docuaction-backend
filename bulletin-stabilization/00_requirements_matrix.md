# 00 — Requirements Matrix

**Phase 0, read-only.**

---

## ⚠️ Source-document status — read first

The brief says to treat the **FCC Statement of Work**, **FCC Sample Daily News Summary**,
and **FCC Boolean Search document** as the functional requirements. I searched for all
three and **none is present**:

| Expected input | Result |
|---|---|
| FCC Statement of Work | **NOT FOUND** — no file matching `*statement*of*work*`, `*SOW*`, or `*scope*of*work*` in either repo |
| FCC Sample Daily News Summary | **NOT FOUND** — no sample bulletin artifact on disk |
| FCC Boolean Search document | **NOT FOUND** as a document (the *code* `fcc_boolean_search.py` exists) |
| `/mnt/user-data/uploads/` | **does not exist** in this environment |

**Consequence: the column "FCC Requirement" below cannot be sourced from the SOW.** I will
not fabricate contract requirements — inventing an FCC deliverable list and then reporting
compliance against it would be worse than having no matrix.

What I have done instead: built the matrix from the **two authoritative in-repo
specifications** that already encode client requirements, plus the requirements stated in
your brief. Where a row derives from a document that references "Appendix A/B", that
appendix itself is not in the repo — flagged inline.

**Authoritative substitutes used:**
- `docs/fcc-bulletin-review/FCC_Bulletin_Implementation_Specification.md` (325 lines, marked *"Authoritative"*)
- `docs/fcc-bulletin-review/Master_Implementation_Blueprint.md` (301 lines)
- `docs/fcc-source-research/Master_Source_Catalog.csv` (122 sources, scored)
- In-code client-spec markers: `engine.py` *"the 6 buckets in the AGT FCC Daily News email"*, *"the client's Appendix A spec"*, *"Appendix B Sources"*

**Action required from you:** supply the SOW, sample bulletin, and Boolean document, or
confirm the in-repo specs are the contract of record. Everything downstream — especially
Phase 8 "match or exceed FCC sample quality" — is unverifiable without the sample.

---

## A. Requirements derived from your brief

| # | Requirement | Currently met? | How / where | Gap |
|--:|---|:--:|---|---|
| A1 | Multi-provider collection | **YES (ad-hoc)** | 11 collectors in `run_daily_cycle` (`engine.py:3111-3149`) | No common interface; provider logic inline in `engine.py` |
| A2 | Provider failover, never stop on one failure | **YES** | `asyncio.gather(..., return_exceptions=True)`; per-collector try/except | Failures logged, not persisted per-provider with retry count |
| A3 | Provider config in DB | **NO** | Providers are `if KEY:` branches on env vars | `bulletin_providers` table absent |
| A4 | Source registry | **PARTIAL** | `bulletin_source_registry` table **exists** (9 cols); `fcc_sources.py` tiering; 122-source research CSV | Table lacks country/state/language/media_type/reliability_score/health_status; CSV not loaded into it |
| A5 | Stale-source warning (24 h) | **NO** | `bulletin_source_outcome` records per-run outcomes | No 24 h staleness rule or alert |
| A6 | Boolean profiles in DB, editable via API | **NO** | Hardcoded: `FCC_SEARCH_TOPICS` (`fcc_boolean_search.py:5`), `SECTION_KEYWORDS` (`boolean_filter.py:4`) | `bulletin_search_profiles` table + CRUD absent |
| A7 | Collect first, filter later — Claude never discovers | **YES** | `ingest_news` disabled `engine.py:3113`; Claude only classifies/summarises | `ingest_broadcast`/`ingest_social` still use `web_search` behind flags |
| A8 | Article validation (HTTP 200, canonical, publisher, date, language, country, not dup, not 404) | **PARTIAL** | Date windowing `get_briefing_window`; dedup; `_is_excluded_domain`; Google-News URL resolution | No HTTP liveness check, no canonical resolution, no language/country assertion |
| A9 | Date validation with timezone | **YES** | `_parse_pub_dt`, `_normalize_pub`, aware-ET comparisons | — |
| A10 | Republished/wire-story detection | **PARTIAL** | `_cluster_stories`, `clustering.py`, `better_deduplicate` | No explicit wire-service lineage (Reuters→Yahoo→MSN) |
| A11 | Story clustering, one summary many publishers | **YES** | `_cluster_stories` → `_summaries_for` → `_collect_sections` | — |
| A12 | Cost tracking table + endpoints | **NO** | **Nothing** — no `cost_usd`/`tokens_in` anywhere | `bulletin_cost_logs`, `/costs`, `/costs/{run_id}` absent |
| A13 | Cost alerting ($2 run / $5 day / spike) | **NO** | — | Absent |
| A14 | 80 % Claude cost reduction | **UNVERIFIABLE** | No baseline instrumentation exists | See `00_gap_analysis.md` §4 — the $6.65 baseline is not reproducible |
| A15 | Quality gate before publish | **PARTIAL** | `_build_coverage_report`, `/coverage-assurance/{agency_id}`, PWS coverage | No blocking gate; thresholds not enforced pre-publish |
| A16 | Coverage report per run | **YES** | `_build_coverage_report`, `coverage_json` in `bulletin_run_log` | — |
| A17 | `bulletin_statistics` table | **PARTIAL** | `bulletin_run_log` carries 8 metrics + `coverage_json` | Named table absent; overlap is substantial |
| A18 | Editor review (move/merge/edit/pin/hide/approve/preview/publish) | **PARTIAL** | `/queue`, `/briefings/{id}/approve`, `/briefings/{id}/preview`, `editor_audit.py`, `editorial_rules.py` | No move/merge/pin/hide endpoints |
| A19 | Exports HTML + DOCX + PDF | **YES** | `_render_agt_html`, `_render_agt_docx`, `pdf_generator.py`, plus Excel | Outlook-safety and page numbers unverified |
| A20 | Bulletin format (TOC, badges, back-to-top, social, archive, footer) | **PARTIAL** | `_render_agt_html` renders the 6 client buckets; `_is_paywalled_url` badge; `_leadership_prefix` | TOC / back-to-top / page numbers unverified — **needs the sample to judge** |
| A21 | Provider health dashboard | **PARTIAL** | `health_monitor.py` probes; `provider_analysis.py` analytics; `/health` | No `/providers/health` with rate-limit/cost/success-rate |
| A22 | Manual "Run Now" with provider+profile selection | **PARTIAL** | `POST /run/{agency_id}`, `/run/{agency_id}/sync`, `/collect/{agency_id}` | Body does not accept `providers[]` or `profiles[]` |
| A23 | `GET /status/{bulletin_id}` with progress | **PARTIAL** | `/briefings/{id}`, `/runs/{agency_id}/{run_id}` | No live progress/cost field |
| A24 | Operational runbook | **NO** | Adjacent runbooks exist in `docs/runbooks/` | `10_runbook.md` absent |
| A25 | Preserve scheduler, windows, weekend rule | **YES — do not touch** | `scheduler.py:187` (72 h) + `get_briefing_window()` agree | — |
| A26 | US/FCC focus, exclude irrelevant foreign | **YES** | `_is_fcc_relevant_v2` 3-tier, `fcc_relevance_points`, `_is_excluded_domain` | Country not asserted per-article |

**Score: 8 met · 12 partial · 6 not met** (excluding A14, unverifiable).

## B. Requirements from the in-repo authoritative spec

| # | Requirement (source) | Currently met? | Gap |
|--:|---|:--:|---|
| B1 | Section 508 / WCAG 2.1 AA (Spec §10, Blueprint §10) | **UNKNOWN** | Not assessed here; not in your brief's phases |
| B2 | Audit trail (Spec §9, Blueprint §9) | **YES** | `bulletin_audit_log`, `editor_audit.py`, `/audit/{agency_id}` |
| B3 | Coverage assurance "HONEST — binding" (Spec §8) | **PARTIAL** | Endpoint exists; honesty gate not enforced pre-publish |
| B4 | Delivery workflow (Spec §6) | **YES** | `deliver_briefing`, `bulletin_delivery_log`, `/send/{agency}/{id}` |
| B5 | 8 AM ET government lifecycle (Blueprint §4) | **PARTIAL** | Scheduler runs **1 AM ET**, not 8 AM — reconcile with the client |

> **B5 is a live discrepancy** between the Blueprint and the running system. Worth
> confirming which is contractually correct before Phase 9.
