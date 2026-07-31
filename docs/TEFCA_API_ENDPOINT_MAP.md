# TEFCA API Endpoint Map

**Generated:** 2026-07-30 from the live dev OpenAPI schema (`https://docuaction-dev.azurewebsites.net/openapi.json`)  
**Total operations in schema:** 276 | **TEFCA-related:** 140

Auth column reflects the guard actually declared in source: router-level `dependencies=[Depends(require_role(...))]` where present, otherwise the per-route dependency. Where a test verified the anonymous response, that is noted.

> **Two parallel TEFCA stacks exist.** `/api/tefca/registry/*` is the normalized registry (`app/tefca_registry/`). `/api/tefca/*` and `/api/v1/tefca/*` are the older review-protocol module (`app/Tefca/`). They do not share entity storage, and no route joins them.

## TEFCA Registry (normalized stack) (19)

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/api/tefca/registry/entities` | reviewer (router-level `require_role`) | List Entities |
| GET | `/api/tefca/registry/entities/{entity_id}` | reviewer (router-level `require_role`) | Get Entity |
| GET | `/api/tefca/registry/entities/{entity_id}/children` | reviewer (router-level `require_role`) | Get Children |
| GET | `/api/tefca/registry/entities/{entity_id}/findings` | reviewer (router-level `require_role`) | Entity Findings |
| GET | `/api/tefca/registry/entities/{entity_id}/hierarchy` | reviewer (router-level `require_role`) | Get Subtree |
| POST | `/api/tefca/registry/entities/{entity_id}/verify` | reviewer (router-level `require_role`) | Verify Entity |
| GET | `/api/tefca/registry/findings` | reviewer (router-level `require_role`) | List Findings |
| GET | `/api/tefca/registry/hierarchy` | reviewer (router-level `require_role`) | Hierarchy Roots |
| POST | `/api/tefca/registry/import/csv` | reviewer (router-level `require_role`) | Import Csv Route |
| POST | `/api/tefca/registry/import/fhir-bundle` | reviewer (router-level `require_role`) | Import Fhir Bundle Route |
| GET | `/api/tefca/registry/import/history` | reviewer (router-level `require_role`) | Import History |
| GET | `/api/tefca/registry/import/{batch_id}` | reviewer (router-level `require_role`) | Import Detail |
| GET | `/api/tefca/registry/participants` | reviewer (router-level `require_role`) | List Participants |
| GET | `/api/tefca/registry/qhins` | reviewer (router-level `require_role`) | List Qhins |
| GET | `/api/tefca/registry/search` | reviewer (router-level `require_role`) | Search |
| GET | `/api/tefca/registry/stats` | reviewer (router-level `require_role`) | Get Stats |
| GET | `/api/tefca/registry/verification-jobs` | reviewer (router-level `require_role`) | List Jobs |
| GET | `/api/tefca/registry/verification-jobs/{job_id}` | reviewer (router-level `require_role`) | Get Job |
| POST | `/api/tefca/registry/verify` | reviewer (router-level `require_role`) | Verify Bulk |

## TEFCA Dashboard (2)

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/api/tefca/dashboard/summary` | viewer (per-route) | Executive dashboard summary (aggregate, viewer role required) |
| GET | `/api/tefca/dashboard/trends` | viewer (per-route) | Monthly trends for charting (aggregate, viewer role required) |

## TEFCA Review Protocol (/api/v1/tefca) (19)

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/api/v1/tefca/api/v1/tefca/demo/validate-sample` | reviewer (router-level `require_role`) | [dev] Validate N bundled entities via the REAL pipeline |
| GET | `/api/v1/tefca/connectors/status` | reviewer (router-level `require_role`) | Probe all data source connectors |
| GET | `/api/v1/tefca/cycles` | reviewer (router-level `require_role`) | List review cycles |
| POST | `/api/v1/tefca/cycles` | reviewer (router-level `require_role`) | Create review cycle |
| POST | `/api/v1/tefca/evidence/generate` | reviewer (router-level `require_role`) | Generate + persist 5-element evidence record |
| GET | `/api/v1/tefca/mock/entities` | reviewer (router-level `require_role`) | View bundled RCE development dataset |
| GET | `/api/v1/tefca/priority-cases` | reviewer (router-level `require_role`) | List priority cases |
| POST | `/api/v1/tefca/priority-cases` | reviewer (router-level `require_role`) | Create COR-directed priority case |
| PATCH | `/api/v1/tefca/priority-cases/{case_id}` | reviewer (router-level `require_role`) | Update priority case |
| GET | `/api/v1/tefca/queue/tier2` | reviewer (router-level `require_role`) | Tier-2 analyst queue |
| GET | `/api/v1/tefca/queue/tier3` | reviewer (router-level `require_role`) | Tier-3 SME escalation queue |
| PATCH | `/api/v1/tefca/queue/{record_id}/classify` | reviewer (router-level `require_role`) | Analyst override classification |
| PATCH | `/api/v1/tefca/queue/{record_id}/escalate` | reviewer (router-level `require_role`) | Escalate record to Tier-3 SME |
| GET | `/api/v1/tefca/reports` | reviewer (router-level `require_role`) | List generated reports |
| POST | `/api/v1/tefca/reports/final/{cycle_id}` | reviewer (router-level `require_role`) | Generate D3.2 final report |
| POST | `/api/v1/tefca/reports/weekly/{cycle_id}` | reviewer (router-level `require_role`) | Generate D3.1 weekly progress report |
| POST | `/api/v1/tefca/validate/batch` | reviewer (router-level `require_role`) | Run Tier-1 validation across a cycle (async, persisted) |
| POST | `/api/v1/tefca/validate/entity` | reviewer (router-level `require_role`) | Validate one RCE entity (persisted) |
| GET | `/api/v1/tefca/validate/status/{cycle_id}` | reviewer (router-level `require_role`) | Batch validation progress |

## TEFCA Legacy module (/api/tefca) (53)

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| POST | `/api/tefca/admin/seed-mock-data` | per-route (dashboard router has no router-level guard) | [admin] Apply RFQ columns + seed mock review data (idempotent) |
| POST | `/api/tefca/demo/run-cycle` | per-route (dashboard router has no router-level guard) | [admin] Run one QA validation cycle on a mock review (demo) |
| GET | `/api/tefca/discrepancy-taxonomy` | per-route (dashboard router has no router-level guard) | Discrepancy taxonomy (reference) |
| POST | `/api/tefca/entities/upload` | per-route (dashboard router has no router-level guard) | Import entities from CSV or JSON |
| GET | `/api/tefca/findings` | per-route (dashboard router has no router-level guard) | List findings across all entities |
| GET | `/api/tefca/findings/{finding_id}` | per-route (dashboard router has no router-level guard) | Single finding with evidence chain |
| GET | `/api/tefca/import/history` | per-route (dashboard router has no router-level guard) | Entity import history |
| GET | `/api/tefca/methodology` | per-route (dashboard router has no router-level guard) | Review methodology / control framework (reference) |
| GET | `/api/tefca/priority` | per-route (dashboard router has no router-level guard) | List priority cases (filters: status, qhin, date range) |
| POST | `/api/tefca/priority/create` | per-route (dashboard router has no router-level guard) | Create a COR-directed priority review (admin only) |
| POST | `/api/tefca/priority/quarterly-report` | per-route (dashboard router has no router-level guard) | Generate priority quarterly aggregation |
| GET | `/api/tefca/priority/{case_id}` | per-route (dashboard router has no router-level guard) | Priority case detail |
| POST | `/api/tefca/priority/{case_id}/execute` | per-route (dashboard router has no router-level guard) | Execute a priority review |
| GET | `/api/tefca/priority/{case_id}/report` | per-route (dashboard router has no router-level guard) | Formatted COR status report |
| GET | `/api/tefca/qa/alerts` | per-route (dashboard router has no router-level guard) | Recent QA threshold alerts |
| POST | `/api/tefca/qa/alerts/test` | per-route (dashboard router has no router-level guard) | Send a test QA alert email (verify delivery) |
| GET | `/api/tefca/qa/audit` | per-route (dashboard router has no router-level guard) | QA audit trail (filters: review_id, gate_name, gate_type, passed) |
| GET | `/api/tefca/qa/audit/export` | per-route (dashboard router has no router-level guard) | Export the QA audit trail as CSV |
| GET | `/api/tefca/qa/connector-health` | per-route (dashboard router has no router-level guard) | Connector health scores |
| GET | `/api/tefca/qa/evidence-summary` | per-route (dashboard router has no router-level guard) | Evidence completeness across all reviews |
| GET | `/api/tefca/qa/golden-records` | per-route (dashboard router has no router-level guard) | List the golden known-answer test cases |
| GET | `/api/tefca/qa/health` | public (documented as monitoring) | Platform readiness check (public — monitoring) |
| GET | `/api/tefca/qa/inter-rater` | per-route (dashboard router has no router-level guard) | [deprecated alias] Internal consistency score — NOT true inter-rater reliability |
| GET | `/api/tefca/qa/internal-consistency` | per-route (dashboard router has no router-level guard) | Internal consistency score (pipeline self-consistency — NOT inter-rater reliability) |
| GET | `/api/tefca/qa/regression` | per-route (dashboard router has no router-level guard) | Run golden-record regression; detect classification drift |
| POST | `/api/tefca/qa/report` | per-route (dashboard router has no router-level guard) | Generate a QA scorecard report (report_type='qa') |
| GET | `/api/tefca/qa/report-gate` | per-route (dashboard router has no router-level guard) | Evidence gate that must be open before a report is generated |
| GET | `/api/tefca/qa/sampling-validation` | per-route (dashboard router has no router-level guard) | Sampling validation vs Cochran @95% CI |
| GET | `/api/tefca/qa/score` | per-route (dashboard router has no router-level guard) | Overall QA score across all dimensions |
| GET | `/api/tefca/qa/sla` | per-route (dashboard router has no router-level guard) | Priority-review SLA tracking |
| GET | `/api/tefca/qa/statistical` | per-route (dashboard router has no router-level guard) | Combined statistical QA (sampling + internal consistency + CI) |
| GET | `/api/tefca/qa/sweep` | per-route (dashboard router has no router-level guard) | Run a full QA sweep (all gates + alerts + SLA) |
| POST | `/api/tefca/qa/validate-evidence/{review_id}` | per-route (dashboard router has no router-level guard) | Evidence + chain-of-custody QA on a review |
| POST | `/api/tefca/qa/validate-review/{review_id}` | per-route (dashboard router has no router-level guard) | Trigger full QA validation on a review |
| GET | `/api/tefca/reports` | per-route (dashboard router has no router-level guard) | List reports (filters: type, start, end) |
| POST | `/api/tefca/reports/biweekly` | per-route (dashboard router has no router-level guard) | Generate a bi-weekly ongoing review (SOW Task 4) |
| GET | `/api/tefca/reports/export` | per-route (dashboard router has no router-level guard) | CSV export of reviews (role-gated — contains PII) |
| POST | `/api/tefca/reports/final` | per-route (dashboard router has no router-level guard) | Generate the final retrospective report (SOW Task 3) |
| POST | `/api/tefca/reports/quarterly` | per-route (dashboard router has no router-level guard) | Generate a quarterly report (SOW Task 4) |
| POST | `/api/tefca/reports/weekly` | per-route (dashboard router has no router-level guard) | Generate a weekly progress report (SOW Task 3) |
| GET | `/api/tefca/reports/{report_id}` | per-route (dashboard router has no router-level guard) | Full report detail |
| GET | `/api/tefca/reports/{report_id}/csv` | per-route (dashboard router has no router-level guard) | Download report as 12-column CSV |
| GET | `/api/tefca/reports/{report_id}/docx` | per-route (dashboard router has no router-level guard) | Download report as branded DOCX |
| GET | `/api/tefca/reports/{report_id}/download` | per-route (dashboard router has no router-level guard) | Download a report (pdf\|docx) |
| GET | `/api/tefca/reports/{report_id}/pdf` | per-route (dashboard router has no router-level guard) | Download report as branded PDF |
| GET | `/api/tefca/reviews` | per-route (dashboard router has no router-level guard) | List entity reviews (filters: status, qhin) |
| GET | `/api/tefca/reviews/new-submissions` | per-route (dashboard router has no router-level guard) | List new submissions since a date |
| POST | `/api/tefca/reviews/run-sample` | per-route (dashboard router has no router-level guard) | Compute + record a stratified sampling run |
| GET | `/api/tefca/reviews/{review_id}` | per-route (dashboard router has no router-level guard) | Single review detail with evidence |
| POST | `/api/tefca/reviews/{review_id}/execute` | per-route (dashboard router has no router-level guard) | Execute a review against live connectors |
| GET | `/api/tefca/sampling-runs` | per-route (dashboard router has no router-level guard) | List sampling runs |
| GET | `/api/tefca/search` | per-route (dashboard router has no router-level guard) | Global entity search (NPI, name, QHIN) with live NPPES lookup |
| GET | `/api/tefca/status` | public (documented as monitoring) | Module status + data provenance (public) |

## Case Management (22)

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/api/v1/case-management/billing/cpt-reference` | authenticated (verified: 401 anonymous) | CPT code reference guide |
| POST | `/api/v1/case-management/billing/determine-code` | authenticated (verified: 401 anonymous) | Determine appropriate CPT billing code |
| GET | `/api/v1/case-management/billing/monthly-summary` | authenticated (verified: 401 anonymous) | Monthly billing summary by patient |
| GET | `/api/v1/case-management/care-plans` | authenticated (verified: 401 anonymous) | List care plans |
| POST | `/api/v1/case-management/care-plans/generate` | authenticated (verified: 401 anonymous) | Generate comprehensive care plan |
| GET | `/api/v1/case-management/dashboard/stats` | authenticated (verified: 401 anonymous) | Case management dashboard statistics |
| POST | `/api/v1/case-management/discharge/generate` | authenticated (verified: 401 anonymous) | Generate Joint Commission compliant discharge summary |
| POST | `/api/v1/case-management/education/generate` | authenticated (verified: 401 anonymous) | Generate patient education materials |
| GET | `/api/v1/case-management/education/topics` | authenticated (verified: 401 anonymous) | Available education topic templates |
| GET | `/api/v1/case-management/government/cases` | authenticated (verified: 401 anonymous) | List government cases |
| POST | `/api/v1/case-management/government/cases/generate` | authenticated (verified: 401 anonymous) | Generate government case document |
| GET | `/api/v1/case-management/info` | authenticated (verified: 401 anonymous) | Case Management module information |
| POST | `/api/v1/case-management/meetings/generate-minutes` | authenticated (verified: 401 anonymous) | Generate care team meeting minutes from transcript |
| GET | `/api/v1/case-management/notes` | authenticated (verified: 401 anonymous) | List case management notes |
| POST | `/api/v1/case-management/notes/generate` | authenticated (verified: 401 anonymous) | Generate CCM note from structured input |
| POST | `/api/v1/case-management/notes/tcm` | authenticated (verified: 401 anonymous) | Generate TCM note — Transitional Care Management |
| POST | `/api/v1/case-management/notes/voice-to-note` | authenticated (verified: 401 anonymous) | Voice transcript → billable CCM note (Core WOW Feature) |
| PATCH | `/api/v1/case-management/notes/{note_id}/approve` | authenticated (verified: 401 anonymous) | Approve and sign case management note |
| GET | `/api/v1/case-management/patients` | authenticated (verified: 401 anonymous) | List case management patients |
| POST | `/api/v1/case-management/patients` | authenticated (verified: 401 anonymous) | Create case management patient |
| GET | `/api/v1/case-management/patients/{patient_id}` | authenticated (verified: 401 anonymous) | Get patient details |
| POST | `/api/v1/case-management/sdoh/assess` | authenticated (verified: 401 anonymous) | Generate SDOH assessment narrative |

## Enterprise (decisions, audit, actions) (7)

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/api/enterprise/audit` | authenticated (`get_current_user`, no router-level guard) | Get Audit Log |
| GET | `/api/enterprise/audit/entity/{entity_id}` | authenticated (`get_current_user`, no router-level guard) | Get Entity Audit |
| GET | `/api/enterprise/decisions` | authenticated (`get_current_user`, no router-level guard) | List Decisions |
| GET | `/api/enterprise/decisions/{decision_id}` | authenticated (`get_current_user`, no router-level guard) | Get Decision |
| POST | `/api/enterprise/decisions/{decision_id}/approve` | authenticated (`get_current_user`, no router-level guard) | Approve Decision Endpoint |
| POST | `/api/enterprise/decisions/{decision_id}/reject` | authenticated (`get_current_user`, no router-level guard) | Reject Decision Endpoint |
| POST | `/api/enterprise/decisions/{decision_id}/review` | authenticated (`get_current_user`, no router-level guard) | Send For Review |

## Decision Intelligence (15)

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/api/decisions/bank` | per-route | List Decisions |
| POST | `/api/decisions/bank/create` | per-route | Create Decision |
| GET | `/api/decisions/bank/{decision_id}` | per-route | Get Decision |
| POST | `/api/decisions/bank/{decision_id}/stakeholder` | per-route | Update Stakeholder |
| POST | `/api/decisions/defensibility` | per-route | Generate Defensibility Packet |
| POST | `/api/decisions/extract/{output_id}` | per-route | Extract Decisions From Output |
| POST | `/api/decisions/feedback` | per-route | Submit Feedback |
| GET | `/api/decisions/feedback/stats` | per-route | Get Feedback Stats |
| GET | `/api/decisions/feedback/{output_id}` | per-route | Get Output Feedback |
| GET | `/api/decisions/memory` | per-route | Get Institutional Memory |
| GET | `/api/decisions/provenance/{output_id}` | per-route | Get Provenance |
| GET | `/api/decisions/status` | per-route | Decision Intel Status |
| GET | `/api/sla/decision/{decision_id}` | per-route | Get Decision Sla |
| GET | `/api/sla/history/{decision_id}` | per-route | Get Decision History |
| GET | `/api/sla/outcome/{decision_id}` | per-route | Get Decision Outcome |

## Absent by design or omission

These were looked for and do not exist. Listed so the map is usable as a negative reference, not just a positive one.

| Expected route | Status |
|----------------|--------|
| `POST /api/tefca/registry/entities` | Absent - entities are created only via import |
| `PUT|PATCH /api/tefca/registry/entities/{id}` | Absent - registry has no mutation route |
| `PUT /api/tefca/registry/entities/{id}/status` | Absent - state machine not exposed |
| `DELETE on any audit path` | Absent - audit is append-only by omission |
| `PUT|PATCH /api/enterprise/decisions/{id}` | Absent - decisions immutable by omission |
| `GET /api/v1/case-management/cases` | Absent - `/government/cases` is the case list |
