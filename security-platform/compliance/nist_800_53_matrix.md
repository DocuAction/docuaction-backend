# NIST SP 800-53 Rev. 5 - Control Matrix

**Project:** DocuAction  
**Scan:** `docuaction_20260728T003746`  
**Findings analysed:** 309  
**Generated:** 2026-07-28T01:18:14+00:00

> **How to read this.** A control is marked **GAP** only where a finding demonstrates a deficiency, and **EVIDENCED** only where a passing test or an observed configuration supports it. Everything else is **NOT ASSESSED** - an automated scan that finds nothing is not evidence that a control is satisfied. This document supports an assessment; it is not a certification.


## Coverage by control family

| Family | Name | Controls with findings | Findings | Severity | Status |
|---|---|--:|--:|---|---|
| AC | Access Control | 3 | 97 | H77 M20 | **GAP** |
| AT | Awareness and Training | 0 | 0 | - | NOT ASSESSABLE by scanner |
| AU | Audit and Accountability | 5 | 8 | H2 M6 | **GAP** |
| CA | Assessment, Authorization, Monitoring | 0 | 0 | - | NOT ASSESSED |
| CM | Configuration Management | 3 | 40 | H33 M3 L4 | **GAP** |
| CP | Contingency Planning | 4 | 10 | M8 L2 | **GAP** |
| IA | Identification and Authentication | 2 | 103 | C6 H82 L15 | **GAP** |
| IR | Incident Response | 0 | 0 | - | NOT ASSESSED |
| MA | Maintenance | 0 | 0 | - | NOT ASSESSABLE by scanner |
| MP | Media Protection | 0 | 0 | - | NOT ASSESSABLE by scanner |
| PE | Physical and Environmental | 0 | 0 | - | NOT ASSESSABLE by scanner |
| PL | Planning | 0 | 0 | - | NOT ASSESSABLE by scanner |
| PM | Program Management | 0 | 0 | - | NOT ASSESSABLE by scanner |
| PS | Personnel Security | 0 | 0 | - | NOT ASSESSABLE by scanner |
| RA | Risk Assessment | 1 | 27 | H25 M1 L1 | **GAP** |
| SA | System and Services Acquisition | 0 | 0 | - | NOT ASSESSED |
| SC | System and Communications Protection | 7 | 41 | C6 H6 M5 L22 I2 | **GAP** |
| SI | System and Information Integrity | 4 | 144 | H30 M14 L100 | **GAP** |
| SR | Supply Chain Risk Management | 0 | 0 | - | NOT ASSESSED |

## Controls with findings

| Control | Findings | Severity | Example |
|---|--:|---|---|
| **AC-12** | 7 | M7 | `AGT-JWT-003` JWT encoded without an expiration claim |
| **AC-3** | 85 | H74 M11 | `AGT-AUTHZ-001` Endpoint 'GET /saml/config' has no authentication dependency |
| **AC-4** | 6 | H4 M2 | `AGT-FHIR-001` FHIR resource route without an obvious access control check |
| **AU-12** | 5 | M5 | `AZ-DB-009-docuaction-db-dev` [dev] Database audit logging |
| **AU-2** | 5 | M5 | `AZ-DB-009-docuaction-db-dev` [dev] Database audit logging |
| **AU-3** | 2 | H2 | `AGT-PHI-001` Potential PHI written to application logs without masking |
| **AU-6** | 2 | M2 | `AZ-MON-007` Diagnostic settings on PostgreSQL |
| **AU-9** | 3 | H2 M1 | `AGT-PHI-001` Potential PHI written to application logs without masking |
| **CM-3** | 5 | M2 L3 | `AZ-APP-015-dev` [dev] Deployment slots |
| **CM-6** | 8 | H8 | `AGT-AZ-002` Unresolved Key Vault reference committed as a literal |
| **CM-8** | 27 | H25 M1 L1 | `1124170` next: Next.js: Middleware / Proxy bypass in App Router appli |
| **CP-10** | 6 | M4 L2 | `AZ-APP-015-dev` [dev] Deployment slots |
| **CP-2** | 5 | M4 L1 | `AZ-DB-005-docuaction-db-geo` [prod] High availability |
| **CP-6** | 2 | M2 | `AZ-DB-003-docuaction-db-dev` [dev] Geo-redundant backup |
| **CP-9** | 2 | M2 | `AZ-DB-003-docuaction-db-dev` [dev] Geo-redundant backup |
| **IA-2** | 73 | H72 L1 | `AGT-AUTHZ-001` Endpoint 'GET /saml/config' has no authentication dependency |
| **IA-5** | 30 | C6 H10 L14 | `AGT-AZ-001` Hardcoded Azure/DB connection string |
| **RA-5** | 27 | H25 M1 L1 | `1124170` next: Next.js: Middleware / Proxy bypass in App Router appli |
| **SC-12** | 22 | C6 H2 L14 | `AGT-AZ-001` Hardcoded Azure/DB connection string |
| **SC-13** | 6 | H1 M1 L4 | `B324` B324: Use of weak MD5 hash for security. Consider usedforsec |
| **SC-28** | 8 | C6 H2 | `AGT-AZ-001` Hardcoded Azure/DB connection string |
| **SC-30** | 1 | L1 | `HDR-006` No server/framework version disclosure |
| **SC-5** | 4 | M2 L2 | `AZ-DB-010-docuaction-db-dev` [dev] Storage auto-grow |
| **SC-7** | 6 | H3 M2 L1 | `AZ-DB-006-docuaction-db-dev` [dev] Public network access |
| **SC-8** | 2 | I2 | `AZ-APP-009-dev` [dev] HTTP/2 enabled |
| **SI-10** | 67 | H5 M13 L49 | `AGT-SQL-002` SQL statement built with an f-string or concatenation |
| **SI-11** | 49 | L49 | `B110` B110: Try, Except, Pass detected. |
| **SI-2** | 27 | H25 M1 L1 | `1124170` next: Next.js: Middleware / Proxy bypass in App Router appli |
| **SI-4** | 1 | L1 | `AZ-APP-011-dev` [dev] Health-check path configured |

## Families a scanner cannot assess

These require documentary or procedural evidence and no automated tool can substitute for it:

- **AT** Awareness and Training
- **MA** Maintenance
- **MP** Media Protection
- **PE** Physical and Environmental
- **PL** Planning
- **PM** Program Management
- **PS** Personnel Security

**8 of 19 families have automated findings.** Families with none are NOT ASSESSED, not compliant.
