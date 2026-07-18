# DocuAction AI — Security Architecture Overview

**Product:** DocuAction AI (Healthcare/TEFCA suite: DocuAction TEFCA ARC)
**Version:** 6.0.0
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Certifications (organizational):** CMMI Level 3, ISO/IEC 27001, ISO 9001
**Document Classification:** Internal — Security Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (acknowledgment SLA: 48 hours)

---

## 1. Purpose and Scope

This document summarizes the security architecture of the DocuAction AI platform (the "Platform") as deployed on Microsoft Azure. It describes the defense-in-depth model, the mapping of implemented technical controls to architectural layers, and the shared-responsibility boundary between AGT and Microsoft Azure.

This overview is written to support review by federal customers and prime contractors. It reflects controls that are **implemented in the Platform today** and clearly identifies items that are **targeted/planned**. It does not assert a formal Authorization to Operate (ATO) or a completed third-party assessment.

Scope covers the DocuAction backend service (`api-prod.docuaction.io`), its supporting Azure infrastructure, and the interfaces to the DocuAction frontend (`app.docuaction.io`).

---

## 2. Defense-in-Depth Model

The Platform applies layered security controls so that the failure of any single control does not result in compromise of protected data. Six control layers are recognized:

1. **Edge / Transport (TLS)** — encrypted transport termination at the Azure edge.
2. **Network** — host allowlisting, origin allowlisting, and Azure network boundary controls.
3. **Application** — authentication, authorization, input/upload safety, rate limiting, error handling.
4. **Data** — encryption at rest, PII/PHI masking, data classification and handling.
5. **Identity** — Microsoft Entra ID SSO, JWT session management, RBAC, secrets management.
6. **Monitoring & Response** — audit logging, Microsoft Defender for Cloud, health monitoring, incident response.

### 2.1 Layered-Defense Diagram

```
                          External Users / Federal Reviewers
                                        |
                                    HTTPS / TLS
                                        |
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  LAYER 1 — EDGE / TRANSPORT                                               │
  │  • TLS 1.2+ termination at Azure edge   • HTTPS-only   • HSTS            │
  │  • Security response headers                                             │
  └─────────────────────────────────────────────────────────────────────────┘
                                        |
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  LAYER 2 — NETWORK                                                        │
  │  • TrustedHost middleware (host allowlist)                               │
  │  • Strict CORS origin allowlist                                          │
  │  • Azure App Service network boundary                                    │
  └─────────────────────────────────────────────────────────────────────────┘
                                        |
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  LAYER 3 — APPLICATION                                                    │
  │  • JWT (HS256) access + refresh w/ revocation                           │
  │  • 8-level RBAC (least privilege)                                        │
  │  • Global rate limiting        • Upload safety controls                 │
  │  • Centralized error handling with request IDs                          │
  │  • Admin-approval + disposable-email blocking on registration           │
  └─────────────────────────────────────────────────────────────────────────┘
                                        |
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  LAYER 4 — DATA                                                           │
  │  • Encryption at rest (Azure TDE / Storage Service Encryption)          │
  │  • Database SSL in transit                                               │
  │  • PII masking in AI pipeline (multi-pattern redaction)                 │
  │  • Data classification: PHI / PII / CUI / Internal / Public             │
  └─────────────────────────────────────────────────────────────────────────┘
                                        |
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  LAYER 5 — IDENTITY                                                       │
  │  • Microsoft Entra ID SSO      • bcrypt password hashing                │
  │  • Session controls            • Secrets as Azure app settings          │
  │    (target: Azure Key Vault + managed identity)                         │
  └─────────────────────────────────────────────────────────────────────────┘
                                        |
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  LAYER 6 — MONITORING & RESPONSE                                          │
  │  • Enterprise audit logging    • Microsoft Defender for Cloud (Std)     │
  │  • Health monitoring           • Incident response process              │
  └─────────────────────────────────────────────────────────────────────────┘
                                        |
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  DATA STORES — Azure Database for PostgreSQL Flexible Server (SSL)       │
  └─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Control-to-Layer Mapping

The following table maps implemented Platform controls to their primary architectural layer.

| Control | Layer | Status |
|---|---|---|
| TLS in transit (HTTPS at Azure edge) | Edge / Transport | Implemented |
| HSTS / HTTPS-only | Edge / Transport | Implemented |
| Security response headers | Edge / Transport | Implemented |
| TrustedHost middleware (host allowlist) | Network | Implemented |
| Strict CORS allowlist | Network | Implemented |
| JWT (HS256) access + refresh with revocation | Application / Identity | Implemented |
| 8-level RBAC | Application / Identity | Implemented |
| Global rate limiting | Application | Implemented |
| Upload safety controls | Application | Implemented |
| Centralized error handling with request IDs | Application | Implemented |
| Admin-approval + disposable-email blocking on registration | Application | Implemented |
| bcrypt password hashing | Identity | Implemented |
| Microsoft Entra ID SSO | Identity | Implemented |
| Session controls | Identity | Implemented |
| Database SSL | Data | Implemented |
| PII masking in AI pipeline (multi-pattern redaction) | Data | Implemented |
| Encryption at rest (Azure TDE / Storage Service Encryption) | Data | Inherited from Azure |
| Enterprise audit logging | Monitoring & Response | Implemented |
| Microsoft Defender for Cloud (Standard) | Monitoring & Response | Inherited/Configured |
| Health monitoring | Monitoring & Response | Implemented |
| Secrets management (Azure app settings; Key Vault target) | Identity | Partial (target: Key Vault) |

---

## 4. Shared-Responsibility Model (AGT ↔ Microsoft Azure)

The Platform runs as a Platform-as-a-Service (PaaS) workload. Responsibility is divided as follows.

| Domain | Microsoft Azure (Provider) | Alliance Global Tech (Customer) |
|---|---|---|
| Physical data center security | Responsible | Inherited |
| Host OS / hypervisor patching (PaaS) | Responsible | Inherited |
| Platform runtime (App Service, PostgreSQL Flexible Server) | Responsible | Configured by AGT |
| Encryption at rest (platform-managed keys) | Responsible | Configured / optionally CMK |
| Network edge & DDoS baseline | Responsible | Configured (allowlists) |
| Application code & dependencies | — | Responsible |
| Authentication & authorization logic | — | Responsible |
| RBAC & least-privilege policy | — | Responsible |
| Secrets management configuration | Shared (Key Vault infra) | Responsible |
| Data classification & handling | — | Responsible |
| Audit logging & log review | Provides tooling (Defender/Monitor) | Responsible |
| Incident response | Provides alerts (Defender) | Responsible |
| Business Associate Agreement (HIPAA) | Party to BAA | Responsible to establish/maintain |

---

## 5. Related Security & Compliance Documents

| Document | Path |
|---|---|
| Encryption Standard | `docs/security/encryption-standard.md` |
| Access Control Model (RBAC) | `docs/security/access-control-model.md` |
| Secrets Management | `docs/security/secrets-management.md` |
| Incident Response Plan | `docs/security/incident-response-plan.md` |
| Vulnerability Disclosure Policy | `docs/security/vulnerability-disclosure-policy.md` |
| Data Classification | `docs/compliance/data-classification.md` |
| NIST SP 800-53 (Rev. 5) Mapping | `docs/compliance/nist-800-53-mapping.md` |
| HIPAA Safeguards | `docs/compliance/hipaa-safeguards.md` |
| SBOM Policy | `docs/compliance/sbom-policy.md` |

---

## 6. Compliance Frameworks in Scope

- **NIST SP 800-53 (Rev. 5)** — control mapping across AC, AU, IA, SC, SI, CM, CP, IR, RA, CA, MP families.
- **OWASP** — Top 10 and ASVS as application-security baselines.
- **HIPAA Security Rule** — administrative, physical (inherited), and technical safeguards.
- **Section 508 / WCAG 2.2 AA** — front-end accessibility (documented in the frontend repository).

---

## 7. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Review cadence | Per release or on material architecture change |
| Approver | AGT Security (security@agtbi.com) |

*This document describes implemented and targeted controls honestly. Where a control is inherited from Azure or planned, it is marked accordingly. No statement herein should be read as asserting a completed independent assessment or ATO.*
