# Delivery Workflow Remediation — Implementation Contract (2026-09-17)

This document is the single contract every implementation lane builds against.
It fixes the vocabularies, table names, module boundaries, API shapes, role
floors and status derivation so that lanes working in parallel converge.
Deviations must be recorded here first.

Compliance position: the design supports defensible audit lineage, evidence
integrity, authenticity, reproducibility and operational accountability. It
aligns with ONC auditing and tamper-resistance principles, HL7 FHIR Provenance
and AuditEvent concepts, NIST log-management practice, CMS/NPPES NPI validation
evidence and WCAG 2.2 AA. It does not claim that a contract clause mandates a
particular database design.

## 1. Foundation already in place (do not redefine)

| Module | Purpose |
|---|---|
| `app/tefca_registry/rce/traceability_models.py` | Five tables + constants: `RceDeliveryStageEvent`, `RceDispositionEvent`, `RceReconciliationSnapshot`, `TefcaIdentifierDecisionEvent`, `RceDeliveryReportLink`; vocab tuples `STAGE_EVENT_STAGES`, `DISPOSITIONS`, `IDENTIFIER_DECISIONS`, `SNAPSHOT_TRIGGERS`; view name `CURRENT_DISPOSITIONS_VIEW` |
| `alembic/versions/20260917_delivery_traceability.py` | Creates the five tables, the view, and grants (SELECT+INSERT everywhere, UPDATE on stage events only, no DELETE). Downgrade refuses if any row exists. |
| `app/tefca_registry/rce/stage_events.py` | `open_stage`, `close_stage`, `record_instant`, `timeline`, `summarise` |
| `app/tefca_registry/rce/dispositions.py` | `record`, `current_for_intake`, `counts_for_intake`, `history_for_record`, `records_without_disposition`, `equation`, `material_changes`; reason-code constants |
| `app/tefca_registry/rce/identifier_decisions.py` | `raise_conflict`, `decide`, `apply_confirmed_submitted`, `latest_event`, `history`, `unresolved_for_intake` |
| `app/tefca_registry/rce/status_model.py` | `processing_outcome`, `review_state`, `coverage_state` and the vocabularies |
| `app/core/request_context.py` | context vars, `bind`, `correlation_id`, `build_identity`, `build_sha`, `RequestContextMiddleware` |
| `app/core/logging_config.py` | `configure_logging`, `JsonFormatter`, `redact`, `safe_error` |

Snapshot writer: `reconciliation.py` (lane P) persists `RceReconciliationSnapshot` with
`sequence = max+1 per job`, `hash = sha256(canonical json of equation+dimensions+checks)`,
`migration_revision` read from `alembic_version`, `build_sha` from `request_context`.

## 2. NPI rule codes (rule set 1.2.0)

| rule_id | issue_type | severity | authority | stage | holds? | identifier written? |
|---|---|---|---|---|---|---|
| NPI-001 | `NPI_REQUIRED` (when profile requires) / `NPI_NOT_SUPPLIED` (optional) | HIGH / INFORMATIONAL | HUMAN_REQUIRED / NO_CORRECTION | QUALITY | required only | n/a |
| NPI-002 | `NPI_LENGTH_INVALID` | HIGH | HUMAN_REQUIRED | QUALITY | yes | no |
| NPI-004 | `NPI_FORMAT_INVALID` | HIGH | HUMAN_REQUIRED | QUALITY | yes | no |
| NPI-003 | `NPI_CHECKSUM_INVALID` | HIGH | HUMAN_REQUIRED | QUALITY | yes | no |
| NPI-002 | `MULTIPLE_NPI_IN_ONE_FIELD` (kept) | HIGH | HUMAN_REQUIRED | QUALITY | yes | no |
| NPI-005 | `NPI_NOT_FOUND` | MEDIUM | HUMAN_REQUIRED | VERIFICATION | no | stays, flagged |
| NPI-006 | `NPI_DEACTIVATED` | HIGH | HUMAN_REQUIRED | VERIFICATION | no (post-promotion) | status set `inactive_pending_review` |
| NPI-008 | `NPI_EXISTING_VALUE_CONFLICT` | HIGH | HUMAN_REQUIRED | PROMOTION | yes | existing untouched, submitted preserved |
| NPI-009 | `NPI_VERIFICATION_UNAVAILABLE` | INFORMATIONAL | NO_CORRECTION | VERIFICATION | no | n/a |

Order in QUALITY: applicability → presence → length → numeric format → Luhn (80840).
A value that fails length is not evaluated for format or checksum; a value that
fails format is not evaluated for checksum (one primary finding per value).
Historical rows keep `NPI_MALFORMED` / `NPI_CHECK_DIGIT_FAILED`; expose
`LEGACY_ISSUE_TYPES = {"NPI_MALFORMED": ["NPI_LENGTH_INVALID","NPI_FORMAT_INVALID"], "NPI_CHECK_DIGIT_FAILED": ["NPI_CHECKSUM_INVALID"]}` in `quality_rules.py`.
Promotion identifier screen calls `app.services.npi_validator.validate_npi`.
Verification-time findings (NPI-005/006/009) are written to `rce_issues` with
`run_id` = current run of the intake so they appear in the one ledger.

## 3. Dispositions and reason codes

Disposition written by promotion for every curated record (sequence 1, SYSTEM):

| Condition | disposition | reason_code |
|---|---|---|
| `parse_status != ok` (REJECTED curated) | REJECTED | REJECTED_UNPARSEABLE |
| HELD by quality issue | HELD | HELD_QUALITY_ISSUE |
| HELD by identifier conflict | HELD | HELD_IDENTIFIER_CONFLICT |
| schema drift refusal | HELD | HELD_SCHEMA_DRIFT |
| no `rce_org_oid` or no `name` | MISSING_KEY | MISSING_KEY_NO_OID_OR_NAME |
| `is_test_record` and profile excludes test records | EXCLUDED | EXCLUDED_TEST_RECORD |
| new entity | CREATED | CREATED_NEW_ENTITY |
| matched, material fields differ | UPDATED | UPDATED_MATERIAL_FIELDS (changed_fields listed) |
| matched, no material difference | MATCHED_UNCHANGED | MATCHED_NO_MATERIAL_CHANGE |

Material fields for UPDATED vs MATCHED_UNCHANGED: `name, entity_level,
operational_status, is_active, address_line, address_city, address_state,
address_postal_code, exchange_purposes, sequoia_org_type, org_managing_org`.
Identifiers (`npi, tefcaid, hcid, aaid`) are compared separately: any difference
raises a conflict (NPI-008 for npi; `IDENTIFIER_EXISTING_VALUE_CONFLICT` with
`field_name` for the others) and HOLDS the record; the entity is not updated.
On UPDATED the promotion writes a `tefca_entity_versions` row and a registry
audit row `entity_updated`.
`rce_source_records.promotion_status` mirror: promoted → `promoted`; HELD → `held`;
REJECTED/EXCLUDED/MISSING_KEY → `excluded`.

## 4. Status derivation

`status_model.processing_outcome(...)` and `status_model.review_state(...)` are
the only sources. Inputs come from: job row, latest snapshot, `stage_events.summarise`,
open findings at HIGH/CRITICAL (current run), `identifier_decisions.unresolved_for_intake`,
invalid identifiers promoted (count of active npi identifier rows for the intake's
entities whose value fails `validate_npi`), failed required verification (evidence
rows with disposition FAIL on a required dimension), review record counts.
Expose on job `to_dict()` as `processing_outcome` and `review_state` objects
(`{value, code, detail, basis?}`); keep `state` and `stage` unchanged.

## 5. Stage events written by the runner

REGISTERED (route, instant), RECEIPT_PRESERVED (route, instant), SHA256 (route,
instant), SCHEMA_VALIDATION + PARSING (Area 1; open/close), QUALITY, CURATION,
MATCHING + PROMOTION + RELATIONSHIPS (promotion: open MATCHING when the OID map
loads, PROMOTION for pass 1, RELATIONSHIPS for pass 2), VERIFICATION_READINESS,
RECONCILIATION, READY_FOR_REVIEW (instant, only when snapshot passed),
REPORT_GENERATION (report generator, instant, on delivery_processing).
Runner binds `request_context.bind(job_id=..., intake_id=..., stage=..., attempt=...)`.
All runner error logs use `exc_info=True`.

## 6. Job-keyed detail API

`GET /api/tefca/rce/delivery-jobs/{id}/detail` — viewer floor; evidence blocks
require reviewer (return `null` + `availability.<block> = "requires_role:reviewer"`).
Resolution: job id → `resolved_from: "job_id"`; else intake id with exactly one
job → `"intake_id"`; more than one job → 409 `{candidates: [...]}`; none → 404.

```
{ resolved_from, job:{...to_dict + failed_stage, remediation_guidance},
  status:{processing_outcome:{value,code,detail,basis}, review_state:{value,code,detail}},
  counts:{records_received, records_accounted, ...},
  timeline:[stage events], reconciliation:{snapshot|null, history_count},
  dispositions:{created,updated,matched_unchanged,held,rejected,missing_key,excluded,total,equation},
  exceptions:{total, open, resolved, by_code:{}, by_severity:{}},
  verification:{state, sources:{nppes:{coverage_state...}, pecos:{}, leie:{}, sam:{}}},
  reports:[{report_id, report_type, snapshot_id, generated_at, artifacts:[...]}],
  build:{git_sha, build_time, version, migration_revision},
  correlation:{request_id, job_correlation_id},
  availability:{records|exceptions|lineage|audit|verification|reports: one of
      available | not_yet | never_ran | not_configured | processing | reconstructed
      | unavailable | requires_role:reviewer} }
```

Other routes (all under `/api/tefca/rce`):

| Route | Floor |
|---|---|
| GET /delivery-jobs/{id}/timeline | viewer |
| GET /deliveries/{intake_id}/dispositions?disposition&source_row&entity_name&npi&limit&offset | reviewer |
| GET /deliveries/{intake_id}/dispositions.csv | reviewer |
| GET /deliveries/{intake_id}/exceptions?source_row&entity_name&npi&rule_code&issue_type&severity&stage&status&from&to&assigned_to&disposition&limit&offset | reviewer |
| GET /deliveries/{intake_id}/verification-coverage | viewer |
| GET /deliveries/{intake_id}/audit | reviewer |
| GET /deliveries/{intake_id}/records, /curated, /curated/{id}/lineage | reviewer (was viewer) |
| POST /issues/{issue_id}/dispositions `{decision, reason, corrected_value?}` | reviewer |
| POST /identifier-decisions `{entity_id, identifier_type, decision, reason, selected_value?}` | reviewer |
| POST /api/tefca/rce/deliveries (sync upload) | program_manager (was contributor) + `Deprecation` header |
| GET /api/admin/health | admin |

Exception ledger row shape (`/exceptions`): `{issue_id, issue_code, delivery:{job_id,intake_id},
source_row, source_record_id, entity_name, submitted_value, existing_value, normalized_value,
rule_code (rule_id), issue_type, legacy_issue_type?, severity, stage, description,
reason_code, created_at, status (resolution), assignee, disposition_history:[...],
actor, decided_at, evidence_source, correlation_id, build_sha}`.

## 7. Reports

New `report_type = "delivery_processing"` in `RCE_TYPES`; requires
`parameters.job_id` or `parameters.intake_id` (422 otherwise; `data_quality` and
`intake` also require it now). Data service `app/reports/data/delivery_processing_data.py`
reads the LATEST snapshot unless `parameters.snapshot_id` is given (regeneration).
Template `app/reports/templates/delivery_processing.html`. CSV = record-level
dispositions (line_number, source id, name, submitted NPI, curated NPI, disposition,
reason_code, entity_id, finding_count, warning_count). Generation writes an
`audit_logs` row (`action=report_generated`, `event_type=reporting`) and an
`RceDeliveryReportLink`. Download writes `report_downloaded`.
Report `generated_at`, `build_sha`, `snapshot_id`, `template_version` printed on page 1.

## 8. Health

Public `GET /health`: `status, service, version, git_sha, build_time, environment,
modules{name: active|disabled}`. Nothing else.
Admin `GET /api/admin/health` (admin): public fields + `migration_revision,
database{reachable, latency_ms}, scheduler, connectors (with notes), usps, program_profile,
request_id`.

## 9. Module gate

Add `/api/v1/tefca` to the `tefca_arc` prefix tuple in `app/core/modules.py`.

## 10. Frontend

Canonical detail URL: `/tefca-arc/deliveries/detail/?job={job_id}`. List page adds a
"View details" link column; row click / Enter / Space navigate; list state
(sort, page, filters) preserved in the URL query. Tabs: Overview, Processing Timeline,
Records, Exceptions, Verification, Changes and Lineage, Audit History, Reports.
Two-axis status columns. Validation Queue page → work queue with
`queue_source=RCE_DQ_HUMAN_REQUIRED&intake_id=`. Nav: "Registry Findings" (renamed),
"Delivery Exceptions" (new picker page `/tefca-arc/exceptions/`). Badge bar reads
connector health; per-delivery coverage from detail. `X-Request-ID` sent on every
call; 401 redirect carries `?reason=expired`. Footer shows `NEXT_PUBLIC_BUILD_SHA`.
