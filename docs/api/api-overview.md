# DocuAction AI — API Overview

**Alliance Global Tech, Inc. (AGT)** — Copyright © 2024–2026
**Product:** DocuAction AI — **Version 6.0.0**
**Base URL (production):** `https://api-prod.docuaction.io`
**Compliance frameworks:** NIST SP 800-53 · OWASP · HIPAA · Section 508

---

## 1. Introduction

The DocuAction AI backend exposes approximately **261 REST endpoints** spanning
document intelligence, audio transcription, healthcare claims, data systems, the
DocuAction TEFCA ARC review protocol, and supporting enterprise capabilities. All
endpoints are served over TLS from `https://api-prod.docuaction.io` and are subject
to host allow-listing (`TrustedHostMiddleware`), strict CORS, global rate limiting,
JWT authentication, and 8-level RBAC.

## 2. Interactive Documentation

Machine-readable OpenAPI and interactive Swagger UI are available at **`/docs`**,
**gated by the `ENABLE_DOCS` environment flag**. Documentation is typically
disabled in hardened production environments and enabled selectively for
integration and review. The raw OpenAPI schema underpins client generation and
contract review.

## 3. Versioning & Compatibility

- **Prefix:** all application endpoints are served under the **`/api`** prefix.
- **Policy:** changes are **additive and backward-compatible**. New fields and
  endpoints may be introduced without breaking existing integrations; breaking
  changes are avoided and, if ever unavoidable, communicated and versioned
  explicitly.
- **Deprecation:** deprecated behavior is announced ahead of removal.

## 4. Authentication

All non-public endpoints require a JWT bearer token issued by password login or
Microsoft Entra ID SSO. See `authentication.md` for the full flows, token claims,
and RBAC enforcement.

```
Authorization: Bearer <access_token>
```

## 5. Error Envelope

Errors are returned in a consistent JSON envelope to support reliable client
handling and traceable support:

```json
{
  "error": "Human-readable message",
  "code": "MACHINE_READABLE_CODE",
  "request_id": "correlation-id"
}
```

- `error` — a safe, human-readable description (no PHI/PII/secret leakage).
- `code` — a stable, machine-readable error identifier.
- `request_id` — a correlation identifier for support and audit trace lookup.

## 6. Rate Limiting

A **global rate limiter** protects all endpoints against abuse and denial-of-service
patterns. Callers exceeding the configured threshold receive an HTTP `429` response.
Clients should implement backoff and honor rate-limit signaling.

## 7. Health & Operations

- **`GET /health`** — lightweight liveness/readiness check for load balancers and
  monitoring. Note: because `TrustedHostMiddleware` is enforced, requests with an
  unlisted `Host` header receive `400` on all routes, including `/health`.
- **Monitoring:** Microsoft Defender for Cloud (Standard) provides continuous
  posture management across App Service and PostgreSQL.

## 8. Endpoint Groups

The ~261 endpoints are organized into the following functional groups. Paths shown
are representative under the `/api` prefix and illustrate the group, not an
exhaustive listing.

| Group | Purpose | Representative paths |
|-------|---------|----------------------|
| Authentication | Login, tokens, identity, SSO | `/api/auth/login`, `/api/auth/me`, `/api/auth/login/azure`, `/api/auth/callback/azure` |
| Documents | Document ingestion & intelligence | `/api/documents/...` |
| Audio | Whisper transcription & analysis | `/api/audio/...` |
| Healthcare Claims | Claims intake & processing | `/api/claims/...` |
| Data Systems | Structured data / system integration | `/api/data-systems/...` |
| Comparison | Document / dataset comparison | `/api/comparison/...` |
| Extraction | Structured data extraction | `/api/extraction/...` |
| Automation | Workflow automation | `/api/automation/...` |
| TEFCA Review Protocol | DocuAction TEFCA ARC reviews & connectors | `/api/tefca/...`, `/api/tefca/dashboard/...` |
| Case Management | Case lifecycle & assignment | `/api/cases/...` |
| Bulletin Intelligence | Scheduled intelligence briefings | `/api/bulletin/...` |
| Enterprise & Validation | Enterprise features, validation engine | `/api/enterprise/...`, `/api/validation/...` |
| Decision Intelligence | Analytics & decision support | `/api/decision-intel/...` |
| Export & Templates | Export rendering, templates | `/api/export/...`, `/api/templates/...` |
| Meetings, SLA & Plans | Collaboration & planning | `/api/meetings/...`, `/api/sla/...`, `/api/plans/...` |
| Operations | Health & readiness | `/health` |

> Note: precise per-group endpoint counts are intentionally not enumerated here;
> only the approximate total of **~261** endpoints is authoritative. Group
> membership is qualitative and evolves additively.

## 9. Connector Endpoints (TEFCA)

TEFCA Review Protocol endpoints integrate authoritative sources — NPPES (live),
PECOS (live), OIG LEIE (live), SAM.gov (API key required), and RCE/Sequoia and
IQVIA OneKey (pending). Connector behavior and roles align with HHSAR 352.204-71
and FAR 52.212-4.

## 10. Related Documents

- `authentication.md` — authentication and SSO sequence diagrams.
- `../architecture/system-overview.md` — system architecture.
- `../architecture/data-flow.md` — request and data flow.
