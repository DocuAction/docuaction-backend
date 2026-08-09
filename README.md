# DocuAction AI — Backend

**DocuAction AI** is an enterprise document, voice & healthcare intelligence
platform. The backend is a high-performance, asynchronous FastAPI service that
powers document processing, audio transcription, healthcare claims and TEFCA
review, data comparison and extraction, automation, and intelligence reporting
across roughly **261 API endpoints**.

The healthcare/TEFCA module suite is delivered as **DocuAction TEFCA ARC**.

**Platform version:** 6.0.0

---

## Organization

Developed and maintained by **Alliance Global Tech, Inc. ("AGT")** — a
government-focused technology firm operating under a mature, certified
engineering and quality program:

- **CMMI Level 3**
- **ISO 27001** (Information Security Management)
- **ISO 9001** (Quality Management)

Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.

---

## Technology Stack

| Area              | Technology                                                        |
| ----------------- | ----------------------------------------------------------------- |
| Language           | Python 3.12                                                       |
| Web framework      | FastAPI 0.115.0                                                   |
| ASGI server        | Uvicorn 0.30.6 (with gunicorn workers in production)             |
| ORM / DB driver    | SQLAlchemy 2.0.35 (async) + asyncpg 0.29.0                        |
| Migrations         | Alembic 1.13.2                                                    |
| Validation         | Pydantic 2.9.2 + pydantic-settings 2.5.2                          |
| Auth / JWT         | python-jose[cryptography] 3.4.0 (HS256); passlib + bcrypt 4.0.1   |
| HTTP client        | httpx 0.27.2                                                      |
| AI                 | Anthropic SDK 0.39.0                                              |
| Scheduling         | APScheduler 3.10.4                                                |
| Documents / export | reportlab 4.2.5, WeasyPrint, python-docx 1.1.2, pdfplumber 0.11.4, openpyxl 3.1.5 |
| Data quality       | pandera 0.32.0                                                    |
| Reliability        | tenacity 9.0.0, python-statemachine 3.2.0                        |
| Uploads            | python-multipart 0.0.18                                           |

---

## Architecture Overview

DocuAction backend is a layered, fully asynchronous FastAPI application backed by
PostgreSQL via SQLAlchemy 2.0 (async) and asyncpg, with Alembic-managed schema
migrations. Application code is organized under the `app/` package with clear
separation between API routing, domain modules, services, and data access.

**Core modules:**

- Documents
- Audio (OpenAI Whisper transcription)
- Healthcare Claims
- Data Systems
- Comparison
- Extraction
- Automation
- TEFCA Review Protocol (**DocuAction TEFCA ARC**)
- Case Management
- Bulletin Intelligence

**Plus enterprise capabilities:** enterprise administration, validation, decision
intelligence, export, templates, meetings, SLA, and plans.

The service exposes approximately **261 endpoints** and integrates with external
healthcare and procurement data sources through the TEFCA connectors.

**TEFCA connectors:**

| Connector                         | Status              |
| --------------------------------- | ------------------- |
| NPPES                             | Live                |
| PECOS                             | Live                |
| OIG LEIE                          | Live                |
| SAM.gov                           | API key required    |
| TEFCA entity data / ONC   | Pending             |
| IQVIA OneKey                      | Pending             |

---

## Security Posture

Security is a first-class concern. See **[SECURITY.md](SECURITY.md)** for the
vulnerability disclosure policy and supported versions.

- **Authentication:** JWT access + refresh token pair (HS256) issued by **both**
  password login and **Microsoft Entra ID SSO** (OAuth 2.0 authorization-code,
  confidential client). Downstream authorization is identical regardless of
  sign-in method.
- **Authorization:** an **8-level RBAC hierarchy** — viewer (1), contributor (2),
  manager (3), reviewer (4), senior_analyst (5), qalead (6), program_manager (7),
  admin (8). TEFCA contract roles align to HHSAR 352.204-71 / FAR 52.212-4.
- **Password security:** bcrypt hashing via passlib; administrator approval for
  new accounts.
- **Session & token controls:** JWT revocation and session management.
- **Platform protection:** **Microsoft Defender for Cloud (Standard tier)** across
  the Azure estate.
- **Request hardening:** global rate limiting, upload safety (content-type and
  size enforcement), centralized error handling, and TrustedHost / strict CORS
  enforcement with required security configuration validated at startup.

---

## Compliance

DocuAction's engineering practices align with:

- **NIST SP 800-53** — security and privacy controls
- **OWASP** — Top 10 / ASVS secure development
- **HIPAA** — protected health information handling for the TEFCA ARC modules
- **Section 508 / WCAG 2.2 AA** — accessibility

---

## Azure Deployment Status

DocuAction backend is **live in production on Microsoft Azure**:

- **Backend:** Azure App Service (Linux, Python 3.12, gunicorn + uvicorn workers)
  at **https://api-prod.docuaction.io**
- **Database:** Azure Database for PostgreSQL Flexible Server
- **Frontend:** **https://app.docuaction.io**
- **Threat protection:** Microsoft Defender for Cloud (Standard tier)
- **Identity:** Microsoft Entra ID SSO + password authentication

The platform was **migrated from Railway to Microsoft Azure** as part of the
6.0.0 release.

---

## Local Development

**Prerequisites:** Python 3.12 and PostgreSQL 14+.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and set at least DATABASE_URL and SECRET_KEY

# 4. Run the development server
uvicorn app.main:app --reload --port 8000
```

- **Health check:** http://localhost:8000/health
- **Interactive API docs:** http://localhost:8000/docs

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for branch strategy, conventional
commits, linting/formatting (ruff), tests (pytest), and the pre-commit hooks.

---

## Governance & Documentation

Project governance and operational documentation live under the [`docs/`](docs/)
directory, including:

- **Architecture** — system design and module structure
- **Security** — controls, hardening, and posture
- **Compliance** — NIST 800-53, OWASP, HIPAA, Section 508 mappings
- **Deployment** — Azure App Service + PostgreSQL Flexible Server procedures
- **Runbooks** — operational and incident response procedures
- **API** — endpoint reference

Repository governance files:

- [SECURITY.md](SECURITY.md) — vulnerability disclosure policy
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution guidelines
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — community standards
- [CHANGELOG.md](CHANGELOG.md) — release history
- [LICENSE](LICENSE) — proprietary license & U.S. Government rights
- [NOTICE](NOTICE) — third-party attributions

---

## License & Copyright

DocuAction AI is **proprietary software** of Alliance Global Tech, Inc.
Unauthorized use, reproduction, or distribution is prohibited. See
[LICENSE](LICENSE) for the full terms, including the U.S. Government Rights
clause (FAR 52.227-14).

Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.

**Contact:** general — imran@agtbi.com · security — security@agtbi.com
