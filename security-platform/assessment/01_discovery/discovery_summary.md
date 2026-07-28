# Discovery Summary — DocuAction (Phase 0, Part 1)

**Mode:** Read-only assessment. **Zero code/config/data changes.** All output under `security-platform/assessment/01_discovery/`.

## What DocuAction actually is

DocuAction is **not** a TEFCA-only application — it is a **large multi-product "Enterprise Intelligence" platform** (52.5k backend LOC, 29.6k frontend LOC) in which **TEFCA is one of ~10 modules**. The platform spans:

- **GovCon procurement** (RFQs, quotes, BOM, suppliers, products, deal registration, pricing)
- **ATS / staffing / bench sales**
- **ERP / finance** (contracts, invoices, employees, expenses)
- **Migration Intelligence**, **Case Management (CCM)**, **Healthcare Claims**, **Bulletin Intelligence (FCC)**
- **TEFCA** — legacy "Review Protocol" (`app/Tefca`) **and** the new normalized **Registry** (`app/tefca_registry`) that this engagement built and deployed.

This breadth is the single most important context for the whole assessment: **security/UX/quality findings must be scoped per-module**, and the TEFCA registry (the newest, best-tested slice) is not representative of the older modules.

## Headline counts

| Area | Count |
|---|---|
| Backend Python files / LOC | **163 / 52,483** |
| API endpoints (decorators) | **411** (GET 232 · POST 143 · PATCH 27 · DELETE 9) |
| Endpoint-defining modules | 52 |
| Declared SQLAlchemy models | **113** |
| Tables in local dev DB | **51** |
| FK constraints / indexes (local) | 41 / 151 |
| FK columns missing a leading index | **15** |
| Router namespaces | ~45 prefixes |
| Frontend framework | **Next.js 16 (App Router, static export)** |
| Frontend page routes / components / src files / LOC | **75 / 63 / 182 / 29,609** |
| RBAC roles | 8 (viewer→admin) |
| External APIs | 8 (Anthropic, OpenAI, NPPES, LEIE, PECOS, SAM, RCE/Sequoia, SendGrid) |
| Azure resources (prod RG) | ~20 (App Service, 2× PostgreSQL, KV+private endpoint, SWA, App Insights, Log Analytics, 4 alerts, VNet) |
| CI workflows | 3 (CodeQL, dependency-review, security-scan) |

## Stack at a glance

- **Backend:** FastAPI 0.115 · SQLAlchemy 2.0 async + asyncpg · Pydantic 2.9 · PostgreSQL 16 · gunicorn/uvicorn on Azure App Service (Linux, Python 3.12).
- **Frontend:** Next.js 16 App Router, **static export** to Azure Static Web Apps · Tailwind (layout) + `azure-tokens.css` + JS design tokens (inline styles) · Fluent-2-inspired platform component library (`src/platform`).
- **Auth:** JWT HS256 (15m/24h-admin access, 7d refresh) · bcrypt · 8-level RBAC · Entra ID SSO · in-memory lockout/throttling · token-epoch revocation.
- **Infra:** Managed Identity + Key Vault (private endpoint) · geo-redundant PG backups · App Insights + alerts.

## Early signals worth flagging now (detail deferred to their parts)

**Strengths observed during discovery**
- Mature security scaffolding already present: TrustedHost + CORS allowlist + 6 security headers + global rate limiting + upload scanner + audit logging + KV private endpoint + CI SAST/dependency-review.
- The **TEFCA registry** module is clean, well-indexed, well-audited, and tested.

**Concerns to carry forward**
1. **Model↔table gap:** 113 declared models vs 51 materialized tables (two `Base` classes; procurement/ERP/ATS/migration/CM models not in this DB) — architectural clarity + dead-schema risk (Part 2).
2. **Runtime `create_all` instead of Alembic** on prod — migration governance gap (Part 2/9).
3. **No HA / single-instance** (App Service capacity 1, PG HA disabled) + **manual deploy, no deploy pipeline/tests** (Part 9).
4. **15 un-indexed FK columns** + JSONB without GIN (Part 7).
5. **Dependency flags:** unpinned `weasyprint`/`gunicorn`, redundant `passlib`, `xlsx` from CDN tarball, suspicious `lucide-react ^1.23.0`, unused `@tanstack/react-table` (Part 8/Phase 1 scan).
6. **`DATABASE_URL` as a direct credential string** (vs KV/passwordless) and a **possibly-orphaned 2nd Postgres** (Part 8/9).
7. **~106/411 endpoints** without an explicit auth dependency — must be enumerated to confirm intent (Part 8).
8. **Dockerfile diverges** from the actual prod runtime (Part 9 doc gap).

## Deliverables produced (Part 1)
- `app_inventory.json` — machine-readable full inventory
- `api_inventory.md` — endpoints, namespaces, auth posture
- `database_inventory.md` — tables, categories, FKs, missing indexes
- `dependency_inventory.md` — packages + flags
- `infrastructure_inventory.md` — Azure + CI/CD + deploy model
- `discovery_summary.md` — this document

**STOP — awaiting approval before Part 2 (Architecture Review).**
