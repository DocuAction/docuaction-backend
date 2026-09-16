# Changelog

All notable changes to the **DocuAction AI** backend are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Maintained by **Alliance Global Tech, Inc. ("AGT")**.
Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.

---

## [Unreleased]

### Added
- **Delivery Processing Report** (`report_type=delivery_processing`, Lane R of the 2026-09-17 delivery-workflow remediation). One report per named delivery, built from persisted evidence only: page-one identity block (delivery, job, intake, file, SHA-256, size, registrant, timestamps, build SHA, migration revision, reconciliation snapshot id + hash, template version, generated at), processing outcome and review state with basis, stage timeline, the reconciliation equation from the **pinned** snapshot, disposition totals and the record-level table, findings by code/severity and rows, identifier conflicts and decisions, verification coverage per source, analyst actions, lineage, an always-present evidence-limitations list and an audit note. Data service `app/reports/data/delivery_processing_data.py`; template `app/reports/templates/delivery_processing.html`; CSV is the record-level dispositions table (every row); totals agree across HTML, CSV and dataset (`tests/test_delivery_processing_report.py`).
- **Delivery linkage and audit for reports.** Generating a delivery-scoped report writes an `audit_logs` row first (`action=report_generated`, `event_type=reporting`, `resource_type=report`, correlation id from the request context, details {job_id, intake_id, snapshot_id, template_version, build_sha}), then an `rce_delivery_report_links` row pointing at it (job, intake, snapshot, report, artifact when registered, template version, generated_by, build SHA, correlation id), then a `REPORT_GENERATION` stage event on the job. Every download (`/html`, `/pdf`, `/csv`, `/docx`, `/package`, artifact download) writes `report_downloaded`. `GET /api/reports/{id}` returns `delivery_link`; new `GET /api/reports/by-delivery/{job_id}` (viewer) lists a job's reports (`app/reports/data/delivery_report_links.py`, `tests/test_report_links.py`).
- **Snapshot-pinned regeneration.** `parameters.snapshot_id` re-renders from that persisted reconciliation snapshot (it must belong to the delivery, else 422 `SNAPSHOT_NOT_FOR_JOB`); the response and the dataset state `snapshot_id` / `snapshot_created_at`; a report never silently adopts newer live data.
- **Historical reconstruction tool** `scripts/dryrun_reconstruct_dispositions.py`: read-only by default (`postgresql_readonly` + `SET TRANSACTION READ ONLY`), proposes a disposition per source record from Area 1 / Area 2 / registry rows and classifies every proposed field as `directly_evidenced | deterministically_recomputed | inferred | unavailable`; `--write` refuses without `--approved-by` and `--approval-ref`, against non-local hosts without `--allow-shared`, while any proposal is unavailable, or while the ledger is not empty; written rows would carry `reconstructed=true` and a `reconstruction` payload plus a `RECONSTRUCTION` snapshot. Policy: `docs/rce/HISTORICAL_RECONSTRUCTION_POLICY.md`. No write was executed anywhere.
- **Migration proof** `tests/test_traceability_migration.py`: on a throwaway database `20260917_delivery_traceability` upgrades from empty, downgrades while empty, refuses to downgrade once evidence exists (`DowngradeWouldDestroyEvidenceError`), lets the runtime role INSERT but not UPDATE/DELETE/TRUNCATE, returns the highest sequence from `rce_current_dispositions`, rejects a passing snapshot whose counts do not sum (`ck_rce_snapshot_equation`), and matches the contract's grant matrix.
- Docs: `docs/rce/DELIVERY_WORKFLOW_USER_GUIDE.md` (the 20-step end-to-end guide and "where to go for"), `docs/observability/DELIVERY_OBSERVABILITY.md` (structured log fields, correlation model, KQL for App Service diagnostic tables and App Insights, sampling/retention/redaction/cost, alert thresholds, the Azure operator steps that were **not** executed).

### Changed
- **Every RCE report type requires a named delivery.** `data_quality`, `intake` and `delivery_processing` need `parameters.job_id` or `parameters.intake_id`; a job id resolves to its intake through `rce_delivery_jobs.source_intake_id`. Missing → `ReportParameterError` → HTTP 422 `{error, code: "DELIVERY_IDENTIFIER_REQUIRED"}` (404 `DELIVERY_NOT_FOUND`, 409 `DELIVERY_AMBIGUOUS`, 422 `DELIVERY_IDENTIFIER_MISMATCH` / `SNAPSHOT_NOT_FOR_JOB`). The newest-intake default in `rce_report_data.RceReportDataService._intake` is removed.
- Convergence utility and chain tests pinned to head `20260917_delivery_traceability`: the five traceability tables are chain-created (`MANAGED_CHAIN_CREATES`), stay owned by `docuaction_owner` after FINALIZE (`AREA1_OWNER_TABLES`; the app holds only the chain's append-only grants), the managed gate no longer counts chain-created tables as missing candidates, and MANAGED PREPARE grants `REFERENCES` to the owner role on `rce_issues`, `tefca_entity_versions`, `audit_logs` (the app-owned tables the new foreign keys point at). Both integration suites pass on a pristine PostgreSQL 18 cluster (legacy 6/6, managed 3/3). `20260917_delivery_traceability.py` added to `GUARDED_REVISIONS`.
- Source-derived name columns widened from `VARCHAR(500)` to `TEXT` (migration `20260915_curated_text_columns`): `rce_curated_records.name`, `tefca_reg_entities.name` / `display_name`, `tefca_entity_contacts.company` / `name`. A delivered organisation name longer than 500 characters failed the whole CURATION stage of a DEV delivery (`StringDataRightTruncationError`) although Area 1 held it unbounded; the curated and registry projections are 1:1 copies of the delivered value and no contractual maximum exists for it. Catalogue-only change, no row touched; the downgrade refuses to run while any value longer than 500 characters exists rather than truncate. Identifier, code, status and address-component limits are unchanged. Regression: `tests/test_curated_text_columns.py`.

### Fixed
- `dispositions.current_for_intake`: the optional `disposition` filter bound an untyped NULL parameter (`:disp IS NULL OR d.disposition = :disp`), which asyncpg rejects with `AmbiguousParameterError` on every unfiltered call; both sides are now `CAST(:disp AS text)`.

### Security
- _None yet._

---

## [6.0.0] — 2026

Major platform release: migration to Microsoft Azure, enterprise authentication
hardening, and the general availability of the DocuAction TEFCA ARC healthcare
module suite.

### Added
- **Microsoft Azure hosting.** Backend now runs on Azure App Service (Linux,
  Python 3.12, gunicorn with uvicorn workers), fronted by the production domain
  `api-prod.docuaction.io`.
- **Azure Database for PostgreSQL Flexible Server** as the managed primary
  datastore, replacing the previous Railway-hosted database.
- **Microsoft Entra ID SSO** (OAuth 2.0 authorization-code flow, confidential
  client) as an authentication method alongside password sign-in. Both methods
  issue the same JWT access + refresh token pair (HS256) and receive identical
  downstream authorization.
- **DocuAction TEFCA ARC** healthcare module suite, including the TEFCA Review
  Protocol, validation engine, case management, and decision intelligence.
- **TEFCA connectors** for provider and sanction data: NPPES (live), PECOS
  (live), and OIG LEIE (live); SAM.gov (API key required); TEFCA entity data /
  ONC and IQVIA OneKey (pending).
- **Bulletin Intelligence** module with scheduled collection and delivery via
  APScheduler.
- Additional platform modules: Documents, Audio (OpenAI Whisper transcription),
  Healthcare Claims, Data Systems, Comparison, Extraction, Automation, plus
  enterprise, export, templates, meetings, SLA, and plans capabilities —
  spanning approximately 261 API endpoints.

### Changed
- **Migrated off Railway.** All hosting, networking, and database workloads were
  moved to Microsoft Azure; Railway-specific deployment configuration has been
  retired.
- Consolidated authentication and authorization on the JWT + Entra SSO model
  with an 8-level RBAC hierarchy (viewer, contributor, manager, reviewer,
  senior_analyst, qalead, program_manager, admin). TEFCA contract roles align to
  HHSAR 352.204-71 / FAR 52.212-4.
- Centralized application error handling for consistent, non-leaking error
  responses across the API surface.

### Fixed
- Corrected TEFCA NPPES active-status handling by sharing a single active-status
  constant across the validation engine, eliminating false `NPI_INACTIVE`
  results.
- Resolved scheduler event-loop handling so scheduled Bulletin jobs run reliably
  under the gunicorn/uvicorn worker model.

### Security
- **Authentication hardening:** JWT revocation support, session controls, bcrypt
  password hashing, and an administrator approval step for new accounts.
- **Microsoft Defender for Cloud (Standard tier)** enabled across the Azure
  estate for continuous posture management and threat protection.
- **Upload safety:** enforced content-type and size constraints on file uploads.
- **Global rate limiting** applied across the API to mitigate abuse and
  resource-exhaustion.
- **TrustedHost and CORS hardening:** strict allowed-host enforcement and
  tightened cross-origin policy; required security configuration
  (`SECRET_KEY`, `DATABASE_URL`) is now mandatory at startup.

---

[Unreleased]: https://github.com/
[6.0.0]: https://github.com/
