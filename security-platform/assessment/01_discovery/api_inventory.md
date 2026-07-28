# API Inventory — DocuAction Backend

**Framework:** FastAPI 0.115.0 · **Total endpoint decorators:** 411 · **Files defining endpoints:** 52
**By method:** GET 232 · POST 143 · PATCH 27 · DELETE 9

> Read-only static analysis. Counts are decorator counts; a handful of routers register additional routes programmatically.

## Router namespaces (prefixes)

The API is organized into ~45 router prefixes spanning **multiple products**, of which TEFCA is one:

| Domain | Prefixes |
|---|---|
| **Auth / users / admin** | `/api/auth`, `/api/user`, `/api/admin`, `/api/security` |
| **TEFCA (legacy)** | `/api/v1/tefca`, `/api/v1/tefca/demo`, `/api/tefca` (dashboard) |
| **TEFCA Registry (new)** | `/api/tefca/registry` |
| **Bulletin Intelligence** | `/api/v1/bulletin` |
| **Case Management** | `/api/v1/case-management` |
| **Healthcare Claims** | `/api/healthcare` |
| **Procurement / GovCon** | `/rfq`, `/quotes`, `/bom`(via rfq), `/products`, `/suppliers`, `/deals`, `/deal-tracker`, `/deal-registrations`, `/pricing`, `/proposal-library`, `/compare`(intel) |
| **ATS / Staffing** | `/ats`, `/ats/ai-agent`, `/ats/bench`, `/staffing`, `/opportunities` |
| **ERP / Finance** | `/finance`, `/invoices`, `/projects` |
| **Intelligence / Governance / Migration** | `/api/intel`, `/intel`, `/ai`, `/api/governance`, `/api/decisions`, `/api/migration`, `/api/plan`, `/api/sla`, `/api/enterprise`, `/api/meetings`, `/api/templates`, `/api/validation` |
| **CRM / misc** | `/customers`, `/company-profile`, `/agency-contacts`, `/support`, `/export` |

## Endpoint density (top modules)

| Endpoints | Module |
|---:|---|
| 36 | `app/bulletin_intelligence/routes.py` |
| 26 | `app/routers/ats.py` |
| **19** | **`app/tefca_registry/routes.py`** (registry: reads, verification, **import**) |
| 17 | `app/api/enterprise_routes.py` |
| 16 | `app/routers/staffing.py` |
| 14 | `intel.py`, `finance.py`, `ats_agent.py`, `api/routes.py`, `admin_users.py` |
| 13 | `opportunities.py` |
| 12 | `suppliers.py`, `wow_routes.py`, `migration_routes.py`, `decision_intel_routes.py` |
| … | (~30 more modules with 5–11 each) |

The legacy TEFCA router (`app/Tefca/routes.py`) alone is **2,852 LOC** and hosts the largest single-file route surface (cycles, reviews, priority, QA, reports, dashboard, demo).

## TEFCA Registry endpoints (`/api/tefca/registry/*`) — the newest surface

Router-gated with `require_role("reviewer")`. 19 routes:

**Reads:** `GET /stats`, `/entities` (+filters+`q`+paginate), `/entities/{id}` (detail), `/qhins`, `/participants`, `/hierarchy` (lazy roots), `/entities/{id}/children`, `/entities/{id}/hierarchy`, `/search`, `/findings`, `/entities/{id}/findings`, `/verification-jobs`, `/verification-jobs/{id}`
**Verification (writes):** `POST /entities/{id}/verify`, `POST /verify` (bulk, `senior_analyst`)
**Import (writes):** `POST /import/fhir-bundle`, `POST /import/csv`, `GET /import/history`, `GET /import/{batch_id}`

## Authentication posture (endpoint level)

| Pattern | Count |
|---|---:|
| Endpoints using `Depends(get_current_user)` | 225 |
| Endpoints using `Depends(require_role(...))` | 80 |
| Routers with **router-level** auth dependency (e.g. TEFCA legacy + registry) | 2 |
| Rate limiting | Global `RateLimitMiddleware` (all) + stricter per-endpoint limits on auth |

**Observation (for Part 8):** ~305 of 411 endpoints carry an explicit auth dependency. The remaining ~106 include intentionally-public routes (`/health`, `/api/auth/*` login/signup, bulletin public reads) — but this delta should be enumerated in the Security review to confirm none are unintentionally unauthenticated.

## Cross-cutting

- **File uploads:** 17 modules accept `UploadFile`; a multi-layer scanner (`app/services/file_scanner.py`) runs before processing (magic bytes, dangerous content, size, CSV/JSON structure, SHA-256).
- **External calls originating from endpoints:** Anthropic, OpenAI, NPPES, LEIE, PECOS, SAM.gov, RCE/Sequoia FHIR, SendGrid.
- **Raw SQL:** 149 `text(...)` usages across the codebase — an injection surface to review in Part 8 (most appear parameterized; startup uses `text()` for `ALTER TABLE … IF NOT EXISTS` schema repair).
