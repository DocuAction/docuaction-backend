# OWASP ASVS v4.0 - Verification Matrix

**Project:** DocuAction  
**Scan:** `docuaction_20260728T003746`  
**Findings analysed:** 309  
**Generated:** 2026-07-28T01:18:14+00:00

> **How to read this.** A control is marked **GAP** only where a finding demonstrates a deficiency, and **EVIDENCED** only where a passing test or an observed configuration supports it. Everything else is **NOT ASSESSED** - an automated scan that finds nothing is not evidence that a control is satisfied. This document supports an assessment; it is not a certification.


| Chapter | Area | Requirements referenced | Findings | Severity | Status |
|---|---|--:|--:|---|---|
| V1 | Architecture, Design and Threat Modeling | 0 | 0 | - | NOT ASSESSED |
| V2 | Authentication | 1 | 16 | C6 H10 | **NOT MET** (findings present) |
| V3 | Session Management | 1 | 7 | M7 | **NOT MET** (findings present) |
| V4 | Access Control | 2 | 73 | H73 | **NOT MET** (findings present) |
| V5 | Validation, Sanitization and Encoding | 3 | 8 | H4 M4 | **NOT MET** (findings present) |
| V6 | Stored Cryptography | 1 | 1 | M1 | **NOT MET** (findings present) |
| V7 | Error Handling and Logging | 1 | 2 | H2 | **NOT MET** (findings present) |
| V8 | Data Protection | 0 | 0 | - | NOT ASSESSED |
| V9 | Communication | 0 | 0 | - | NOT ASSESSED |
| V10 | Malicious Code | 0 | 0 | - | NOT ASSESSED |
| V11 | Business Logic | 0 | 0 | - | NOT ASSESSED |
| V12 | Files and Resources | 1 | 8 | M8 | **NOT MET** (findings present) |
| V13 | API and Web Service | 0 | 0 | - | NOT ASSESSED |
| V14 | Configuration | 2 | 30 | C1 H27 M1 L1 | **NOT MET** (findings present) |

## Requirements referenced by findings

| ASVS requirement | Findings | Example |
|---|--:|---|
| FedRAMP-Moderate | 27 | `AZ-DB-006-docuaction-db-dev` [dev] Public network access |
| V12.3.1 | 8 | `AGT-PATH-001` open() called with a non-literal path |
| V14.2.1 | 27 | `1124170` next: Next.js: Middleware / Proxy bypass in App Router appli |
| V14.3.2 | 3 | `openai-api-key` OpenAI API key in .env |
| V2.10.4 | 16 | `AGT-AZ-001` Hardcoded Azure/DB connection string |
| V3.3.1 | 7 | `AGT-JWT-003` JWT encoded without an expiration claim |
| V4.1.1 | 72 | `AGT-AUTHZ-001` Endpoint 'GET /saml/config' has no authentication dependency |
| V4.2.1 | 1 | `AGT-FHIR-001` FHIR resource route without an obvious access control check |
| V5.1.4 | 1 | `FHIR-ID-002` Backend validates the NPI check digit (Luhn/80840) |
| V5.3.3 | 3 | `AGT-XSS-001` HTML response built from an f-string without escaping |
| V5.3.4 | 4 | `AGT-SQL-002` SQL statement built with an f-string or concatenation |
| V6.2.2 | 1 | `AGT-CRYPTO-001` Weak hash algorithm used |
| V7.1.1 | 2 | `AGT-PHI-001` Potential PHI written to application logs without masking |

**ASVS level.** No target level (L1/L2/L3) has been agreed. For a healthcare application processing ePHI, **L2** is the normal baseline; this matrix reports what the current ruleset touches, not conformance to a level.
