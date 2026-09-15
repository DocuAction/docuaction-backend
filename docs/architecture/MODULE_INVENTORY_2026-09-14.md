# Module inventory — 2026-09-14

Generated from the structural-hardening worktrees (frontend PR #42 head 52f91d5 + sprint changes; backend main 75383cf + sprint changes) by the sprint inventory script. Companion to `FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT.md`. Not hand-edited.

## Appendix A. Frontend route inventory (generated 2026-09-14 from `src/app/**/page.*` at the sprint head)

Total routes: 80. Domains: ADMIN 2, CORE 3, CORE_OPTIONAL 9, GOVCON 25, PUBLIC 10, TEFCA 31.

| Route | Module owner | Domain | Shell | Auth | Role requirement | API dependencies (first 6) | Visible in current TEFCA shell | Should ship in TEFCA |
|---|---|---|---|---|---|---|---|---|
| /actions-inbox | document_automation | CORE_OPTIONAL | standalone | NO | server-side per endpoint | /api/documents, /api/enterprise/actions, /api/enterprise/actions/, /api/enterprise/decisio | YES | YES |
| /admin/users | core | ADMIN | AppLayout | YES | server-side per endpoint |  | YES | YES |
| /agency-contacts | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /ai-analyze | govcon | GOVCON | AppShell | YES | shell role gates | /api/ai/generate-proposal-deep, /api/ai/generate-proposal-quick | NO | NO |
| /analytics | document_automation | CORE_OPTIONAL | standalone | NO | server-side per endpoint | /api/decisions/feedback/stats, /api/enterprise/actions, /api/enterprise/audit, /api/enterp | YES | YES |
| /ats-agent | govcon | GOVCON | AppShell | YES | Admin, Manager, Staffing Manager, Recrui |  | NO | NO |
| /ats | govcon | GOVCON | AppShell | YES | Admin,Manager,Staffing Manager,Recruiter |  | NO | NO |
| /auth/callback | public | PUBLIC | standalone | NO | server-side per endpoint |  | NO | PUBLIC |
| /bench-sales | govcon | GOVCON | AppShell | YES | Admin, Manager, Staffing Manager, Recrui |  | NO | NO |
| /bom | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /bulletin | bulletin_intelligence | CORE_OPTIONAL | AppLayout | YES | server-side per endpoint |  | NO | NO |
| /case-management | case_management | CORE_OPTIONAL | AppLayout | YES | server-side per endpoint |  | NO | NO |
| /company-profile | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /compare | document_automation | CORE_OPTIONAL | standalone | NO | server-side per endpoint | /api/compare-documents, /api/documents, /api/extract-structured | YES | YES |
| /contact | public | PUBLIC | standalone | NO | server-side per endpoint |  | NO | PUBLIC |
| /dashboard | core | CORE | AppLayout | YES | server-side per endpoint | /api/auth/me, /api/auth/refresh, /api/documents, /api/documents/, /api/documents/upload, / | YES | YES |
| /deal-tracker | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /deal | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /decisions | document_automation | CORE_OPTIONAL | standalone | NO | server-side per endpoint | /api/decisions/defensibility, /api/enterprise/decisions, /api/enterprise/decisions/ | YES | YES |
| /documents | document_automation | CORE_OPTIONAL | standalone | NO | server-side per endpoint | /api/documents, /api/documents/upload, /api/export/, /api/intelligence/outputs, /api/intel | YES | YES |
| /finance | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /forgot-password | public | PUBLIC | standalone | NO | server-side per endpoint |  | NO | PUBLIC |
| /healthcare | healthcare_claims | CORE_OPTIONAL | standalone | NO | server-side per endpoint | /api/documents, /api/outputs, /api/transcribe | NO | NO |
| /intel | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /intelligence | core | CORE | standalone | NO | server-side per endpoint | /api/export/, /api/intelligence/auto-process/, /api/intelligence/document/, /api/intellige | YES | YES |
| /invoices | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /login | public | PUBLIC | standalone | YES | server-side per endpoint |  | NO | PUBLIC |
| /manage-customers | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /manage-products | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /manage-suppliers | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /manage-users | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /opportunities | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| / | public | PUBLIC | standalone | YES | server-side per endpoint |  | NO | PUBLIC |
| /pricing | govcon | GOVCON | standalone | NO | server-side per endpoint |  | NO | PUBLIC |
| /pricing | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | PUBLIC |
| /product | public | PUBLIC | standalone | NO | server-side per endpoint |  | NO | PUBLIC |
| /projects | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /proposal-library | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /quotes | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /register | public | PUBLIC | standalone | YES | server-side per endpoint |  | NO | PUBLIC |
| /reset-password | public | PUBLIC | standalone | NO | server-side per endpoint |  | NO | PUBLIC |
| /rfqs | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /settings | core | ADMIN | standalone | NO | server-side per endpoint |  | YES | YES |
| /signup | public | PUBLIC | standalone | NO | server-side per endpoint |  | NO | PUBLIC |
| /staffing | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | NO |
| /support | govcon | GOVCON | AppShell | YES | shell role gates |  | NO | PUBLIC |
| /tefca-arc/administration | tefca_arc | TEFCA | module-layout | YES | { value: viewer, label: Viewer (1) },
   | /api/admin/users, /api/admin/users/${rejecting.user.id}/reject, /api/admin/users/${user.id | YES | YES |
| /tefca-arc/analytics | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/dashboard/summary, /api/tefca/dashboard/trends, /api/tefca/qa/sla, /api/tefca/r | YES | YES |
| /tefca-arc/assignment | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/rce/deliveries, /api/tefca/workflow/deliveries/${encode, /api/tefca/workflow/di | YES | YES |
| /tefca-arc/audit | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/audit-trail | YES | YES |
| /tefca-arc/configuration | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/status | YES | YES |
| /tefca-arc/connectors | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/connectors/cms-systems, /api/tefca/status | YES | YES |
| /tefca-arc/cycles | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/reviews, /api/tefca/sampling-runs, /api/v1/tefca/cycles, /api/v1/tefca/mock/ent | YES | YES |
| /tefca-arc/dashboard | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /tefca-arc/decisions | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/reviews, /api/tefca/reviews/${d.id}, /api/tefca/reviews/${d.id}/decision, /api/ | YES | YES |
| /tefca-arc/deliveries | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/rce/deliveries/${encode, /api/tefca/rce/delivery-jobs, /api/tefca/workflow/deli | YES | YES |
| /tefca-arc/findings | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/dashboard/summary, /api/tefca/discrepancy-taxonomy, /api/tefca/findings | YES | YES |
| /tefca-arc/help | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/learning/ | YES | YES |
| /tefca-arc/import | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/entities/upload, /api/tefca/import/history | YES | YES |
| /tefca-arc/insights | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /tefca-arc/my-reviews | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/workflow/my-reviews${query} | YES | YES |
| /tefca-arc/operations | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/arc/operations/analyst-workload, /api/tefca/arc/operations/cases/${encode, /api | YES | YES |
| /tefca-arc | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/dashboard/notifications, /api/tefca/dashboard/recent-activity, /api/tefca/dashb | YES | YES |
| /tefca-arc/priority | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/priority, /api/tefca/qa/sla | YES | YES |
| /tefca-arc/qa | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/qa/audit, /api/tefca/qa/health, /api/tefca/qa/score, /api/tefca/qa/sweep | YES | YES |
| /tefca-arc/reports | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/reports, /api/reports/${r.report_id}/${fmt}, /api/reports/${r.report_id}/package, /ap | YES | YES |
| /tefca-arc/reviews | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/qa/audit, /api/tefca/reports/export, /api/tefca/reviews, /api/tefca/reviews/${r | YES | YES |
| /tefca-arc/search | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/search | YES | YES |
| /tefca-arc/trust-center | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/status | YES | YES |
| /tefca-arc/validation | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/reviews, /api/tefca/status, /api/v1/tefca/mock/entities, /api/v1/tefca/queue/ti | YES | YES |
| /tefca-arc/workspace | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint | /api/tefca/rce/field-map, /api/tefca/workflow/reviews/${encode | YES | YES |
| /tefca-dashboard | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /tefca-registry/entities | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /tefca-registry/entity | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /tefca-registry/issues | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /tefca-registry | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /tefca-registry/verification | tefca_arc | TEFCA | module-layout | YES | server-side per endpoint |  | YES | YES |
| /trust | core | CORE | standalone | NO | server-side per endpoint | /api/decisions/feedback/stats, /api/enterprise/audit, /api/enterprise/decisions, /api/ente | YES | YES |
| /validation | document_automation | CORE_OPTIONAL | standalone | NO | server-side per endpoint | /api/intelligence/outputs/, /api/validation/queue, /api/validation/review/, /api/validatio | YES | YES |
| /verify-email | public | PUBLIC | standalone | NO | server-side per endpoint |  | NO | PUBLIC |

## Appendix B. Backend endpoint inventory (generated from `APIRouter` declarations at the sprint head)

Total declared endpoints: 646. Mounted in `app/main.py`: 405. Declared but NOT mounted: 241 (the entire GovCon `app/routers/` package plus `app/api/{admin,compliance,cross_intel_routes,governance_routes,security}.py`).

Per module (mounted / declared):

- `bulletin_intelligence`: 60 / 60
- `case_management`: 22 / 22
- `core`: 161 / 180
- `document_automation`: 12 / 12
- `govcon`: 0 / 204
- `healthcare_claims`: 9 / 9
- `meeting_intelligence`: 5 / 10
- `migration_intelligence`: 12 / 12
- `tefca_arc`: 124 / 137

Unmounted router files (file, declared prefix, routes):

- `app/api/admin.py` prefix `/api/admin` — 3 routes
- `app/api/auth_endpoints.py` prefix `/api/auth` — 2 routes
- `app/api/compliance.py` prefix `/api/user` — 2 routes
- `app/api/cross_intel_routes.py` prefix `/api/intel` — 5 routes
- `app/api/governance_routes.py` prefix `/api/governance` — 10 routes
- `app/api/security.py` prefix `/api/security` — 2 routes
- `app/core/learning/routes.py` prefix `/api/learning` — 13 routes
- `app/routers/agency_contacts.py` prefix `/agency-contacts` — 4 routes
- `app/routers/ai_analysis.py` prefix `/ai` — 10 routes
- `app/routers/ats.py` prefix `/ats` — 26 routes
- `app/routers/ats_agent.py` prefix `/ats/ai-agent` — 14 routes
- `app/routers/bench.py` prefix `/ats/bench` — 11 routes
- `app/routers/bom.py` prefix `/rfq/{rfq_id}/bom` — 3 routes
- `app/routers/company_profile.py` prefix `/company-profile` — 3 routes
- `app/routers/customers.py` prefix `/customers` — 5 routes
- `app/routers/deal_regs.py` prefix `/deal-registrations` — 3 routes
- `app/routers/deal_tracker.py` prefix `/deal-tracker` — 5 routes
- `app/routers/deals.py` prefix `/deals` — 10 routes
- `app/routers/export.py` prefix `/export` — 3 routes
- `app/routers/finance.py` prefix `/finance` — 14 routes
- `app/routers/intel.py` prefix `/intel` — 14 routes
- `app/routers/invoices.py` prefix `/invoices` — 5 routes
- `app/routers/opportunities.py` prefix `/opportunities` — 13 routes
- `app/routers/pricing.py` prefix `/pricing` — 1 routes
- `app/routers/products.py` prefix `/products` — 3 routes
- `app/routers/projects.py` prefix `/projects` — 9 routes
- `app/routers/proposal_library.py` prefix `/proposal-library` — 6 routes
- `app/routers/quotes.py` prefix `/quotes` — 6 routes
- `app/routers/rfq.py` prefix `/rfq` — 4 routes
- `app/routers/staffing.py` prefix `/staffing` — 16 routes
- `app/routers/suppliers.py` prefix `/suppliers` — 12 routes
- `app/routers/support.py` prefix `/support` — 4 routes

| Endpoint | File | Module | Mounted | Auth dependency seen | Role |
|---|---|---|---|---|---|
| GET /api/tefca/methodology/status | app/Tefca/learning_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/methodology/categories | app/Tefca/learning_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/methodology/categories/{category} | app/Tefca/learning_routes.py | tefca_arc | YES | YES |  |
| GET /api/v1/tefca/connectors/status | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/mock/entities | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/cycles | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/cycles | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/validate/entity | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/evidence/generate | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/validate/batch | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/validate/status/{cycle_id} | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/queue/tier2 | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/queue/tier3 | app/Tefca/routes.py | core | YES | YES |  |
| PATCH /api/v1/tefca/queue/{record_id}/classify | app/Tefca/routes.py | core | YES | YES |  |
| PATCH /api/v1/tefca/queue/{record_id}/escalate | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/priority-cases | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/priority-cases | app/Tefca/routes.py | core | YES | YES |  |
| PATCH /api/v1/tefca/priority-cases/{case_id} | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reports/weekly/{cycle_id} | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reports/final/{cycle_id} | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/validate-sample | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/dashboard/summary | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/dashboard/trends | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/status | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/search | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports/export | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/demo/run-cycle | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/admin/seed-mock-data | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/methodology | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/discrepancy-taxonomy | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reviews/run-sample | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reviews/{review_id}/execute | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/sampling-runs | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reports/weekly | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reports/final | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports/{report_id} | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports/{report_id}/csv | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports/{report_id}/pdf | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports/{report_id}/docx | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reports/biweekly | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/reports/quarterly | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reviews/new-submissions | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/priority/create | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/priority/{case_id}/execute | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/priority | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/priority/{case_id} | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/priority/{case_id}/report | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/priority/quarterly-report | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/health | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/connector-health | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/audit | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/qa/validate-review/{review_id} | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/score | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/qa/validate-evidence/{review_id} | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/report-gate | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/evidence-summary | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/sampling-validation | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/internal-consistency | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/inter-rater | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/statistical | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/golden-records | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/regression | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/sla | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/sweep | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/alerts | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/qa/alerts/test | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/qa/report | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/qa/audit/export | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reviews | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reviews/{review_id} | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/findings | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/findings/{finding_id} | app/Tefca/routes.py | core | YES | YES |  |
| POST /api/v1/tefca/entities/upload | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/import/history | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/v1/tefca/reports/{report_id}/download | app/Tefca/routes.py | core | YES | YES |  |
| GET /api/admin/retention/config | app/api/admin.py | core | NO (router not mounted) | YES |  |
| POST /api/admin/retention/run | app/api/admin.py | core | NO (router not mounted) | YES |  |
| GET /api/admin/system/status | app/api/admin.py | core | NO (router not mounted) | YES |  |
| GET /api/admin/areas | app/api/admin_users.py | core | YES | CHECK |  |
| GET /api/admin/users | app/api/admin_users.py | core | YES | CHECK |  |
| GET /api/admin/users/{user_id}/activity | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/admin/users/invite | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/admin/users | app/api/admin_users.py | core | YES | CHECK |  |
| PATCH /api/admin/users/{user_id}/role | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/admin/users/bulk-role | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/admin/users/{user_id}/approve | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/admin/users/{user_id}/reject | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/admin/users/{user_id}/resend-verification | app/api/admin_users.py | core | YES | CHECK |  |
| GET /api/admin/users/pending | app/api/admin_users.py | core | YES | CHECK |  |
| PATCH /api/admin/users/{user_id}/permissions | app/api/admin_users.py | core | YES | CHECK |  |
| PATCH /api/admin/users/{user_id} | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/admin/users/{user_id}/set-password | app/api/admin_users.py | core | YES | CHECK |  |
| DELETE /api/admin/users/{user_id} | app/api/admin_users.py | core | YES | CHECK |  |
| POST /api/transcribe | app/api/audio_routes.py | meeting_intelligence | YES | CHECK |  |
| POST /api/auth/refresh | app/api/auth_endpoints.py | core | NO (router not mounted) | CHECK |  |
| GET /api/auth/saml/config | app/api/auth_endpoints.py | core | NO (router not mounted) | CHECK |  |
| GET /api/auth/sso/status | app/api/azure_auth_routes.py | core | YES | CHECK |  |
| GET /api/auth/login/azure | app/api/azure_auth_routes.py | core | YES | CHECK |  |
| GET /api/auth/callback/azure | app/api/azure_auth_routes.py | core | YES | CHECK |  |
| DELETE /api/user/hard-delete | app/api/compliance.py | core | NO (router not mounted) | CHECK |  |
| GET /api/user/data-export | app/api/compliance.py | core | NO (router not mounted) | CHECK |  |
| GET /api/intel/dashboard | app/api/cross_intel_routes.py | meeting_intelligence | NO (router not mounted) | CHECK |  |
| POST /api/intel/search | app/api/cross_intel_routes.py | meeting_intelligence | NO (router not mounted) | CHECK |  |
| GET /api/intel/source/{output_id} | app/api/cross_intel_routes.py | meeting_intelligence | NO (router not mounted) | CHECK |  |
| GET /api/intel/versions/{document_id} | app/api/cross_intel_routes.py | meeting_intelligence | NO (router not mounted) | CHECK |  |
| GET /api/intel/history | app/api/cross_intel_routes.py | meeting_intelligence | NO (router not mounted) | CHECK |  |
| POST /api/decisions/bank/create | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| GET /api/decisions/bank | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| GET /api/decisions/bank/{decision_id} | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| POST /api/decisions/bank/{decision_id}/stakeholder | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| POST /api/decisions/extract/{output_id} | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| POST /api/decisions/feedback | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| GET /api/decisions/feedback/stats | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| GET /api/decisions/feedback/{output_id} | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| GET /api/decisions/provenance/{output_id} | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| POST /api/decisions/defensibility | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| GET /api/decisions/memory | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| GET /api/decisions/status | app/api/decision_intel_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/jobs/create | app/api/enterprise_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/jobs/{job_id}/process | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/jobs | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/decisions | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/decisions/{decision_id} | app/api/enterprise_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/decisions/{decision_id}/approve | app/api/enterprise_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/decisions/{decision_id}/reject | app/api/enterprise_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/decisions/{decision_id}/review | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/actions | app/api/enterprise_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/actions/{action_id}/approve | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/queue | app/api/enterprise_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/queue/process | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/audit | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/audit/entity/{entity_id} | app/api/enterprise_routes.py | core | YES | CHECK |  |
| POST /api/enterprise/tenant/setup | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/tenant | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/enterprise/status | app/api/enterprise_routes.py | core | YES | CHECK |  |
| GET /api/export/{output_id}/{format} | app/api/export.py | core | YES | CHECK |  |
| POST /api/governance/gate | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| POST /api/governance/pipeline | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| POST /api/governance/conflicts | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| GET /api/governance/conflicts/auto | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| GET /api/governance/source-map/{output_id} | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| GET /api/governance/certificate/{audit_id} | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| GET /api/governance/policy | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| GET /api/governance/policies | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| GET /api/governance/status | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| POST /api/governance/validate | app/api/governance_routes.py | core | NO (router not mounted) | CHECK |  |
| POST /api/healthcare/claims/process | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| POST /api/healthcare/claims/process-text | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| GET /api/healthcare/claims/{claim_id} | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| GET /api/healthcare/claims | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| POST /api/healthcare/claims/{claim_id}/appeal | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| GET /api/healthcare/metrics | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| POST /api/healthcare/claims/{claim_id}/validate | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| GET /api/healthcare/fwa/{claim_id} | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| GET /api/healthcare/revenue/{claim_id} | app/api/healthcare_claims_routes.py | healthcare_claims | YES | CHECK |  |
| GET /api/intelligence/outputs | app/api/intelligence_routes.py | core | YES | CHECK |  |
| GET /api/intelligence/outputs/{output_id} | app/api/intelligence_routes.py | core | YES | CHECK |  |
| GET /api/intelligence/document/{document_id} | app/api/intelligence_routes.py | core | YES | CHECK |  |
| POST /api/intelligence/auto-process/{document_id} | app/api/intelligence_routes.py | core | YES | CHECK |  |
| POST /api/meetings/process | app/api/meeting_routes.py | meeting_intelligence | YES | YES |  |
| POST /api/meetings/approve/{meeting_id} | app/api/meeting_routes.py | meeting_intelligence | YES | YES |  |
| GET /api/meetings/domains | app/api/meeting_routes.py | meeting_intelligence | YES | YES |  |
| POST /api/migration/projects | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| GET /api/migration/projects | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| GET /api/migration/projects/{project_id} | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| POST /api/migration/schemas/upload | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| GET /api/migration/schemas/{schema_id} | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| GET /api/migration/schemas/{schema_id}/fields | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| POST /api/migration/mappings/generate | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| POST /api/migration/mappings/{mapping_id}/approve | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| POST /api/migration/mappings/{mapping_id}/reject | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| POST /api/migration/mappings/{mapping_id}/conflict | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| GET /api/migration/manifests/{project_id} | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| GET /api/migration/status | app/api/migration_routes.py | migration_intelligence | YES | YES |  |
| POST /api/auth/forgot-password | app/api/password_reset.py | core | YES | CHECK |  |
| POST /api/auth/reset-password | app/api/password_reset.py | core | YES | CHECK |  |
| GET /api/plan/usage | app/api/plans.py | core | YES | CHECK |  |
| GET /api/plan/info | app/api/plans.py | core | YES | CHECK |  |
| POST /api/plan/upgrade | app/api/plans.py | core | YES | CHECK |  |
| POST /api/auth/signup | app/api/routes.py | core | YES | CHECK |  |
| POST /api/auth/verify-email | app/api/routes.py | core | YES | CHECK |  |
| POST /api/auth/login | app/api/routes.py | core | YES | CHECK |  |
| POST /api/auth/logout | app/api/routes.py | core | YES | CHECK |  |
| GET /api/auth/me | app/api/routes.py | core | YES | CHECK |  |
| POST /api/process | app/api/routes.py | core | YES | CHECK |  |
| POST /api/process-file | app/api/routes.py | core | YES | CHECK |  |
| POST /api/transcribe | app/api/routes.py | meeting_intelligence | YES | CHECK |  |
| POST /api/documents/upload | app/api/routes.py | core | YES | CHECK |  |
| GET /api/documents | app/api/routes.py | core | YES | CHECK |  |
| DELETE /api/documents/{doc_id} | app/api/routes.py | core | YES | CHECK |  |
| POST /api/outputs/generate/{doc_id} | app/api/routes.py | core | YES | CHECK |  |
| GET /api/outputs | app/api/routes.py | core | YES | CHECK |  |
| GET /api/outputs/{output_id} | app/api/routes.py | core | YES | CHECK |  |
| GET /api/security/residency | app/api/security.py | core | NO (router not mounted) | CHECK |  |
| GET /api/security/status | app/api/security.py | core | NO (router not mounted) | CHECK |  |
| GET /api/sla/check | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/sla/decision/{decision_id} | app/api/sla_routes.py | core | YES | CHECK |  |
| POST /api/sla/approve | app/api/sla_routes.py | core | YES | CHECK |  |
| POST /api/sla/outcome | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/sla/outcomes | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/sla/outcome/{decision_id} | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/sla/history/{decision_id} | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/sla/notifications | app/api/sla_routes.py | core | YES | CHECK |  |
| POST /api/sla/notifications/read | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/sla/rights | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/sla/status | app/api/sla_routes.py | core | YES | CHECK |  |
| GET /api/templates/ | app/api/templates.py | core | YES | CHECK |  |
| POST /api/templates/ | app/api/templates.py | core | YES | CHECK |  |
| DELETE /api/templates/{template_id} | app/api/templates.py | core | YES | CHECK |  |
| GET /api/validation/queue | app/api/validation_routes.py | core | YES | CHECK |  |
| POST /api/validation/review/{item_id} | app/api/validation_routes.py | core | YES | CHECK |  |
| GET /api/validation/stats | app/api/validation_routes.py | core | YES | CHECK |  |
| POST /api/compare-documents | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/compare-documents/{comparison_id} | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/compare-documents | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/comparison-modes | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| POST /api/extract-structured | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/extract-structured/{extraction_id} | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/extraction-templates | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| POST /api/automations/rules | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/automations/rules | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| DELETE /api/automations/rules/{rule_id} | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/document-memory | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/document-memory/anomalies | app/api/wow_routes.py | document_automation | YES | CHECK |  |
| GET /api/v1/bulletin/download/{agency_id} | app/bulletin_intelligence/bulletin_download_routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/download-options/{agency_id} | app/bulletin_intelligence/bulletin_download_routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/download-excel/{agency_id} | app/bulletin_intelligence/bulletin_download_routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/briefings/{briefing_id}/excel | app/bulletin_intelligence/bulletin_download_routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/briefings/{briefing_id}/excel-qa | app/bulletin_intelligence/bulletin_download_routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/health | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/costs | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/profiles | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/profiles/seed | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/collect | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/upload-reviewed/{briefing_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/recipients | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/recipients | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| DELETE /api/v1/bulletin/recipients/{email} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/email-preview/{briefing_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/regenerate/{briefing_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/perigon/health | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/qa/google-news-compare | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/coverage/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/refresh/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/agencies | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/agencies | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/agencies/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/run/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/run/{agency_id}/sync | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/admin/purge-articles | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/admin/last-window/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/latest/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/latest/{agency_id}/preview | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/today/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/collect/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/send/{agency_id}/{briefing_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | YES |  |
| GET /api/v1/bulletin/queue/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/history/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/audit/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/runs/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/runs/{agency_id}/{run_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/quality/latest | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/sources | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/sources/health | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/sources/missing | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/sources/load-catalog | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/sources/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | YES |  |
| POST /api/v1/bulletin/sources/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | YES |  |
| GET /api/v1/bulletin/coverage-assurance/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | YES |  |
| GET /api/v1/bulletin/source-classifications | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/pws-coverage/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | YES |  |
| POST /api/v1/bulletin/briefings/{briefing_id}/approve | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/briefings/{briefing_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/briefings/{briefing_id}/preview | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/briefings/{briefing_id}/docx | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/archive/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/archive/{agency_id}/stats | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/archive/{agency_id}/clips | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | YES |  |
| POST /api/v1/bulletin/llm-visibility/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/briefings/{briefing_id}/pdf | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/run/{agency_id}/preview | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/demo/{agency_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| POST /api/v1/bulletin/generate-word/{briefing_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/bulletin/generate-word/{briefing_id} | app/bulletin_intelligence/routes.py | bulletin_intelligence | YES | CHECK |  |
| GET /api/v1/case-management/dashboard/stats | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/patients | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/patients | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/patients/{patient_id} | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/notes/voice-to-note | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/notes/generate | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/notes/tcm | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/notes | app/case_management/routes.py | case_management | YES | CHECK |  |
| PATCH /api/v1/case-management/notes/{note_id}/approve | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/care-plans/generate | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/care-plans | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/discharge/generate | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/education/generate | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/education/topics | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/sdoh/assess | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/government/cases/generate | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/government/cases | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/billing/determine-code | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/billing/cpt-reference | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/billing/monthly-summary | app/case_management/routes.py | case_management | YES | CHECK |  |
| POST /api/v1/case-management/meetings/generate-minutes | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/v1/case-management/info | app/case_management/routes.py | case_management | YES | CHECK |  |
| GET /api/learning/programs | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program} | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/search | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/navigation | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/modules/{slug} | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/modules/{slug}/{lesson_slug} | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/help/{key:path} | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/paths | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/paths/{slug} | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/traceability | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/library | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/glossary | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| GET /api/learning/{program}/prohibited | app/core/learning/routes.py | tefca_arc | NO (router not mounted) | YES |  |
| POST /api/reports/generate | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/ | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/{report_id} | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/{report_id}/release | app/reports/routes.py | tefca_arc | YES | YES |  |
| POST /api/reports/{report_id}/release | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/{report_id}/package | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/{report_id}/html | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/{report_id}/pdf | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/{report_id}/docx | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/{report_id}/csv | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/health/engine | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/sow | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/sow/{deliverable} | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/artifacts/{report_id} | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/artifacts/{report_id}/download | app/reports/routes.py | tefca_arc | YES | YES |  |
| POST /api/reports/exports/onc-review-workbook | app/reports/routes.py | tefca_arc | YES | YES |  |
| GET /api/reports/exports/jobs/{job_id} | app/reports/routes.py | tefca_arc | YES | YES |  |
| POST /agency-contacts/ | app/routers/agency_contacts.py | govcon | NO (router not mounted) | YES |  |
| GET /agency-contacts/ | app/routers/agency_contacts.py | govcon | NO (router not mounted) | YES |  |
| GET /agency-contacts/{contact_id} | app/routers/agency_contacts.py | govcon | NO (router not mounted) | YES |  |
| PATCH /agency-contacts/{contact_id} | app/routers/agency_contacts.py | govcon | NO (router not mounted) | YES |  |
| GET /ai/check-key | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| POST /ai/analyze-text | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| POST /ai/analyze-file | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| POST /ai/generate-response | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| POST /ai/generate-proposal-quick | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| POST /ai/generate-proposal-deep | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| POST /ai/upload-and-propose | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| POST /ai/export-pdf | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| GET /ai/sam-search | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| GET /ai/labor-categories | app/routers/ai_analysis.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/dashboard | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/jobs | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/jobs | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/jobs/{job_id} | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/jobs/{job_id} | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/candidates | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/candidates | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/candidates/{cid} | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/candidates/{cid} | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/applications | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/applications | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/applications/{app_id}/status | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/bench | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/bench | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/bench/{bench_id} | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/activities | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/public/jobs | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/public/apply | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/import/oorwin | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/access-check | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/submissions | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/submissions | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/submissions/{sub_id} | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/reports/recruiter-performance | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/reports/sales-performance | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/candidates/{cid}/parse-resume | app/routers/ats.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/analyze-resume | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/compare | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/ai-agent/quick-match/{candidate_id} | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/submission-package/{candidate_id} | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/batch-summary | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/create-task | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/outcomes | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/ai-agent/outcomes | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/ai-agent/learning-stats | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/ai-agent/memory | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/ai-agent/memory/{memory_id} | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/ai-agent/scope | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/job-search | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/ai-agent/job-search/save | app/routers/ats_agent.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/bench/dashboard | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/bench/discovery | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| POST /ats/bench/outreach | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/bench/outreach | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/bench/outreach/{log_id} | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/bench/submissions | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/bench/submissions/{sub_id}/status | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/bench/reminders | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/bench/follow-ups | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| PATCH /ats/bench/follow-ups/{fup_id} | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| GET /ats/bench/pipeline | app/routers/bench.py | govcon | NO (router not mounted) | YES |  |
| POST /rfq/{rfq_id}/bom/ | app/routers/bom.py | govcon | NO (router not mounted) | YES |  |
| GET /rfq/{rfq_id}/bom/ | app/routers/bom.py | govcon | NO (router not mounted) | YES |  |
| PATCH /rfq/{rfq_id}/bom/{item_id} | app/routers/bom.py | govcon | NO (router not mounted) | YES |  |
| GET /company-profile/ | app/routers/company_profile.py | govcon | NO (router not mounted) | YES |  |
| POST /company-profile/ | app/routers/company_profile.py | govcon | NO (router not mounted) | YES |  |
| GET /company-profile/naics-summary | app/routers/company_profile.py | govcon | NO (router not mounted) | YES |  |
| POST /customers/ | app/routers/customers.py | govcon | NO (router not mounted) | YES |  |
| GET /customers/ | app/routers/customers.py | govcon | NO (router not mounted) | YES |  |
| GET /customers/{cid} | app/routers/customers.py | govcon | NO (router not mounted) | YES |  |
| PATCH /customers/{cid} | app/routers/customers.py | govcon | NO (router not mounted) | YES |  |
| DELETE /customers/{cid} | app/routers/customers.py | govcon | NO (router not mounted) | YES |  |
| POST /deal-registrations/ | app/routers/deal_regs.py | govcon | NO (router not mounted) | YES |  |
| GET /deal-registrations/ | app/routers/deal_regs.py | govcon | NO (router not mounted) | YES |  |
| GET /deal-registrations/{dr_id} | app/routers/deal_regs.py | govcon | NO (router not mounted) | YES |  |
| GET /deal-tracker/dashboard | app/routers/deal_tracker.py | govcon | NO (router not mounted) | YES |  |
| GET /deal-tracker/alerts | app/routers/deal_tracker.py | govcon | NO (router not mounted) | YES |  |
| POST /deal-tracker/{deal_id}/extend | app/routers/deal_tracker.py | govcon | NO (router not mounted) | YES |  |
| POST /deal-tracker/{deal_id}/mark-used | app/routers/deal_tracker.py | govcon | NO (router not mounted) | YES |  |
| POST /deal-tracker/{deal_id}/link-rfq | app/routers/deal_tracker.py | govcon | NO (router not mounted) | YES |  |
| GET /deals/workspace/{rfq_id} | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| GET /deals/search | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| POST /deals/comms | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| GET /deals/comms | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| POST /deals/tasks | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| GET /deals/tasks | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| PATCH /deals/tasks/{task_id} | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| GET /deals/agency-metrics | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| GET /deals/alerts | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| POST /deals/auto-tasks/{rfq_id} | app/routers/deals.py | govcon | NO (router not mounted) | YES |  |
| GET /export/rfqs | app/routers/export.py | govcon | NO (router not mounted) | YES |  |
| GET /export/quotes | app/routers/export.py | govcon | NO (router not mounted) | YES |  |
| GET /export/candidates | app/routers/export.py | govcon | NO (router not mounted) | YES |  |
| POST /finance/contracts | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| GET /finance/contracts | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| GET /finance/contracts/{cid} | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| PATCH /finance/contracts/{cid} | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| POST /finance/employees | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| GET /finance/employees | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| GET /finance/employees/{eid} | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| PATCH /finance/employees/{eid} | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| POST /finance/staffing | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| GET /finance/staffing | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| POST /finance/expenses | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| GET /finance/expenses | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| GET /finance/profit-summary | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| POST /finance/contracts/from-rfq/{rfq_id} | app/routers/finance.py | govcon | NO (router not mounted) | YES |  |
| POST /intel/products | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/products | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/products/suggest/{query} | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/price-history/{part_number} | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| POST /intel/price-history/record | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/supplier-metrics | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/supplier-metrics/recommend/{manufacturer} | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| POST /intel/tech-library | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/tech-library | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/tech-library/for-bom | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| POST /intel/purchase-orders | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/purchase-orders | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| PATCH /intel/purchase-orders/{po_id} | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| GET /intel/deal-dashboard | app/routers/intel.py | govcon | NO (router not mounted) | YES |  |
| POST /invoices/ | app/routers/invoices.py | govcon | NO (router not mounted) | YES |  |
| GET /invoices/ | app/routers/invoices.py | govcon | NO (router not mounted) | YES |  |
| GET /invoices/{invoice_id} | app/routers/invoices.py | govcon | NO (router not mounted) | YES |  |
| PATCH /invoices/{invoice_id}/status | app/routers/invoices.py | govcon | NO (router not mounted) | YES |  |
| GET /invoices/{invoice_id}/pdf | app/routers/invoices.py | govcon | NO (router not mounted) | YES |  |
| GET /opportunities/search/federal | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| GET /opportunities/search/state | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| GET /opportunities/state-portals | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| GET /opportunities/search/usaspending | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| POST /opportunities/match | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| GET /opportunities/ | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| POST /opportunities/save | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| PATCH /opportunities/{opp_id} | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| POST /opportunities/{opp_id}/create-rfq | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| DELETE /opportunities/{opp_id} | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| GET /opportunities/saved-searches | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| POST /opportunities/saved-searches | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| GET /opportunities/stats | app/routers/opportunities.py | govcon | NO (router not mounted) | YES |  |
| POST /pricing/calculate | app/routers/pricing.py | govcon | NO (router not mounted) | YES |  |
| POST /products/ | app/routers/products.py | govcon | NO (router not mounted) | YES |  |
| GET /products/ | app/routers/products.py | govcon | NO (router not mounted) | YES |  |
| GET /products/{product_id} | app/routers/products.py | govcon | NO (router not mounted) | YES |  |
| POST /projects/ | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| GET /projects/ | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| GET /projects/stats/summary | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| GET /projects/alerts | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| GET /projects/{project_id} | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| PATCH /projects/{project_id} | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| POST /projects/supplier-quotes | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| GET /projects/supplier-quotes | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| PATCH /projects/supplier-quotes/{sq_id} | app/routers/projects.py | govcon | NO (router not mounted) | YES |  |
| POST /proposal-library/ | app/routers/proposal_library.py | govcon | NO (router not mounted) | YES |  |
| POST /proposal-library/upload | app/routers/proposal_library.py | govcon | NO (router not mounted) | YES |  |
| GET /proposal-library/ | app/routers/proposal_library.py | govcon | NO (router not mounted) | YES |  |
| GET /proposal-library/{item_id} | app/routers/proposal_library.py | govcon | NO (router not mounted) | YES |  |
| DELETE /proposal-library/{item_id} | app/routers/proposal_library.py | govcon | NO (router not mounted) | YES |  |
| GET /proposal-library/search/relevant | app/routers/proposal_library.py | govcon | NO (router not mounted) | YES |  |
| POST /quotes/ | app/routers/quotes.py | govcon | NO (router not mounted) | YES |  |
| POST /quotes/{quote_id}/version | app/routers/quotes.py | govcon | NO (router not mounted) | YES |  |
| GET /quotes/{quote_id} | app/routers/quotes.py | govcon | NO (router not mounted) | YES |  |
| GET /quotes/ | app/routers/quotes.py | govcon | NO (router not mounted) | YES |  |
| POST /quotes/{quote_id}/submit | app/routers/quotes.py | govcon | NO (router not mounted) | YES |  |
| GET /quotes/{quote_id}/pdf | app/routers/quotes.py | govcon | NO (router not mounted) | YES |  |
| POST /rfq/ | app/routers/rfq.py | govcon | NO (router not mounted) | YES |  |
| GET /rfq/ | app/routers/rfq.py | govcon | NO (router not mounted) | YES |  |
| GET /rfq/{rfq_id} | app/routers/rfq.py | govcon | NO (router not mounted) | YES |  |
| PATCH /rfq/{rfq_id} | app/routers/rfq.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/public/jobs | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/public/jobs/{job_id} | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| POST /staffing/public/apply | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| POST /staffing/jobs | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/jobs | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/jobs/{job_id} | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| PATCH /staffing/jobs/{job_id} | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| POST /staffing/candidates | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/candidates | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/candidates/{cid} | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/applications | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| PATCH /staffing/applications/{app_id}/status | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/pipeline | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| POST /staffing/import/oorwin | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/search/advanced | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| GET /staffing/stats | app/routers/staffing.py | govcon | NO (router not mounted) | YES |  |
| POST /suppliers/ | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| GET /suppliers/ | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| POST /suppliers/seed | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| GET /suppliers/stats/summary | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| GET /suppliers/search/{query} | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| GET /suppliers/lookup/manufacturer/{manufacturer} | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| POST /suppliers/import-csv | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| GET /suppliers/contacts/by-supplier/{supplier_id} | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| POST /suppliers/contacts | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| PATCH /suppliers/contacts/{contact_id} | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| DELETE /suppliers/contacts/{contact_id} | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| GET /suppliers/{supplier_id} | app/routers/suppliers.py | govcon | NO (router not mounted) | YES |  |
| POST /support/tickets | app/routers/support.py | govcon | NO (router not mounted) | YES |  |
| GET /support/tickets | app/routers/support.py | govcon | NO (router not mounted) | YES |  |
| PATCH /support/tickets/{ticket_id} | app/routers/support.py | govcon | NO (router not mounted) | YES |  |
| GET /support/tickets/stats | app/routers/support.py | govcon | NO (router not mounted) | YES |  |
| POST /api/tefca/rce/official-deliveries | app/tefca_registry/rce/delivery_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/delivery-jobs | app/tefca_registry/rce/delivery_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/delivery-jobs/{job_id} | app/tefca_registry/rce/delivery_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id}/dashboard | app/tefca_registry/rce/delivery_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/rce/deliveries | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id} | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id}/records | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id}/integrity | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/rce/deliveries/{intake_id}/quality-run | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id}/runs | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id}/issues | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| PATCH /api/tefca/rce/issues/{issue_id} | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/rce/deliveries/{intake_id}/curate | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id}/curated | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/curated/{curated_id}/lineage | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/rce/deliveries/{intake_id}/promote | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/rce/deliveries/{intake_id}/verify | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/deliveries/{intake_id}/reconciliation | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/rce/field-map | app/tefca_registry/rce/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/review-rules | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/review-rules/history | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/review-rules/{rule_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/review-rules | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| PUT /api/tefca/arc/review-rules/{rule_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/samples | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/samples | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/samples/{sample_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/samples/{sample_id}/stats | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reviews | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reviews/{review_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| PATCH /api/tefca/arc/reviews/{review_id}/resolve | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/reports/generate | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reports | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reports/{report_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reports/{report_id}/excel | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reports/{report_id}/html | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/priority-review | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/priority-reviews/dashboard | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/cycles | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/cycles/{cycle_id}/stats | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/reviews/{review_id}/determination | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/reviews/{review_id}/qa | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/reviews/{review_id}/supersede | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reviews/{review_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/qa-queue | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/reviews/{review_id}/history | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/available-cases | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/my-work | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/reviews/{review_id}/claim | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/reviews/{review_id}/release | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/reviews/{review_id}/assign | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/priority-requests | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/priority-requests | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/priority-requests/workload | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/priority-requests/{case_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/priority-requests/{case_id}/package | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/priority-requests/{case_id}/result | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/priority-requests/{case_id}/deadline-history | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/priority-requests/{case_id}/resolve-target | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/priority-requests/{case_id}/deadline | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/priority-requests/{case_id}/withdraw | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/arc/priority-requests/{case_id}/finding | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/dashboard | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/work-queue | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/analyst-workload | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/qa-workload | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/sampling | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/priority | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/readiness | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/cases/{review_id} | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/operations/cases/{review_id}/timeline | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/arc/entities/{entity_id}/verification-coverage | app/tefca_registry/review_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/stats | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/entities | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/qhins | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/participants | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/hierarchy | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/search | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/findings | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/verification-jobs | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/verification-jobs/{job_id} | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/entities/{entity_id} | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/entities/{entity_id}/children | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/entities/{entity_id}/hierarchy | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/entities/{entity_id}/findings | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/registry/entities/{entity_id}/verify | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| PATCH /api/tefca/registry/entities/{entity_id}/status | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| DELETE /api/tefca/registry/entities/{entity_id} | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/registry/dev/seed | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/registry/verify | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/registry/import/fhir-bundle | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/registry/import/csv | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/import/history | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/registry/import/{batch_id} | app/tefca_registry/routes.py | tefca_arc | YES | YES |  |
| GET /api/v1/usps/metrics | app/tefca_registry/usps_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/workflow/deliveries/{intake_id}/qhins | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/workflow/deliveries/{intake_id}/qhins/{qhin_entity_id} | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/workflow/deliveries/{intake_id}/workload | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/workflow/distribute | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |
| POST /api/tefca/workflow/deliveries/{intake_id}/review-cycle | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/workflow/deliveries/{intake_id}/review-cycle | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/workflow/reviews/{review_id}/workspace | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |
| GET /api/tefca/workflow/my-reviews | app/tefca_registry/workflow_routes.py | tefca_arc | YES | YES |  |

## Appendix C. Database tables by owning file

- `app/Tefca/models.py` (17): tefca_entities*, tefca_review_cycles*, tefca_evidence_records*, tefca_source_cache*, tefca_priority_cases*, tefca_reports*, tefca_analyst_queue*, tefca_connector_logs*, tefca_reviews*, tefca_findings*, tefca_import_history, tefca_dimension_evidence, source_version_snapshots, evidence_relationship_path, tefca_ppef_snapshots, tefca_ppef_records, tefca_ppef_ingest_jobs
- `app/api/templates.py` (1): output_templates*
- `app/api/validation_routes.py` (1): validation_queue*
- `app/case_management/models.py` (6): cm_patients*, cm_notes*, cm_care_plans*, cm_discharge_records*, cm_government_cases*, cm_billing_summaries*
- `app/models/__init__.py` (47): customers*, rfqs*, suppliers*, products*, bom_items*, quotes*, quote_line_items*, deal_registrations*, supplier_price_snapshots*, tax_jurisdictions*, financials*, audit_log*, users, dev_projects*, invoices*, invoice_line_items*, agency_contacts*, contracts*, employees*, contract_staffing*, expenses*, proposal_library*, supplier_quote_files*, job_postings*, candidates*, applications*, bench_candidates*, ats_activities*, submissions*, supplier_quote_requests*, supplier_contacts*, product_catalog*, price_history*, supplier_metrics*, technical_library*, purchase_orders*, communication_logs*, tasks*, agency_metrics*, ai_memory*, placement_outcomes*, outreach_logs*, follow_up_queue*, support_tickets*, company_profiles*, opportunities*, saved_searches*
- `app/models/database.py` (6): users, documents*, outputs*, audit_logs, audio_files*, transcripts*
- `app/models/enterprise_models.py` (10): tenants*, tenant_users*, contexts*, process_jobs*, decisions*, actions*, traceability*, policy_validations*, state_audit_log*, execution_queue*
- `app/models/migration_models.py` (9): migration_projects*, migration_schemas*, migration_fields*, migration_mappings*, migration_mapping_versions*, migration_logic_artifacts*, migration_profiling_results*, migration_validation_runs*, migration_manifest_versions*
- `app/platform_config/models.py` (13): platform_tenants*, platform_agencies*, platform_programs*, platform_modules*, platform_workspaces*, platform_pages*, platform_features*, platform_workspace_features*, platform_data_sources*, platform_themes*, platform_jurisdictions*, platform_import_formats*, platform_identifier_types*
- `app/reports/data/artifact_registry.py` (1): report_artifacts
- `app/reports/data/export_job_model.py` (1): report_export_jobs
- `app/tefca_registry/models.py` (18): tefca_reg_entities*, tefca_entity_identifiers*, tefca_entity_relationships*, tefca_entity_versions*, tefca_entity_endpoints*, tefca_verification_jobs*, tefca_verification_checks*, tefca_entity_findings*, tefca_import_batches*, tefca_reg_audit_log*, review_rules*, review_records*, tefca_verifications*, review_samples*, sample_entities*, review_reports*, review_cycles*, review_decision_events
- `app/tefca_registry/rce/delivery_job_model.py` (1): rce_delivery_jobs
- `app/tefca_registry/rce/models.py` (8): rce_source_intakes, rce_source_records, rce_ingestion_runs, rce_rule_execution_history, rce_issues, rce_curated_records, rce_correction_details, tefca_entity_contacts

`*` = declared in a model but not created by any Alembic `create_table` (startup-only or legacy). Alembic creates 21 tables. The 47 GovCon tables in `app/models/__init__.py` are not in the migration chain: the GovCon backend is not provisioned by this repository's migrations.

## Appendix D. Roles, connectors, jobs

Roles (ROLE_HIERARCHY): viewer (1), contributor (2), manager (3), reviewer (4), senior_analyst (5), qalead (6), program_manager (7), admin (8).

Connector classes: NPPESConnector, OIGLEIEConnector, SAMGovConnector, PECOSConnector, RCEDirectoryConnector, IQVIAOneKeyConnector. Health-probed: NPPES, OIG_LEIE, SAM_GOV, PECOS (PECOS is the NPPES proxy; see section on source truth).

Background jobs: bulletin scheduler (`ENABLE_SCHEDULER`, default off; Core-optional module), report export scheduler (`app/reports/export_scheduler.py`, TEFCA), RCE delivery jobs (TEFCA). No GovCon job exists in this backend.