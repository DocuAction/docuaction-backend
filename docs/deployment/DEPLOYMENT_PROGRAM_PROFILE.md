# Deployment Program Profile (module gate)

STATUS = PENDING_REVIEW · introduced 2026-09-14 (Release 1.0 structural hardening) · runtime behaviour unchanged unless a profile is selected.

## What it is

One codebase serves three product domains. A deployment declares which program it serves, and the backend refuses — at the edge, before routing — every request for a module that program does not include. The frontend is built for the same program and neither offers nor routes to those modules, and its GovCon API client sends nothing for them.

| Setting | Where | Values | Default |
|---|---|---|---|
| `DOCUACTION_PROGRAM` | backend environment (App Service setting) | `ALL`, `TEFCA_ARC` | `ALL` (every module, identical to pre-profile behaviour) |
| `DOCUACTION_MODULES_DISABLED` | backend environment | comma-separated module ids | empty |
| `DOCUACTION_MODULES_ENABLED` | backend environment | comma-separated module ids re-enabled inside the profile | empty |
| `NEXT_PUBLIC_PROGRAM` | frontend build environment (inlined like `NEXT_PUBLIC_API_URL`) | `ALL`, `TEFCA_ARC` | `ALL` |

An unrecognised program name fails closed to `TEFCA_ARC` on both sides and logs an error; a typo in a federal deployment can only narrow exposure.

## Modules and paths (`app/core/modules.py`)

| Module id | Domain | Backend paths | Frontend routes |
|---|---|---|---|
| `tefca_arc` | TEFCA | `/api/tefca/*`, `/api/reports/*`, `/api/learning/*`, `/api/v1/usps/*` | `/tefca-arc/*`, `/tefca-registry/*`, `/tefca-dashboard` |
| `govcon` | GOVCON | `/api/{ats,rfq,rfqs,quotes,deals,deal-registrations,deal-tracker,invoices,finance,opportunities,proposal-library,projects,staffing,suppliers,customers,products,pricing,company-profile,agency-contacts,support,bom,ai}/*` and the same without `/api`, plus bare `/export`, `/intel` | 24 GovCon routes (RFQs … Users) |
| `healthcare_claims` | Core-optional | `/api/healthcare/*` | `/healthcare` |
| `case_management` | Core-optional | `/api/v1/case-management/*` | `/case-management` |
| `bulletin_intelligence` | Core-optional | `/api/v1/bulletin/*` | `/bulletin` |
| `migration_intelligence` | Core-optional | `/api/migration/*` | `/migration` |
| `meeting_intelligence` | Core-optional | `/api/meetings/*`, `/api/intel/*`, `/api/transcribe` | — |
| `document_automation` | Core-optional | `/api/automations/*`, `/api/compare-documents/*`, `/api/comparison-modes`, `/api/document-memory/*`, `/api/extract-structured/*`, `/api/extraction-templates` | `/compare`, `/analytics`, `/validation`, `/decisions`, `/documents`, `/actions-inbox` (frontend keeps these in TEFCA_ARC: they are the Core platform surface the TEFCA shell links to) |
| Core (implicit) | CORE | everything else: `/api/auth`, `/api/admin`, `/api/documents`, `/api/outputs`, `/api/enterprise`, `/api/decisions`, `/api/validation`, `/api/sla`, `/api/templates`, `/api/export`, `/api/config`, `/health` … | public pages, `/dashboard`, `/settings`, `/profile`, `/admin/users` |

Profile `TEFCA_ARC` enables `tefca_arc` (+ implicit Core) on the backend; the frontend additionally keeps `document_automation` routes.

## Semantics

- Disabled module → `404 {"error":"Not Found","code":"NOT_FOUND","request_id":…}` for every method, identical to an unmounted route, before authentication runs. A module that is absent from a deployment is not discoverable from it.
- Enabled module → unchanged: `401` unauthenticated, `403` authenticated but below role, `200` otherwise.
- `GET /api/config` (public) reports `program` and `enabled_modules` as module ids only, so a frontend built for one program can detect a backend serving another. The modules a deployment does *not* serve are not published (they answer 404 and are not meant to be discoverable) — closure 2026-09-14.
- `GET /health` reports a profile-gated module as `"disabled"` rather than `"active"` — closure 2026-09-14.
- Background services follow the profile: the Bulletin Intelligence store, hydration and scheduler do not start when `bulletin_intelligence` is not served, whatever `ENABLE_SCHEDULER` says — closure 2026-09-14. The TEFCA schedulers (PPEF, export, delivery) are unaffected.

## Verification (2026-09-14, local synthetic runtime, backend on the sprint branch)

Two instances of the same build on the same ephemeral database: profile `ALL` on :8014 and `TEFCA_ARC` on :8015. Synthetic users for admin, program_manager, reviewer, qalead, viewer.

| Request (with a valid token of each role) | `ALL` | `TEFCA_ARC` |
|---|---|---|
| `GET /api/ats/jobs`, `/ats/jobs`, `/api/ats/candidates`, `/api/rfq/1/bom`, `/api/deal-registrations` | 404 (routers not mounted) | 404 (gate) |
| `GET /api/healthcare/metrics` | 200 | 404 |
| `GET /api/migration/status` | 200 | 404 |
| `GET /api/v1/bulletin/latest`, `/api/v1/case-management/patients`, `/api/meetings/domains` | 404 / 401 per route | 404 |
| `GET /api/tefca/status`, `/api/tefca/registry/entities`, `/api/reports`, `/api/learning/TEFCA_ARC` | 200 | 200 |
| `GET /api/auth/me`, `/api/documents`, `/api/enterprise/audit` | 200 | 200 |
| `GET /api/admin/users` (reviewer / PM) | 403 | 403 (RBAC unchanged) |
| unauthenticated `GET /api/ats/jobs`, `/api/healthcare/metrics` | 404 / 401 | 404 |

Tests: `tests/test_module_gate.py` (18), `tests/test_rate_limit_roles_and_preflight.py` (10), `tests/test_public_endpoint_disclosure.py` (7).

## What this is not

- Not build-time exclusion: GovCon code is still in the TEFCA bundle (`Not available in this deployment` is rendered instead of the page, and the GovCon API client sends nothing). Excluding the code is Release 1.1 work (`docs/architecture/FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT.md`).
- Not a role change: roles, claims and RBAC mappings are untouched.
- Not data deletion: GovCon tables and code remain; they are simply unreachable in a TEFCA deployment.
