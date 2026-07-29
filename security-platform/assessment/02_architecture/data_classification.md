# Data Classification (Section 2C)

Classification scheme (highest wins per table):
**PUBLIC < INTERNAL < CONFIDENTIAL < PII < FINANCIAL < CUI < PHI < AUTHENTICATION < SECRETS**

## Classification definitions & examples

| Class | Definition | DocuAction examples |
|---|---|---|
| PUBLIC | Freely shareable | QHIN names, designation dates, `/health`, jurisdiction reference, bulletin briefings |
| INTERNAL | Internal use | platform_config (modules, pages, themes, data_sources), scan results, templates |
| CONFIDENTIAL | Business-sensitive | review decisions, verification findings, audit metadata, analytics |
| PII | Personally identifiable | user names/emails/phones, candidate/employee records, contact data |
| FINANCIAL | Financial | pricing, quotes, invoices, expenses, payment terms |
| CUI | Controlled Unclassified | TEFCA entity review data, ONC deliverables, evidence records |
| PHI | Protected Health Info | **NPI**, patient records (cm_*), claims, uploaded clinical docs/audio |
| AUTHENTICATION | Credentials | password hashes, JWTs, tokens_revoked_at |
| SECRETS | Infra secrets | DB connection string, API keys, Key Vault secrets |

## Per-table highest classification (materialized tables + key declared)

| Table | Highest class | Sensitive columns |
|---|---|---|
| `users` | **AUTHENTICATION / PII** | `password_hash`, `email`, `full_name`, `tokens_revoked_at`, `role` |
| `audit_logs`, `tefca_reg_audit_log`, `state_audit_log` | CONFIDENTIAL (may embed PII/PHI in `details`/`metadata`) | `details`, `metadata_`, `actor_email`, `ip_address` |
| `documents` | **PHI-capable** / PII | `file_path`, `checksum_sha256`, content (uploaded) |
| `audio_files`, `transcripts` | **PHI-capable** | audio content, `full_text` |
| `tefca_reg_entities`, `tefca_entities` | **CUI / PHI (NPI)** | `fhir_resource`, `address`, identifiers |
| `tefca_entity_identifiers` | **PHI** | `identifier_value` (NPI/TEFCAID/HCID/CCN/CLIA) |
| `tefca_evidence_records`, `tefca_reviews`, `tefca_findings`, `tefca_entity_findings` | **CUI** | review/verification detail, evidence JSONB |
| `tefca_source_cache`, `tefca_verification_checks` | CUI/CONFIDENTIAL | `response_data` (NPPES/LEIE/SAM responses → may contain provider PII/PHI) |
| `cm_patients`, `cm_care_plans`, `cm_notes`, `cm_discharge_records`, `cm_government_cases`, `cm_billing_summaries` | **PHI** (declared; not in DB) | patient identifiers, clinical notes, diagnoses |
| healthcare claims tables | **PHI** | claim/diagnosis data |
| `candidates`, `employees`, `applications`, `submissions`, `bench_candidates` | **PII** (declared; not in DB) | names, emails, resumes, clearance, pay |
| `customers`, `suppliers`, `agency_contacts`, `company_profiles` | PII / CONFIDENTIAL | contacts, CAGE/UEI/DUNS |
| `quotes`, `invoices`, `financials`, `expenses`, `price_history`, `purchase_orders` | **FINANCIAL** (declared; not in DB) | prices, margins, payment |
| `platform_*` | INTERNAL / PUBLIC | config only |
| `migration_*` | INTERNAL / PII-capable (source data) | schemas/mappings of arbitrary customer data |

## Endpoints handling sensitive data (representative)

| Endpoint | Input class | Output class | Protected? |
|---|---|---|---|
| `POST /api/auth/login` | AUTHENTICATION | AUTHENTICATION (JWT) | ✅ public-by-design, throttled |
| `POST /api/tefca/registry/entities/{id}/verify` | CUI/PHI (NPI) | CUI (findings) | ✅ `require_role("reviewer")` |
| `POST /api/tefca/registry/import/fhir-bundle` | **PHI/CUI (FHIR)** | CONFIDENTIAL (batch) | ✅ reviewer + upload scanner |
| `GET /api/tefca/registry/entities/{id}` | — | **CUI/PHI (identifiers, FHIR)** | ✅ reviewer |
| `POST /documents` (upload) | **PHI-capable** | PII | ✅ authed + scanner (⚠ verify PHI-in-AI) |
| `POST /suppliers`, `/quotes`, `/rfq` | PII/FINANCIAL | PII/FINANCIAL | ❌ **UNAUTHENTICATED** (dormant tables) |
| `GET /api/security/residency`,`/status` | — | INTERNAL/CONFIDENTIAL | ⚠ unauthenticated — verify disclosure |

## Key data-protection findings (→ Part 8/10)
1. **PHI concentrates in:** identifiers (NPI), documents, audio/transcripts, `tefca_source_cache.response_data`, and the (undeployed) `cm_*`/healthcare tables. **No app-level field encryption or role-based masking of PHI observed.**
2. **Audit tables can embed PII/PHI** in `details`/`metadata`/`response_data` JSONB — confirm these don't over-log sensitive values (Part 8).
3. **SECRETS**: DB connection string is an app setting (not KV ref) — see infra findings.
