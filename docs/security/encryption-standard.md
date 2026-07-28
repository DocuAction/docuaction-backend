# DocuAction AI — Encryption Standard

**Product:** DocuAction AI (Version 6.0.0)
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Security Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour acknowledgment SLA)

---

## 1. Purpose and Scope

This standard defines the cryptographic protections applied to DocuAction AI data **in transit** and **at rest**, the algorithms and key sizes required, key-management responsibilities, and application-level cryptographic mechanisms (password hashing, token signing). It applies to the backend service, its Azure data stores, and all interfaces exposed at `api-prod.docuaction.io`.

The standard reflects controls implemented today and clearly marks platform-inherited and planned items. It does not assert a completed FIPS 140 validation of AGT-authored code; see the FIPS-alignment note in Section 7.

---

## 2. Encryption in Transit

| Control | Description | Status |
|---|---|---|
| TLS termination | All external HTTP traffic is served over HTTPS with TLS terminated at the Azure edge. Minimum protocol **TLS 1.2**; TLS 1.3 used where negotiated. | Implemented |
| HTTPS-only | Plaintext HTTP is redirected/rejected; the application is served exclusively over HTTPS. | Implemented |
| HSTS | HTTP Strict Transport Security is asserted via security response headers to prevent protocol downgrade. | Implemented |
| Database transport | Connections from the application to Azure Database for PostgreSQL Flexible Server are established over **SSL/TLS**. | Implemented |
| Cipher suites | Modern AEAD cipher suites with ephemeral key exchange (**ECDHE**) for forward secrecy, as provided by the Azure edge. | Inherited from Azure |

**Algorithms in transit:** AES-256 (and AES-128 where negotiated) for bulk encryption; ECDHE for key agreement (forward secrecy); RSA/ECDSA for certificate authentication. Certificates are managed through the Azure platform.

---

## 3. Encryption at Rest

| Data store | Mechanism | Key management | Status |
|---|---|---|---|
| Azure Database for PostgreSQL Flexible Server | Transparent Data Encryption (TDE) | Platform-managed keys (default); **Customer-Managed Keys (CMK)** via Azure Key Vault available as an option | Inherited from Azure |
| Azure Storage (blobs, backups, artifacts) | Azure Storage Service Encryption (SSE) | Platform-managed keys (default); CMK optional | Inherited from Azure |
| Application secrets | Azure App Service application settings (encrypted at rest by the platform); **target: Azure Key Vault** | Platform-managed; managed-identity access targeted | Partial |

**Algorithm at rest:** AES-256 is used by Azure SSE and PostgreSQL TDE for data-at-rest encryption. Key wrapping for CMK scenarios uses RSA-class keys held in Azure Key Vault.

---

## 4. Application-Level Cryptography

### 4.1 Password Hashing
- **Algorithm:** bcrypt (adaptive, salted per-credential work factor).
- **Storage:** Only bcrypt hashes are persisted; plaintext passwords are never stored or logged.
- **Rationale:** bcrypt provides deliberate computational cost to resist offline brute-force and rainbow-table attacks.

### 4.2 Session Token Signing (JWT)
- **Scheme:** JSON Web Tokens signed with **HMAC-SHA256 (HS256)**.
- **Signing key:** A server-side `SECRET_KEY` provisioned as an Azure App Service application setting (never committed to source).
- **Token types:** Short-lived **access** tokens and longer-lived **refresh** tokens.
- **Revocation:** Tokens are subject to server-side revocation to support logout, credential change, and incident containment.

### 4.3 PII/PHI Handling
- Multi-pattern PII masking (redaction) is applied within the AI processing pipeline so that sensitive identifiers are minimized before downstream processing. See `docs/compliance/data-classification.md`.

---

## 5. Key Management

| Key / secret | Current custody | Target custody | Rotation |
|---|---|---|---|
| TLS certificates | Azure platform | Azure platform | Platform-managed |
| Data-at-rest keys (TDE/SSE) | Azure platform-managed keys | Optional CMK in Key Vault | Platform-managed / policy on CMK |
| JWT `SECRET_KEY` | Azure App Service app setting | Azure Key Vault + managed identity | Rotation policy (planned) — see `docs/security/secrets-management.md` |
| Database credentials | Azure App Service app setting | Azure Key Vault + managed identity | Rotation policy (planned) |
| Third-party API keys / Entra client secret | Azure App Service app setting | Azure Key Vault + managed identity | Rotation policy (planned) |

Secrets are never printed in application logs, error responses, or source control. Detailed handling is defined in the Secrets Management document.

---

## 6. Cryptographic Standards Summary

| Purpose | Algorithm / Parameter |
|---|---|
| Bulk encryption (transit & rest) | AES-256 (AES-128 acceptable in transit where negotiated) |
| Key exchange (transit) | ECDHE (forward secrecy) |
| Certificate authentication | RSA / ECDSA |
| Password hashing | bcrypt (salted, adaptive cost) |
| Token integrity/authenticity | HMAC-SHA256 (HS256) |
| CMK key wrapping (optional) | RSA-class keys in Azure Key Vault |

---

## 7. FIPS-Alignment Note

The Platform relies on Microsoft Azure platform cryptography for transport and at-rest encryption. Microsoft publishes FIPS 140-validated cryptographic modules for the underlying Azure services. AGT's design intent is to align application cryptography with **FIPS 140-approved algorithms** (AES, SHA-2/HMAC-SHA-256, RSA/ECDHE). This document does **not** assert that AGT-authored application code has itself undergone independent FIPS 140 module validation; where FIPS-validated modules are required by a specific engagement, they are inherited at the Azure platform layer and confirmed against Microsoft's current validation attestations.

---

## 8. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Review cadence | Per release or on cryptographic change |
| Approver | AGT Security (security@agtbi.com) |
