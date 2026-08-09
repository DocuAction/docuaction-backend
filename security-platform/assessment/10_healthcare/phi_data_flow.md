# PHI Data Flow

> Where PHI enters, is stored, and is transmitted — with the minimization/masking posture at each hop. Static review. Read-only. Cross-references Part 8 `data_protection_review.md`.

## 1. Where PHI ENTERS
- **Document / audio uploads** — `POST /api/documents`, audio routes (`api/routes.py`) → `Document`/`AudioFile` (`models/database.py:48,101`). **Free-text clinical uploads are the primary PHI ingress.**
- **Case Management** — `app/case_management/routes.py` accepts patient name/MRN/DOB/diagnoses and clinical narratives in request bodies (`:189` onward). **Unauthenticated** (Part 8 AUTHZ-01). Patient CRUD is currently non-persisting stubs, but the AI-generation endpoints ingest real PHI.
- **TEFCA imports** (`tefca_registry/routes.py:215-262`) — FHIR/CSV of **provider-organization** data (NPI/TEFCAID/HCID/addresses). Org identifiers + contact PII, generally **not patient PHI**.
- No dedicated patient-record persistence endpoints in the TEFCA registry (entities are organizations, not patients).

## 2. Where PHI is STORED
| Location | Content | Sensitivity |
|---|---|---|
| `documents.file_path` + extracted text | arbitrary clinical uploads | **Highest PHI** |
| `outputs.content` (`models/database.py:78`) | AI output on clinical docs | High PHI |
| `transcripts.full_text` (`:123`), `audio_files` | clinical transcripts/audio | High PHI |
| `case_management` JSONB (`case_management/models.py:98-152`) | `insurance_primary/secondary`, `diagnoses_icd10`, `hcc_codes`, `sdoh_flags`, `care_plan_updates`, `address` | **PHI in un-GIN-indexed JSONB** (also Part 7) |
| `audit_logs.details` / `tefca_reg_audit_log.metadata` | may incidentally carry identifiers | Medium |
| TEFCA `fhir_resource` JSONB (`tefca_registry/models.py:54`), identifiers, entity address | Organization FHIR JSON, NPI/CCN/CLIA | **Provider PII, not patient PHI** |

**At-rest protection:** platform-level (Azure transparent encryption) only — no field-level encryption on PHI columns (Part 8 crypto).

## 3. Where PHI is TRANSMITTED
| Destination | Data sent | TLS | Minimized? |
|---|---|:--:|---|
| NPPES (`connectors.py:243`), LEIE (`:350`), SAM (`:459`), PECOS (`:558`), RCE/ONC (`:638`), IQVIA (`:760`) | **provider** NPI/UEI/name/address only | ✅ HTTPS | N/A (no patient PHI) |
| **Anthropic** (`ai_engine.py:469`) — main doc pipeline | document text, **masked** (`mask_pii`, :251) | ✅ HTTPS | ◐ **partial — misses names/addresses** |
| **Anthropic** — case-management engines (`ccm_engine.py:25,164`, `discharge_engine.py:19,33`) | clinical text (**names, MRN, DOB, diagnoses**) | ✅ HTTPS | ❌ **NOT masked, NOT authenticated** |

## 4. PHI → AI APIs (the top compliance item) — **Partial → Gap**
- **Main pipeline masks** but `pii_masking.py:15-49` is **regex/keyword-only**: SSN, credit card, phone, email, DOB-**with-label**, MRN-**with-label**. It does **NOT** redact bare **patient names, street addresses, or unlabeled dates** → names reach the model. **Partial minimization.**
- **Case-management engines send PHI with no masking at all** and behind **no auth** → a live path where an anonymous caller drives PHI to a third party (Part 8 DP-02, part of the AUTHZ-01 Critical). **Gap.**
- **BAA:** no BAA gate in code. Anthropic offers a BAA + zero-retention path — **required before any PHI egress.** Compliance docs exist (`docs/compliance/hipaa-safeguards.md`) but no code enforcement.

## 5. PHI in logs — **Compliant (low risk)**
No SSN/patient-name/MRN/DOB values logged (grep clean). Logs record counts/metadata (`ai_engine.py:252` logs `{pii_count} items redacted`). Minor: email on hard-delete (`compliance.py:154-157`, intentional forensic) + NPI in connector logs (Part 8 DP-01) — PII, not PHI.

## 6. PHI in error messages — **Compliant**
`core/error_handler.py` scrubs 5xx detail + stack traces before responses (Part 8 DP-04). No PHI leakage to clients.

## 7. PHI masking for unauthorized roles — **Gap**
Masking is applied only on the **AI egress path**, not on **API read responses**. A `reviewer` reading `fhir_resource`/documents gets full unmasked data. `api/security.py:83` `"pii_masking_active": True` reflects only AI-pipeline masking (Part 8 DP-05). **Gap.**

## Data-flow verdict
The **provider-data flows are clean** (org identifiers only, all TLS). The **patient-PHI flows carry the risk**: the top item is **unauthenticated + unmasked PHI egress to Anthropic** (DP-02 / AUTHZ-01), compounded by **weak name/address masking** even on the authenticated pipeline and **no read-time role masking**. Log/error hygiene is good. **Priorities:** (1) auth + mask before any external call, (2) sign an Anthropic BAA + zero-retention, (3) strengthen `mask_pii` to cover names/addresses, (4) add role-based response masking, (5) GIN-index + consider field-encrypting the case-management PHI JSONB.

## Status
| Item | Status |
|---|---|
| PHI ingress mapped | ✅ |
| PHI storage mapped | ✅ (PHI in un-indexed JSONB noted) |
| Provider egress (connectors) | ✅ Compliant (TLS, no patient PHI) |
| PHI → AI minimization | ◐/❌ Partial (main) / Gap (case-mgmt) |
| PHI in logs | ✅ Compliant |
| PHI in errors | ✅ Compliant |
| Role-based read masking | ❌ Gap |
| BAA enforcement | ❌ Gap (docs only) |
