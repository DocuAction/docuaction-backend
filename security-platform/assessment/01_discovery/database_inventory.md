# Database Inventory — DocuAction

**Engine:** PostgreSQL 16 (Azure Flexible Server, Burstable, geo-redundant backups, HA disabled)
**Declared models (code):** 113 `__tablename__` declarations · **Tables in local dev DB:** 51
**FK constraints (local):** 41 · **Indexes (local):** 151 · **FK columns without a leading index:** 15

> **Data source:** LOCAL dev PostgreSQL (`localhost/docuaction-db`), SELECT-only. Prod (`docuaction-db-geo`) shares the same schema for the deployed modules; prod `tefca_reg_entities` = 177 (local shows 183 due to +6 local FHIR/CSV import-test rows and verification runs).

## Key finding — model↔table gap

The codebase declares **113 models on TWO different declarative Bases**, but only **51 tables** exist in the dev DB:
- `app.core.database.Base` → users, documents, outputs, audio, transcripts, audit_logs, **all platform_\*, tefca_reg_\*/tefca_entity_\*, and legacy tefca_\*** (created at startup via `Base.metadata.create_all`).
- `app.database.Base` → **procurement / ERP / ATS / migration** models (customers, rfqs, quotes, suppliers, products, invoices, contracts, employees, candidates, applications, migration_\*, …) — **not materialized** in this DB.
- `case_management` (`cm_*`) models — **0 tables** present locally.

➡️ *This means large parts of the declared data model are either deployed to a different database, created lazily elsewhere, or dormant. This should be resolved in Part 2 (Architecture) and confirmed against prod.*

## Table categories (declared, 113)

| Category | Prefix / examples | Count (declared) | In local DB |
|---|---|---:|---:|
| Core platform | users, documents, outputs, audio_files, transcripts, audit_logs | 6 | ✅ |
| Platform config | `platform_*` (tenants, agencies, programs, modules, workspaces, pages, features, workspace_features, data_sources, themes, jurisdictions, import_formats, identifier_types) | 13 | ✅ 13 |
| TEFCA Registry (new) | `tefca_reg_entities`, `tefca_reg_audit_log`, `tefca_entity_identifiers/relationships/versions/endpoints/findings`, `tefca_verification_jobs/checks`, `tefca_import_batches` | 10 | ✅ 10 |
| TEFCA legacy | `tefca_entities`, `tefca_review_cycles`, `tefca_evidence_records`, `tefca_source_cache`, `tefca_priority_cases`, `tefca_reports`, `tefca_analyst_queue`, `tefca_connector_logs`, `tefca_reviews`, `tefca_findings`, `tefca_import_history` | 13 | ✅ 13 |
| Procurement / GovCon | customers, rfqs, bom_items, quotes, quote_line_items, suppliers, supplier_*, products, product_catalog, deal_registrations, price_history, tax_jurisdictions, purchase_orders, proposal_library, technical_library | ~20 | ❌ |
| ATS / Staffing | candidates, applications, job_postings, submissions, bench_candidates, ats_activities, placement_outcomes | ~7 | ❌ |
| ERP / Finance | contracts, contract_staffing, employees, expenses, invoices, invoice_line_items, financials, dev_projects | ~8 | ❌ |
| Migration Intelligence | `migration_*` (projects, schemas, fields, mappings, mapping_versions, manifest_versions, profiling_results, validation_runs, logic_artifacts) | ~9 | ❌ |
| Case Management | `cm_*` (patients, care_plans, notes, discharge_records, government_cases, billing_summaries) | ~6 | ❌ |
| Enterprise / workflow / intel | tenants, tenant_users, tasks, actions, decisions, contexts, execution_queue, follow_up_queue, validation_queue, traceability, policy_validations, agency_metrics, communication_logs, outreach_logs, saved_searches, support_tickets, ai_memory, company_profiles, opportunities, process_jobs, output_templates, state_audit_log, audit_log | ~30 | mixed |

## Top tables by live rows (local dev)

| Table | ~Rows | Notes |
|---|---:|---|
| tefca_entity_identifiers | 567 | seed 550 + import-test |
| tefca_reg_audit_log | 239 | verification (219) + import audits |
| tefca_entity_relationships | 193 | seed 189 + import |
| tefca_entity_versions | 183 | 1 per entity |
| tefca_reg_entities | 183 | seed 177 + 6 import-test |
| tefca_verification_checks / jobs | 177 / 177 | one per entity |
| platform_jurisdictions | 57 | 50 states + DC + 6 territories |
| tefca_entity_findings | 42 | verification output |
| users / documents / audit_logs | 14 / 39 / 74 | core (baseline) |

## Foreign keys & indexes

- **41 FK constraints**, **151 indexes** (local schema).
- The registry schema is well-indexed (explicit `Index(...)` per spec, partial indexes with `WHERE`, unique constraints, `ON DELETE CASCADE` on identifiers).

### ⚠️ 15 FK columns without a leading (supporting) index

Un-indexed FK columns force sequential scans on joins and slow cascade deletes. Found:

| Table.Column |
|---|
| `audit_logs.user_id` |
| `platform_agencies.parent_agency_id` |
| `platform_tenants.default_theme_id`, `platform_tenants.default_agency_id` |
| `platform_workspace_features.feature_id` |
| `tefca_analyst_queue.entity_id / record_id / cycle_id` (legacy) |
| `tefca_evidence_records.cycle_id` (legacy) |
| `tefca_priority_cases.related_evidence_record_id / entity_id` (legacy) |
| `tefca_reports.cycle_id` (legacy) |
| `tefca_source_cache.cycle_id` (legacy) |
| `tefca_entity_findings.verification_check_id` (registry) |
| `tefca_verification_jobs.entity_version_id` (registry) |

*(Documented only — no changes made. Candidate remediation items for Part 7 Performance.)*

### JSONB columns without GIN indexes
`fhir_resource`, `exchange_purposes`, `snapshot_data`, `summary`, `evidence`, `response_data`, `configuration` are JSONB and currently queried by key (`fhir_resource->>'id'` in the import parent-resolver) without GIN indexes — fine at current scale, a Part-7 note as data grows.
