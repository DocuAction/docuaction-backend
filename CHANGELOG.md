# Changelog

All notable changes to the **DocuAction AI** backend are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Maintained by **Alliance Global Tech, Inc. ("AGT")**.
Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.

---

## [Unreleased]

### Added
- Placeholder for changes staged for the next release.

### Changed
- Source-derived name columns widened from `VARCHAR(500)` to `TEXT` (migration `20260915_curated_text_columns`): `rce_curated_records.name`, `tefca_reg_entities.name` / `display_name`, `tefca_entity_contacts.company` / `name`. A delivered organisation name longer than 500 characters failed the whole CURATION stage of a DEV delivery (`StringDataRightTruncationError`) although Area 1 held it unbounded; the curated and registry projections are 1:1 copies of the delivered value and no contractual maximum exists for it. Catalogue-only change, no row touched; the downgrade refuses to run while any value longer than 500 characters exists rather than truncate. Identifier, code, status and address-component limits are unchanged. Regression: `tests/test_curated_text_columns.py`.

### Fixed
- _None yet._

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
