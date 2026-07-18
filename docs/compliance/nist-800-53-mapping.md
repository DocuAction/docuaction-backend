# DocuAction AI — NIST SP 800-53 (Rev. 5) Control Mapping

**Product:** DocuAction AI (Version 6.0.0)
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Compliance Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour acknowledgment SLA)

---

## 1. Purpose and Scope

This document maps DocuAction AI security controls to selected **NIST SP 800-53 Rev. 5** control families. It is provided to support federal review and to communicate control posture honestly. It is **not** an assessment report and does **not** assert a formal Authorization to Operate (ATO), an Assessment & Authorization (A&A) result, or a completed third-party control assessment.

**Status legend:**
- **Implemented** — control is in place in the Platform today.
- **Partial** — control is partially in place; work remains.
- **Planned** — control is targeted but not yet in place.
- **Inherited** — control is provided by Microsoft Azure under the shared-responsibility model.

Responsibility for shared/inherited controls is described in `docs/security/security-overview.md`.

---

## 2. Control-Family Coverage

### AC — Access Control
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| AC-2 Account Management; AC-3 Access Enforcement; AC-6 Least Privilege | 8-level RBAC enforced server-side; least-privilege defaults (new users = viewer); admin-approval on registration; admin user management. | Implemented |
| AC-7 Unsuccessful Logon; AC-12 Session Termination | Session controls; JWT revocation supports session termination. | Partial |
| AC-17 Remote Access | HTTPS-only access; TrustedHost + strict CORS allowlists. | Implemented |

### AU — Audit and Accountability
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| AU-2 Event Logging; AU-3 Content of Records; AU-12 Audit Generation | Enterprise audit logging of authentication, authorization, admin, and data-access events; request IDs for correlation. | Implemented |
| AU-6 Audit Review/Analysis; AU-9 Protection of Audit Info | Review supported via logs and Defender; secrets/PII kept out of logs. | Partial |

### IA — Identification and Authentication
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| IA-2 Identification & Authentication; IA-5 Authenticator Management | Microsoft Entra ID SSO; bcrypt password hashing; JWT (HS256) access+refresh with revocation. | Implemented |
| IA-8 Non-Organizational Users | Entra SSO first-login provisioning at viewer (least privilege). | Implemented |

### SC — System and Communications Protection
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| SC-8 Transmission Confidentiality/Integrity; SC-23 Session Authenticity | TLS 1.2+ at Azure edge; HTTPS-only; HSTS; database SSL; JWT-based sessions. | Implemented |
| SC-13 Cryptographic Protection; SC-28 Protection at Rest | AES-256 at rest via Azure TDE/SSE (platform-managed keys, CMK optional). | Implemented / Inherited |
| SC-12 Key Establishment/Management | Platform key management; app secrets in Azure app settings (Key Vault targeted). | Partial |
| SC-5 Denial-of-Service Protection | Global rate limiting; Azure edge protections. | Implemented / Inherited |
| SC-7 Boundary Protection | TrustedHost middleware + strict CORS; Azure App Service boundary. | Implemented / Inherited |

### SI — System and Information Integrity
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| SI-2 Flaw Remediation | Dependabot weekly; pip-audit; SBOM policy. | Implemented |
| SI-3 Malicious Code / SI-4 Monitoring | Microsoft Defender for Cloud (Standard); health monitoring. | Inherited / Implemented |
| SI-10 Information Input Validation | Upload safety controls; input handling; centralized error handling with request IDs. | Implemented |
| SI-11 Error Handling | Centralized error handling that avoids exposing sensitive detail. | Implemented |

### CM — Configuration Management
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| CM-2 Baseline Configuration; CM-6 Configuration Settings | Version-controlled configuration; secrets externalized to app settings; security headers baseline. | Partial |
| CM-8 System Component Inventory | SBOM (CycloneDX/SPDX) per release and on dependency change. | Partial |

### CP — Contingency Planning
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| CP-9 System Backup; CP-10 Recovery | Azure PostgreSQL Flexible Server backup capabilities; recovery procedures. | Inherited / Partial |

### IR — Incident Response
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| IR-4 Incident Handling; IR-5 Monitoring; IR-6 Reporting; IR-8 IR Plan | Documented Incident Response Plan; Defender alerts; audit logs; 48h external ack via security@agtbi.com. | Implemented |

### RA — Risk Assessment
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| RA-5 Vulnerability Monitoring/Scanning | Dependabot weekly; pip-audit; Defender for Cloud posture. | Implemented / Inherited |

### CA — Assessment, Authorization, Monitoring
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| CA-2 Control Assessments; CA-7 Continuous Monitoring | Ongoing monitoring via Defender/health checks; formal independent assessment not asserted. | Partial / Planned |

### MP — Media Protection
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| MP-4 Media Storage; MP-6 Media Sanitization | Data-at-rest encryption; media/storage lifecycle managed at Azure platform. | Inherited / Partial |

### PE — Physical and Environmental Protection
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| PE-2/PE-3 Physical Access; PE-family | Physical/environmental controls for data centers. | Inherited (Azure) |

### PL / PS / AT (Program & Personnel)
| Representative Controls | DocuAction / Azure Implementation | Status |
|---|---|---|
| PL-2 System Security Plan; PS Personnel Security; AT Awareness & Training | Organizational programs (AGT CMMI L3 / ISO 27001 governance); documentation baseline in `docs/security`. | Partial / Planned |

---

## 3. Honest Posture Statement

DocuAction AI implements a substantive set of NIST SP 800-53 Rev. 5 controls, with meaningful coverage across AC, AU, IA, SC, SI, and IR. Several controls are **Partial** or **Planned** (notably centralized secrets in Key Vault, formal continuous-monitoring/assessment artifacts, and full configuration-baseline management), and physical/environmental and certain platform controls are **Inherited** from Microsoft Azure. **No formal ATO, A&A authorization, or completed independent control assessment is claimed by this document.**

---

## 4. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Baseline reference | NIST SP 800-53 Rev. 5 |
| Review cadence | Per release or on control change |
| Approver | AGT Security (security@agtbi.com) |
