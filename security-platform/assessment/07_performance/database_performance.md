# Database Performance Review

> Static review of models, indexes, and query shapes (PostgreSQL 16, no live `EXPLAIN` — query-plan concerns are estimated). Read-only.

## Index coverage

**The new `tefca_registry` models are well-indexed** (`tefca_registry/models.py`): entity level/status/state/name indexes (l.63-68), identifier type/value/npi (l.99-104), **relationship parent AND child indexed** (l.136-137), verification/endpoint/finding/import indexes. The core `models/database.py` set also indexes its FK columns (`user_id`, `document_id`, `audio_file_id`, `checksum_sha256`). So the "15 FK columns missing indexes" from Part 1 does **not** apply here — it lives in the **legacy `Tefca/models.py`**.

### FK / lookup columns WITHOUT their own index (`Tefca/models.py`)
| Column | Line | Note |
|---|---|---|
| `evidence.cycle_id` | ~162 | only covered as composite `(entity_id, cycle_id)` — no standalone `cycle_id` index |
| `nppes_cache.entity_id`, `.cycle_id` | ~209-210 | unindexed |
| `finding.entity_id` | ~234 | unindexed |
| `finding.related_evidence_record_id` | ~247 | unindexed pseudo-FK |
| `report.cycle_id` | ~259 | unindexed |
| `queue.record_id`, `.entity_id`, `.cycle_id` | ~287-289 | unindexed |
| `tefca_registry` `managing_org_id` | models.py:186 | unindexed pseudo-FK |
| `tefca_registry` `verification_check_id` | models.py:257 | pseudo-FK, backs `get_entity_detail` joins |
| `tefca_registry` `entity_version_id` | models.py:205 | pseudo-FK |

**~10 unindexed FK/lookup columns** — each an unindexed join/filter path that becomes a seq-scan as the parent tables grow.

## JSONB — **no GIN indexes anywhere**

- JSONB is pervasive: `tefca_registry/models.py:54 fhir_resource`, `55 exchange_purposes`, `153 snapshot_data`, `214 summary`, `238-239 response_data/discrepancies`, `268 evidence`; `Tefca/models.py:106-110`, `173-177`, `212-213`; and **PHI-bearing JSONB in `case_management/models.py`** (`address`, `insurance_primary/secondary`, `diagnoses_icd10`, `hcc_codes`, `sdoh_flags`, `care_plan_updates`, l.98-152).
- **0 `gin` / `postgresql_using` / `CREATE INDEX ... gin`** in models or `alembic/versions/*`.
- **Concrete failing query:** `fhir_import.py:244` filters `TefcaRegEntity.fhir_resource["id"].astext == parent_fhir_id` (`fhir_resource->>'id'`) → **sequential scan** today. Any `@>` containment search on `fhir_resource` would too.
- **Fix:** add a **GIN index** on `fhir_resource` (and an **expression index** on `(fhir_resource->>'id')` for the import lookup); GIN on any JSONB column that gets containment/key queries.

## Recursive hierarchy

- **No SQL `WITH RECURSIVE`.** The QHIN → Participant → Sub-Participant tree is walked in **application code** (`queries.py get_subtree/build` l.279-290 + `get_children` + per-node `_child_count`) — this is the N-04 multiplicative N+1. At realistic tree sizes it issues **O(nodes × children)** queries.
- **Fix:** a single recursive CTE (anchor = root entity, recursive = join `TefcaEntityRelationship` on `parent_entity_id`) returns the whole subtree in one round-trip, with counts via a grouped join.

## Table growth projections

| Table | Growth driver | Index support at 100K+ rows |
|---|---|---|
| `tefca_reg_audit_log` | every registry mutation | **Good** — indexed on entity/action/actor/created (models.py:343-346) |
| `TEFCAConnectorLog` | every connector call | **Poor** — full-scanned by dashboard trends (routes.py:1258), no aggregate index |
| `TEFCAReview` | every review record | **Poor** — full-scanned + filtered/sorted (routes.py:1237, 2427-2431) without a supporting composite index |
| evidence / findings / verification checks | per verification run | partial — some composite prefixes; standalone FK gaps above |
| FHIR entity/version tables | per import + version snapshot | entity indexed; `fhir_resource` JSONB **not** GIN-indexed |

**`TEFCAConnectorLog` and `TEFCAReview` will hurt first past 100K rows** — they are dashboard-hot and unindexed for their aggregate/filter patterns.

## Composite index recommendations

Existing good ones: `idx_tefca_entities_npi_qhin`, `idx_tefca_entities_status_bucket`, `idx_evidence_entity_cycle`, `idx_queue_status_tier_priority`.

**Add:**
- `(review_id, finding_type)` on findings — used at routes.py:1204-1205, 2438-2439.
- `(status, qhin, created_at)` on `TEFCAReview` — reviewer-queue filter+sort (routes.py:2427-2431).
- `(connector_name, checked_at)` on `TEFCAConnectorLog` — trends aggregation (routes.py:1258).
- standalone indexes on the ~10 unindexed FK columns above.

## Database performance verdict
Schema design is sound and the **registry tables are genuinely well-indexed**, but three concrete gaps will bite at scale: **(1) no GIN on `fhir_resource`** (seq-scans on FHIR-id lookups), **(2) the hierarchy is walked in Python instead of a recursive CTE** (multiplicative N+1), and **(3) the dashboard-hot `TEFCAConnectorLog`/`TEFCAReview` tables lack aggregate/filter indexes** and are full-scanned. Plus ~10 unindexed legacy FK columns. All are additive migrations — no schema redesign needed.
