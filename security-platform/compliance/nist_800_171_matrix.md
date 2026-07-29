# NIST SP 800-171 Rev. 3 - CUI Control Family Mapping

**Project:** DocuAction  
**Scan:** `docuaction_20260728T003746`  
**Findings analysed:** 309  
**Generated:** 2026-07-28T01:18:14+00:00

> **How to read this.** A control is marked **GAP** only where a finding demonstrates a deficiency, and **EVIDENCED** only where a passing test or an observed configuration supports it. Everything else is **NOT ASSESSED** - an automated scan that finds nothing is not evidence that a control is satisfied. This document supports an assessment; it is not a certification.


800-171 requirements are derived from the 800-53 moderate baseline, so findings are mapped through their 800-53 control families.

| Req | Family | Mapped 800-53 | Findings | Severity | Status |
|---|---|---|--:|---|---|
| 3.1 | Access Control | AC | 97 | H77 M20 | **GAP** |
| 3.2 | Awareness and Training | AT | 0 | - | NOT ASSESSABLE by scanner |
| 3.3 | Audit and Accountability | AU | 8 | H2 M6 | **GAP** |
| 3.4 | Configuration Management | CM | 40 | H33 M3 L4 | **GAP** |
| 3.5 | Identification and Authentication | IA | 103 | C6 H82 L15 | **GAP** |
| 3.6 | Incident Response | IR | 0 | - | NOT ASSESSED |
| 3.7 | Maintenance | MA | 0 | - | NOT ASSESSABLE by scanner |
| 3.8 | Media Protection | MP | 0 | - | NOT ASSESSABLE by scanner |
| 3.9 | Personnel Security | PS | 0 | - | NOT ASSESSABLE by scanner |
| 3.10 | Physical Protection | PE | 0 | - | NOT ASSESSABLE by scanner |
| 3.11 | Risk Assessment | RA | 27 | H25 M1 L1 | **GAP** |
| 3.12 | Security Assessment | CA | 0 | - | NOT ASSESSED |
| 3.13 | System and Communications Protection | SC | 41 | C6 H6 M5 L22 I2 | **GAP** |
| 3.14 | System and Information Integrity | SI | 144 | H30 M14 L100 | **GAP** |

**Scope note.** DocuAction handles ePHI. Whether it also handles CUI depends on the contract vehicle; this matrix is provided for readiness, not as an assertion that CUI is in scope.
