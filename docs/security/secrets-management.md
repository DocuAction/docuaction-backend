# DocuAction AI — Secrets Management Standard

**Product:** DocuAction AI (Version 6.0.0)
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Security Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour acknowledgment SLA)

---

## 1. Purpose and Scope

This standard governs how DocuAction AI handles application secrets: where they are stored, how they are protected, how they are kept out of source control and logs, and the roadmap toward a centralized secrets store. It applies to all secrets required by the backend service running on Azure App Service.

**Non-disclosure principle:** This document enumerates *categories* of secrets. It never contains, and must never contain, real secret values.

---

## 2. Current State

| Practice | Description | Status |
|---|---|---|
| Secrets outside code | Secrets are supplied to the runtime as **Azure App Service application settings** (environment variables injected by the platform), not embedded in source code. | Implemented |
| No secrets in repository | Local configuration files (`.env`) are **gitignored** and never committed. | Implemented |
| Pre-commit scanning | **detect-secrets** runs as a pre-commit control to catch accidental secret introduction before it reaches version control. | Implemented |
| Encryption at rest | Azure App Service application settings are encrypted at rest by the platform. | Inherited from Azure |
| Transport | Settings are delivered to the app over the platform's secured channel; secrets traverse the network only over TLS. | Inherited from Azure |

---

## 3. Target State

| Objective | Description | Status |
|---|---|---|
| Centralized vault | Migrate secrets to **Azure Key Vault** as the authoritative secrets store. | Planned |
| Managed identity access | Grant the App Service a **managed identity** with least-privilege access to Key Vault, eliminating stored access credentials for the vault itself. | Planned |
| Rotation policy | Establish scheduled and event-driven rotation (e.g., on suspected compromise, personnel change, or defined interval) for all rotatable secrets. | Planned |
| No secrets in logs | Enforce that secrets never appear in application logs, error responses, or telemetry; centralized error handling redacts sensitive context. | Implemented (redaction) / Planned (vault-backed) |
| Access auditing | Record and review access to secret material via Key Vault audit events and Microsoft Defender for Cloud. | Planned |

---

## 4. Secret Categories

The following categories of secrets are managed by the Platform. **No values are stored in this document.**

| Category | Examples (names only) | Custody today | Target custody |
|---|---|---|---|
| Database credentials | PostgreSQL connection string / password (`DATABASE_URL`) | Azure app setting | Key Vault + managed identity |
| Token signing key | JWT `SECRET_KEY` (HS256 signing) | Azure app setting | Key Vault + managed identity |
| Identity provider secret | Microsoft Entra ID client secret | Azure app setting | Key Vault + managed identity |
| Third-party API keys | External data/service integration keys (e.g., email delivery, connector/data-source keys) | Azure app setting | Key Vault + managed identity |
| Platform/service tokens | Any service-to-service credentials used by scheduled jobs | Azure app setting | Key Vault + managed identity |

---

## 5. Handling Rules

1. **Never in code.** Secrets must not appear in source files, comments, fixtures, or committed configuration.
2. **Never in logs.** Application logging and centralized error handling must redact secret values and sensitive context; request IDs are used for correlation instead of sensitive data.
3. **Never in client responses.** Error responses expose a request ID and a generic message, not secret material or stack internals.
4. **Least privilege.** Access to secrets is limited to the runtime identity that requires them; human access is minimized and audited.
5. **Rotation on compromise.** Any suspected exposure triggers immediate rotation and invalidation, coordinated with the Incident Response Plan (`docs/security/incident-response-plan.md`).
6. **Pre-commit enforcement.** `detect-secrets` gating remains active; findings block the commit pending review.

---

## 6. Roadmap Summary

```
  Current:  Code (no secrets) ──> Azure App Service application settings ──> Runtime
                                    (encrypted at rest, TLS in transit)

  Target:   Code (no secrets) ──> Azure Key Vault ──(managed identity)──> Runtime
                                    + rotation policy + access audit (Defender/Monitor)
```

---

## 7. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Review cadence | Per release or on secrets-architecture change |
| Approver | AGT Security (security@agtbi.com) |
