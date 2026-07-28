# Data Protection Review

> Manual review of PHI/PII handling in logs, error responses, external egress, and URL parameters. Read-only. Cross-references Part 10 (`phi_data_flow.md`).

## DP-01 — PHI/PII in logs — mostly clean, minor (Low, CWE-532)
- **No SSN / patient-name / MRN / DOB logging** found (grep of log statements returned no PHI values).
- **Minor leaks:** emails logged at `password_reset.py:182` (`RESET LINK GENERATED | email=…`), `azure_auth_routes.py:222`, `migration_routes.py:91`; **NPIs** logged at `connectors.py:300,611`. Email is PII (not PHI); NPI is a provider identifier. Low sensitivity but avoidable. **Fix:** mask/omit email + NPI in info-level logs. Effort: 0.5d.

## DP-02 — HIGH: unauthenticated + unmasked PHI sent to Anthropic (High, CWE-200, OWASP A04/A01)
Two-part finding:
1. **Main document pipeline masks first** — `ai_engine.py:251` `masked_text, pii_count = mask_pii(document_text)`; only `masked_text` is sent (`:311,369`). Good minimization intent. **But** `pii_masking.py:15-49` is **regex/keyword-only**: it redacts SSN, credit card, phone, email, DOB-**with-label**, MRN-**with-label** — and does **NOT redact bare patient/person names, street addresses, or unlabeled dates**. So clinical-narrative **names still reach Anthropic**. **Medium on this path.**
2. **Case Management engines send PHI with NO masking at all** — `case_management/services/ccm_engine.py:25,164` and `discharge_engine.py:19,33` POST clinical text (names, MRN, DOB, diagnoses) **directly** to `https://api.anthropic.com/v1/messages`; neither imports `mask_pii`. Combined with the **unauthenticated** router (AUTHZ-01), this is a live path where **anyone on the internet can drive PHI to a third party**. **High** (part of the Critical in AUTHZ-01).

**BAA context:** no BAA reference is enforced in code. Anthropic offers a BAA/zero-retention path for HIPAA workloads — **required** before any PHI egress. Docs exist (`docs/compliance/hipaa-safeguards.md`) but there is no code-level BAA gate.

**Fix:** (1) require auth on case-management; (2) run `mask_pii` (expanded to cover names/addresses) before **any** external call; (3) confirm a signed Anthropic BAA + zero-retention. Effort: 1–2d (auth+masking) + BAA (process).

## DP-03 — PHI in URL query strings (Medium, CWE-598)
`healthcare_claims_routes.py` `process-text` passes clinical `text` as a **query string** (`:100`) and `provider_name` via query. PHI in query strings lands in **access logs, proxy logs, and browser history**. (Case-management correctly uses request bodies.) **Fix:** move PHI to request bodies. Effort: 0.5d.

## DP-04 — Error responses — GOOD (Info)
`core/error_handler.py` returns generic `{error, code, request_id}`; 5xx detail is replaced with a generic message (`:90-95`), stack traces logged **internally only**. Some endpoints raise `HTTPException(500, f"...{str(e)}")` (`routes.py:468,564,639`; `healthcare_claims_routes.py:59`), but the handler **scrubs** those detail strings before they reach the client. **No PHI or stack-trace leakage to users.** CWE-209/CWE-497: mitigated.

## DP-05 — No role-based PHI masking on reads (Gap, cross-ref Part 10 §17)
`pii_masking.py` masks only on the **AI egress path**. There is **no masking on API read responses** — a `reviewer` reading `fhir_resource` or a document gets full unmasked data. `api/security.py:83` reports `"pii_masking_active": True`, but that flag reflects only AI-pipeline masking, not response-level masking for lower-privileged roles. **Fix:** role-aware field masking on PHI-bearing read responses. Effort: 2–3d.

## Data classification (from Part 2 + this review)
- **Highest PHI concentration:** `documents` (arbitrary clinical uploads), `outputs.content` (AI output), `transcripts.full_text`, `audio_files`, and `case_management` clinical data.
- **PHI in JSONB** (`case_management`: insurance, ICD-10, HCC, SDOH) — un-GIN-indexed (perf) and unmasked (this review).
- **Provider PII (not patient PHI):** TEFCA `fhir_resource` (Organization JSON), identifiers (NPI/CCN/CLIA), entity addresses.

## Verdict
Error-handling and log hygiene are **good**; the serious items are **egress** (unauthenticated + weakly-masked PHI to Anthropic — DP-02) and **PHI in query strings** (DP-03), plus the absence of **read-time role masking** (DP-05). OWASP **A04 (Insecure Design)** and **A02** both draw on these. DP-02 is the second-highest-priority finding in the whole security review after the auth gap it rides on.

## NIST mapping
SC-8 (transmission confidentiality) ◐, AU-3 (audit content — email/NPI in logs) ◐, SI-11 (error handling) ✅, AC-4/SC-28 (PHI flow/masking) ◐.
