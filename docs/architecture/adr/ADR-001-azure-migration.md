# ADR-001: Migration from Railway to Microsoft Azure

**Alliance Global Tech, Inc. (AGT)** — Copyright © 2024–2026
**Status:** Accepted
**Deciders:** DocuAction Engineering (@imran-agt)
**Applies to:** DocuAction AI backend (Version 6.0.0)

---

## Context

The DocuAction AI backend was initially hosted on Railway. As the platform matured
toward federal and healthcare use cases — handling PHI, PII, and CUI under
NIST SP 800-53, HIPAA, and related obligations — the hosting environment needed to
provide enterprise identity, continuous security posture management, managed and
compliant data services, and an auditable cloud governance model. Railway did not
offer the integrated identity, defense-in-depth tooling, or government-aligned
compliance posture that AGT's contractual commitments require.

## Decision

We migrated the backend, database, and identity stack to **Microsoft Azure**:

- **Compute:** Azure App Service (Linux, Python 3.12, gunicorn + uvicorn).
- **Database:** Azure Database for PostgreSQL Flexible Server (replacing the prior
  managed Postgres).
- **Identity:** Microsoft Entra ID SSO alongside existing password authentication.
- **Security posture:** Microsoft Defender for Cloud (Standard) across App Service
  and PostgreSQL resources.
- **Domains:** `api-prod.docuaction.io` (backend, live) and `app.docuaction.io`
  (frontend).

## Consequences

### Positive

- **Enterprise identity** through Entra ID SSO with least-privilege provisioning.
- **Continuous security monitoring** via Microsoft Defender for Cloud (Standard).
- **Government/compliance alignment** — Azure's compliance breadth supports AGT's
  NIST SP 800-53 and HIPAA obligations.
- **Managed PostgreSQL** — Flexible Server provides managed backups, patching, high
  availability options, and encryption suitable for regulated workloads.
- **Consolidated cloud governance** — identity, compute, data, and security posture
  under a single, auditable control plane.

### Negative

- **Increased operational complexity** relative to Railway's turnkey model.
- **Migration effort** — database replication/cutover and configuration hardening
  (TrustedHost/CORS, required SECRET_KEY/DATABASE_URL) were required.
- **Potential cost increase** for managed and Standard-tier security services.

## Alternatives Considered

- **Remain on Railway** — rejected: insufficient enterprise identity, security
  posture management, and compliance alignment for federal healthcare workloads.
- **AWS / GCP** — viable clouds, but Azure was preferred for its native Entra ID
  integration, Defender for Cloud, and compliance posture best matched to AGT's
  government engagements.
