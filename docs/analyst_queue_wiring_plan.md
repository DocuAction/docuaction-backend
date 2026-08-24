# ANALYST QUEUE WIRING PLAN

**Date:** 2026-08-22
**Status:** DOCUMENTATION ONLY — nothing in this plan has been implemented.
**Blocked on:** Methodology Decision **D3** (which tier receives B3).

---

## 1. THE EXACT GAP

### 1.1 What computes the tier

`app/tefca_registry/rce/arc_pipeline.py`

```
line  46   BUCKET_TO_TIER = {"B1": 1, "B2": 2, "B3": 3, "B4": 3}
line  50   TIER_ROLE      = {1: "system", 2: "reviewer", 3: "senior_analyst"}
line 289   tier = BUCKET_TO_TIER.get(classification.bucket, 3)     <-- COMPUTED HERE
```

### 1.2 Where it is discarded

The value is never written to a queue. It survives in exactly two places, neither
of which is a work item:

| Location | Form | Usable as a queue? |
|---|---|---|
| `arc_pipeline.py:323` — `TefcaVerification.detail` | prose: `"... routed to tier 3."` | No — free text |
| `arc_pipeline.py:362` — the HTTP response body | `{"tier": 3, "assigned_role": "senior_analyst"}` | No — discarded after the response |

`review_records` has **no tier column**. Confirmed against the live schema — 16
columns: `id, review_id, entity_id, sample_id, verification_results,
classification_bucket, classification_rule, classification_rule_version,
classification_rationale, reviewer_resolution, reclassified_to, reclassified_by,
reclassified_at, resolution_rationale, reviewed_at, created_at`.

`await db.commit()` at line 373 closes the transaction. The tier is gone.

### 1.3 What should receive it

`tefca_analyst_queue` — which exists, is indexed, is served by four routes and is
rendered by a working frontend page. It holds **0 rows**.

The legacy path *does* populate it: `app/Tefca/routes.py:413` `_enqueue_if_needed()`,
called from `_validate_and_persist` (line 469) and the batch path (line 725). That
function is never called from `arc_pipeline`.

---

## 2. EXISTING INFRASTRUCTURE

### 2.1 Backend routes

| Route | File:line | Required role |
|---|---|---|
| `GET /api/v1/tefca/queue/tier2` | `app/Tefca/routes.py:786` | `viewer` (1) |
| `GET /api/v1/tefca/queue/tier3` | `app/Tefca/routes.py:799` | `senior_analyst` (5) |
| `PATCH /api/v1/tefca/queue/{record_id}/classify` | `app/Tefca/routes.py:812` | `senior_analyst` (5) |
| `PATCH /api/v1/tefca/queue/{record_id}/escalate` | `app/Tefca/routes.py:867` | `senior_analyst` (5) |

### 2.2 Frontend

`frontend/src/app/tefca-arc/validation/page.js` (294 LOC) — calls both queue
endpoints (lines 85–86). Its own header records the live probe result:

> *"The tier queues EXIST and answer; they are empty. A real zero is not
> 'Awaiting Data'."*

The page is complete and correct. It has nothing to display.

### 2.3 Database tables and live row counts

| Table | Rows | Role |
|---|---|---|
| `tefca_analyst_queue` | **0** | the queue |
| `tefca_evidence_records` | **0** | `record_id` FK target |
| `tefca_entities` | **0** | `entity_id` FK target |
| `tefca_review_cycles` | **0** | `cycle_id` FK target |
| `review_records` | **43** | what the RCE path actually writes |
| `tefca_reg_entities` | **23,756** | what the RCE path actually writes |

---

## 3. WHY THIS IS NOT ONE FUNCTION CALL

`tefca_analyst_queue` cannot accept a row from the RCE path. Three structural
blockers, verified against the live schema:

| # | Blocker | Detail |
|---|---|---|
| 1 | **`record_id` is `NOT NULL` with FK → `tefca_evidence_records.record_id`** | The RCE path produces `review_records.id`. There is no `tefca_evidence_records` row to reference, and the column cannot be left null. Constraint: `tefca_analyst_queue_record_id_fkey` |
| 2 | **`entity_id` FK → `tefca_entities.entity_id`** | The RCE path produces `tefca_reg_entities.id`. Different table, zero rows. Constraint: `tefca_analyst_queue_entity_id_fkey` |
| 3 | **`bucket_classification` is the enum `bucketclassification` with values `'1','2','3','4'`** | The RCE path produces the strings `'B1'..'B4'` |

A fourth, non-structural blocker: `cycle_id` FK → `tefca_review_cycles`, which is
also empty. That column is nullable, so it is a gap rather than a hard failure.

**Only step 4 below is genuinely "one function call". Steps 1–3 are schema work.**

---

## 4. TWO IMPLEMENTATION OPTIONS

### Option A — retrofit `tefca_analyst_queue`

Make `record_id` nullable, add `review_record_id UUID FK → review_records.id`, add
`registry_entity_id UUID FK → tefca_reg_entities.id`, add a text bucket column
alongside the enum, and teach `_queue_item_dto` and both GET routes to handle
either shape.

- **Pro:** one queue, one set of routes, the existing frontend page works unchanged.
- **Con:** couples the new generation to three tables that the architecture
  resolution recommends retiring (`tefca_entities`, `tefca_evidence_records`,
  `tefca_review_cycles`). Two nullable FKs and a duplicated bucket representation
  are carried indefinitely.

### Option B — purpose-built `arc_review_queue` *(recommended)*

A new table keyed to the tables the RCE path actually writes:

```
arc_review_queue
  id                 UUID PK
  review_record_id   UUID NOT NULL FK -> review_records.id
  entity_id          UUID NOT NULL FK -> tefca_reg_entities.id
  tier               SMALLINT NOT NULL          -- 1 | 2 | 3
  assigned_role      VARCHAR(30) NOT NULL       -- system | reviewer | senior_analyst
  bucket             VARCHAR(2)  NOT NULL       -- B1..B4, the same vocabulary as review_records
  priority           SMALLINT NOT NULL
  queue_reason       TEXT
  status             VARCHAR(20) NOT NULL DEFAULT 'PENDING'
  claimed_by/at, completed_by/at, created_at, updated_at
  UNIQUE (review_record_id)                     -- one work item per determination
```

- **Pro:** no nullable FKs, no enum mismatch, no coupling to retiring tables. One
  determination, one work item, enforced.
- **Con:** two queue tables coexist until the legacy path is retired; two sets of
  read routes until then.

**Recommendation: Option B.** The retrofit's cost is permanent; the duplication is
temporary and ends when the legacy path is retired.

---

## 5. LOC ESTIMATE

### 5.1 Tier → analyst queue insertion

| Work | Prod | Test |
|---|---|---|
| Migration creating `arc_review_queue` (Option B) | 70 | 30 |
| `_enqueue_arc_item()` helper in `arc_pipeline` | 35 | 55 |
| Call site after line 289; skip T1 (auto-complete writes no work item) | 5 | 15 |
| `GET /api/tefca/arc/queue/{tier}` read routes, role-gated | 65 | 60 |
| Idempotency: re-running `/verify` must not duplicate work items | 15 | 30 |
| **Subtotal** | **190** | **190** |

### 5.2 Analyst determination → QA gate

| Work | Prod | Test |
|---|---|---|
| Migration: `review_records.qa_approved_by / qa_approved_at / qa_notes` | 35 | 20 |
| `PATCH /reviews/{id}/qa-approve`, gated on `qalead` (level 6) | 45 | 45 |
| Segregation of duties — QA actor must differ from the reviewer, modelled on the working precedent in `curation.transition_issue:393-404` | 25 | 45 |
| Audit the QA act via `reg_audit.record` | 10 | 15 |
| **Subtotal** | **115** | **125** |

### 5.3 QA gate → final determination

| Work | Prod | Test |
|---|---|---|
| Make the existing gate refuse rather than report. `GET /api/tefca/qa/report-gate` exists (`routes.py:2786`) and **nothing calls it** — grep for `report_gate` returns only the endpoint and `qa_engine` itself | 30 | 40 |
| Report generation refuses, or explicitly labels, determinations without QA | 25 | 35 |
| **Subtotal** | **55** | **75** |

### 5.4 Frontend

| Work | Prod |
|---|---|
| Surface `arc_review_queue` rows on the existing validation page | 55 |
| QA action + state on the QA page | 60 |
| **Subtotal** | **115** |

### 5.5 Total

| | Production | Test |
|---|---|---|
| Option B (recommended) | **~475 LOC** | **~390 LOC** |
| Option A (retrofit) | ~560 LOC | ~430 LOC |

---

## 6. THE BLOCKING DEPENDENCY

**Methodology Decision D3 determines which tier receives B3.**

| If D3 resolves to | Effect on the current 43-entity population |
|---|---|
| **T2** (Classifier A's model) | 20 entities enter the `viewer`-readable T2 queue, worked by `reviewer` |
| **T3** (Classifier B's model) | 20 entities enter the `senior_analyst`-gated T3 queue |

That is 20 of 43 entities — **47% of the population** — routed to a different
person, at a different privilege level, from a different endpoint.

Wiring the queue before D3 is answered would populate it with rows that must then
be re-routed, and would create a period in which analysts worked items the
approved methodology says were not theirs to work.

`BUCKET_TO_TIER` and `TIER_ROLE` in `arc_pipeline.py:46,50` are the single point of
change once D3 is decided.

---

## 7. SEQUENCING

1. Obtain **D3**. *(blocking)*
2. Migration: create `arc_review_queue`.
3. Wire `_enqueue_arc_item()` into `arc_pipeline` after line 289.
4. Read routes + role gates.
5. Migration: QA columns on `review_records`.
6. QA approval route with segregation of duties.
7. Make the report gate enforce.
8. Frontend.

Steps 5–7 are independent of D3 and could proceed in parallel once approved
separately.
