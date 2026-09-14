# Federal Deployment Isolation Assessment — TEFCA ARC

DOCUMENT_TITLE = Federal Deployment Isolation Assessment (Core / TEFCA ARC / GovCon)
DOCUMENT_VERSION = 0.1
STATUS = PENDING_REVIEW
AUTHORED_BY = Release 1.0 structural hardening builder session (AI-assisted, maker role)
AUTHORED_DATE = 2026-09-14
SOURCE_PR = backend `feat/structural-hardening-1.0`; frontend `feat/structural-hardening-1.0` (stacked on PR #42)
SOURCE_SHA = backend main 75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96; frontend PR #42 head 52f91d57cf46690389e560ba871626c21c46a9f7 (starting points; ending SHAs in the PR descriptions)
REVIEWED_BY = PENDING
REVIEW_DATE = PENDING
APPROVED_BY = PENDING
APPROVAL_DATE = PENDING

This is an architecture assessment produced by the maker. It is not ONC approval, not COR approval, not contractual direction and not authorization for Release 1.1. Everything marked *observed* was reproduced on 2026-09-14 on a local synthetic runtime (ephemeral Postgres, synthetic users, no Government data); everything marked *reading* was read from the source at the SOURCE_SHA.

## 0. Summary

The platform is one repository, one backend process and one frontend bundle carrying three product domains: **Core** (authentication, users, documents, decisions, validation, enterprise audit), **TEFCA ARC** (the federal module: registry, reviews, RCE deliveries, reports, learning) and **GovCon / business operations** (RFQs, quotes, ATS, bench sales, invoices, …). Before this sprint the only thing separating them in a deployment was navigation and client-side route guards.

What this sprint established (all bounded, all reversible, no role/auth/schema change):

- a **server-side module gate** with a deployment program profile (`DOCUACTION_PROGRAM`), answering 404 before routing for any module a deployment does not serve (`app/core/modules.py`);
- the matching **frontend program profile** (`NEXT_PUBLIC_PROGRAM`) that hides, blocks and silences GovCon and Core-optional modules in the TEFCA build;
- the finding that the **GovCon backend is not mounted in this repository at all** (its 27 routers under `app/routers/` are never included by `app/main.py`; its 47 tables are not in the Alembic chain), so the GovCon UI in this repository targets a backend that does not exist here;
- corrections to the rate limiter (TEFCA roles, preflights), public endpoint disclosure (`/health` operator e-mail) and source labelling (PECOS is the NPPES proxy and now says so in every status);
- the reproduction and root cause of the "registry 401": a 15-minute access-token lifetime with no refresh flow, not RBAC.

FEDERAL_DEPLOYMENT_DEFENSIBILITY after this sprint = **PARTIAL**: with `DOCUACTION_PROGRAM=TEFCA_ARC` on the API and `NEXT_PUBLIC_PROGRAM=TEFCA_ARC` on the build, a TEFCA user cannot see, route to, call or retrieve GovCon; GovCon code still ships inside the TEFCA bundle (runtime gate, not build-time exclusion), the shared database still contains Core-optional tables, and the authentication/session layer is unchanged. None of this has been deployed; DEV still runs the pre-sprint build.

## 1. Which routes belong to Core / GovCon / TEFCA?

Generated inventory (Appendix A): **80 frontend routes** — TEFCA 31 (25 `/tefca-arc/*`, 5 `/tefca-registry/*`, legacy `/tefca-dashboard`), GovCon 25, Core-optional 9 (healthcare, case management, bulletin, compare, analytics, validation, decisions, documents, action center), Core 3 (`/dashboard`, `/intelligence`, `/trust`), Admin 2 (`/admin/users`, `/settings`), Public 10.

Shell ownership: 30 TEFCA routes render inside the platform `AppLayout` with the module layouts (`tefca-arc/layout.js` owns `<main id="arc-main">`, `tefca-registry/layout.js` now owns `<main id="registry-main">`); 24 GovCon routes render inside `AppShell` (a separate shell with its own navigation and its own role gates that test for `Admin | Manager | Recruiter | Sales` — display-name roles that do not exist in the platform role table); the rest are `AppLayout` or standalone.

## 2. Which endpoints belong to each?

Generated inventory (Appendix B): **646 declared endpoints**, of which **405 are mounted**: TEFCA 124 (`/api/tefca/*`, `/api/tefca/registry/*`, `/api/tefca/arc/*`, `/api/tefca/rce/*`, `/api/tefca/workflow/*`, `/api/reports/*`, `/api/learning/*`, `/api/v1/usps/*`), Core 161 (auth, documents, outputs, admin users, enterprise, decisions, validation, sla, templates, export, plans, password reset, SSO), Core-optional 120 (bulletin 60, case management 22, migration 12, document automation 12, healthcare 9, meetings 5). **241 declared endpoints are not mounted**: the whole GovCon package `app/routers/` (204 routes across 26 files, declared with prefixes such as `/ats`, `/rfq`, `/quotes` — without `/api`) and `app/api/{admin,compliance,cross_intel_routes,governance_routes,security}.py` (37 routes). *Observed*: `/api/ats/candidates`, `/api/rfqs`, `/api/projects`, `/api/security/status`, `/api/user/data-export` all answer 404 on the unmodified backend for every role.

Consequence: the GovCon pages in this frontend can never load data from this backend. Either a separate GovCon API exists outside this repository, or the GovCon module is dormant. This must be answered by a human (section 16, decision 1).

## 3. Which tables are shared?

139 model tables in the backend (Appendix C). Alembic creates 21 of them; the TEFCA and registry tables are created by their own migrations in the chain (`tefca_*`, `rce_*`, `review_*`, `report_*`). The 47 GovCon tables declared in `app/models/__init__.py` (customers, rfqs, quotes, candidates, bench_candidates, invoices, opportunities, …) are **not in the migration chain** and are not provisioned by this repository. Shared by every domain: `users`, `audit_logs`, `documents`, `outputs`, the enterprise tables (`decisions`, `actions`, `state_audit_log`, `tenants`), and the platform-configuration tables (`platform_programs`, `platform_modules`, `platform_pages`, …).

## 4. Which roles are shared?

One role table (`ROLE_HIERARCHY`: viewer 1, contributor 2, manager 3, reviewer 4, senior_analyst 5, qalead 6, program_manager 7, admin 8, with aliases). TEFCA uses reviewer / senior_analyst / qalead / program_manager / viewer / admin; Core uses viewer / contributor / manager / admin; GovCon's shell tests display names (`Admin`, `Manager`, `Recruiter`, `Sales`) that map to nothing in the table. ROLE_DOMAIN_ASSESSMENT: PLATFORM = admin, manager, contributor, viewer; TEFCA = program_manager, qalead, senior_analyst, reviewer (viewer doubles as COR read-only); GOVCON = none defined in the platform (LEGACY display names in the GovCon shell); UNKNOWN = none. No role was added, renamed, deleted or migrated in this sprint.

## 5. Which navigation elements leak domains?

*Observed on the pre-sprint build*: the platform shell shows every TEFCA user the Core-optional entries Healthcare Claims, Case Management, Bulletin Intelligence and Intelligence; TEFCA navigation is not role-scoped (a viewer sees Supervisor Operations, QHIN Assignment, Data Import). GovCon entries are not in the platform shell, but every GovCon route is directly routable and renders the GovCon shell with its 17-item navigation. *After this sprint, TEFCA build*: the three Core-optional entries are absent (26 vs 29 items for admin), every GovCon route and every Core-optional route renders "Not available in this deployment" with one h1 inside a main landmark, and the GovCon API client sends nothing. Role-scoping of TEFCA navigation is unchanged (Release 1.1).

## 6. What does `allowed_modules` actually enforce?

*Reading and observed*: `users.allowed_modules` is a per-user list of navigation ids consumed only by `AppLayout.canAccess()` (`isAdmin || allowed.has(id)`, unioned with `ALWAYS_ALLOWED`). It decides whether a nav entry renders and whether the shell shows the page or an "Access restricted" alert. It is **not** read by any backend dependency, any router, any job or any data-access path; it does not affect bundle contents; every TEFCA id is in `ALWAYS_ALLOWED`, so for TEFCA it enforces nothing at all.

ALLOWED_MODULES_CURRENTLY_ENFORCES = client-side navigation rendering and client-side page display for non-TEFCA, non-ALWAYS_ALLOWED ids.
ALLOWED_MODULES_DOES_NOT_ENFORCE = API authorization, data access, background jobs, module initialization, bundle inclusion, role visibility, anything for TEFCA ids.

## 7. Can the monorepo produce a TEFCA-only deployment?

Yes, at the runtime level, from this sprint on: API with `DOCUACTION_PROGRAM=TEFCA_ARC`, build with `NEXT_PUBLIC_PROGRAM=TEFCA_ARC`. *Observed* (two instances of the same build on one database, profiles ALL on :8014 and TEFCA_ARC on :8015, tokens for admin / program_manager / reviewer / qalead / viewer):

| Probe | TEFCA_ARC | ALL |
|---|---|---|
| GovCon paths (`/api/ats/jobs`, `/ats/jobs`, `/api/ats/candidates`, `/api/rfq/1/bom`, `/api/deal-registrations`) | 404 every role, and unauthenticated | 404 (unmounted) |
| Core-optional (`/api/healthcare/metrics`, `/api/migration/status`, `/api/v1/bulletin/*`, `/api/v1/case-management/*`, `/api/meetings/*`) | 404 every role | 200 / per-route |
| TEFCA (`/api/tefca/status`, `/api/tefca/registry/entities`, `/api/reports`, `/api/learning/TEFCA_ARC`) | 200 | 200 |
| Core (`/api/auth/me`, `/api/documents`, `/api/enterprise/audit`) | 200 | 200 |
| `/api/admin/users` with reviewer / PM token | 403 | 403 |

The 404 body is the platform's standard `NOT_FOUND` envelope with no module or profile name. The gate runs inside CORS (a gated 404 carries CORS headers), outside nothing that matters for authentication (it runs before it). Fail-closed: an unrecognised program name resolves to `TEFCA_ARC`.

## 8. Can GovCon code be excluded from the TEFCA bundle?

BUILD_TIME_MODULE_EXCLUSION_POSSIBLE = **PARTIAL**. Next.js static export builds every `src/app/**/page.*`; there is no supported per-build route filter. The realistic paths, in increasing cost:

1. *Runtime gate over one bundle* (done): GovCon pages ship but are unreachable; bundle size and attack surface unchanged.
2. *Route groups + a prebuild step* that moves `src/app/(govcon)/*` out of the tree when `NEXT_PUBLIC_PROGRAM=TEFCA_ARC` (simple, mechanical, reversible; the group directories must first be created — a rename of 25 page directories, which is why it is not overnight work).
3. *Separate app shells* (`apps/tefca`, `apps/govcon`) sharing `src/platform` and `src/lib` inside the same repository (Turbo/pnpm workspaces): true exclusion, separate origins, separate CSP, one repo. This is the target for the federal deployment.

RECOMMENDED = RUNTIME_GATE for Release 1.0; BUILD_TIME_EXCLUSION (path 2) as the first Release 1.1 step; SEPARATE_APP_SHELL (path 3) as the Release 1.1 end state. No repository split is needed for any of them.

## 9. Which APIs must be unavailable in a TEFCA deployment?

All of `govcon`, and the Core-optional modules `healthcare_claims`, `case_management`, `bulletin_intelligence`, `migration_intelligence`, `meeting_intelligence`, `document_automation` (backend). The frontend keeps the `document_automation` routes because the TEFCA shell's Core surface (documents, decisions, validation, action center) links to them; whether those belong in a federal deployment is decision 2 in section 16. Unmounted routers stay unmounted; the gate covers their prefixes in case they are ever mounted (test `test_every_govcon_router_prefix_is_covered_by_the_registry`).

## 10. Which jobs must not run?

There is no GovCon job in this backend. Jobs that exist: the bulletin scheduler (`ENABLE_SCHEDULER`, default off, Core-optional — must stay off in a TEFCA deployment; it e-mails subscribers), the report export scheduler (TEFCA), the RCE delivery jobs (TEFCA). TEFCA_GOVCON_JOB_EXPOSURE = NONE. Note: the module gate governs HTTP paths; it does not stop a scheduler that a deployment enables by environment. A TEFCA deployment must therefore also leave `ENABLE_SCHEDULER` unset (documented in `docs/deployment/DEPLOYMENT_PROGRAM_PROFILE.md`).

### 10.1 The "registry 401" — correct interpretation (reproduced)

REGISTRY_PM_401_REPRODUCED = YES, as token expiry. FRESH_TOKEN_REGISTRY_RESULT = 200 for program_manager, reviewer, qalead and viewer on `/api/tefca/registry/{entities,stats,hierarchy}`; TOKEN_EXPIRED = YES after 15 minutes (`ACCESS_EXPIRE_NORMAL`), after which `/api/auth/me` answers 401 and the registry client redirects to `/login`. EXPECTED_HTTP_SEMANTICS = 401 is correct for an expired credential; the defect is that nothing renews it (login discards the refresh token and `/api/auth/refresh` is not mounted). REGISTRY_RBAC_DEFECT = NO. SESSION_ARCHITECTURE_FINDING = YES — see `docs/security/AUTHENTICATION_TARGET_ARCHITECTURE.md`.

## 11. Can deployments / origins separate without a repository split?

Yes. The backend is one container image; two App Service instances of the same image with different `DOCUACTION_PROGRAM` values are two deployments. The frontend is one static export per build; two Static Web Apps built with different `NEXT_PUBLIC_PROGRAM` values are two origins. CORS allow-lists are per backend instance (`ALLOWED_ORIGINS`). What remains shared until Release 1.1 is the database (one schema serving both programs) and the signing key (one `SECRET_KEY` per instance — already separable per instance).

## 12. Recommended authentication target architecture

Summarised from `docs/security/AUTHENTICATION_TARGET_ARCHITECTURE.md`: stage 1 restore the refresh flow (issue at login, mount the router, silent renewal) as a separately authorized Release 1.0 change; stage 2 Entra ID (PKCE, JWKS validation, role mapping) for the federal tenant; stage 3 HttpOnly session cookie or BFF for the browser layer, decided with the custom-domain plan. AUTH_RUNTIME_CHANGED = NO in this sprint.

## 13. What can safely change during current QA?

Everything in this sprint was chosen to be invisible to the shared team-QA deployment: the module gate defaults to `ALL`; the limiter change only widens tiers for named roles and stops counting preflights; `/health` loses one field that no automation reads (verified: the release workflows read `status`, `version` and — on PR #59 — `git_sha`); `/api/config` gains fields; `/api/tefca/status` changes the PECOS value from `available` to `partial` and adds `pecos_backing` (the frontend's `resolveStatus` already maps `partial`); the registry landmark and contrast changes are presentation-only. Safe during QA: all of the above, after the independent checker.

## 14. What must wait?

Refresh-flow activation (changes the login contract); Entra migration; cookie/BFF session; build-time module exclusion (directory restructuring); role-domain separation; mounting or removing the GovCon routers; any change to Tasks 1–6, sampling, Government data or the shared QA schema.

## 15. Release placement

| Release | Items |
|---|---|
| 1.0 | module gate + program profile (this sprint); rate-limiter and preflight correction (this sprint); public endpoint disclosure (this sprint); PECOS/NPPES truth (this sprint); registry landmark and Core-required contrast (this sprint); refresh-flow stage 1 (separate authorization); DEV provenance (PR #59) and workflow governance (PR #58); repository protection (human) |
| 1.1 | build-time exclusion (route groups) then separate app shells; TEFCA navigation role-scoping; GovCon router decision (mount with `/api` prefix behind the gate, or remove); Entra ID identity; cookie/BFF session; legacy GovCon/public design-system modernization |
| 1.2 | operational scale (25K ingestion, QHIN workload, assignment, throughput, forecasting) |
| 2.0 | evidence intelligence (NPPES / PPEF / state / IQVIA under source-rights gates) |

## 16. Human decisions required

1. Is GovCon live anywhere against this backend? If yes, where is its API; if no, should `app/routers/` and the 47 GovCon models be retired in 1.1?
2. Do the Core document-automation routes (documents, decisions, validation, action center) ship in the federal TEFCA deployment? (The frontend profile keeps them; the backend profile gates their optional APIs.)
3. Approve `DOCUACTION_PROGRAM=TEFCA_ARC` / `NEXT_PUBLIC_PROGRAM=TEFCA_ARC` for the future federal DEV/PROD slots (never for the shared QA slot without notice).
4. Authorize the authentication stage-1 change and choose the stage-3 session pattern with the domain plan.
5. Repository protection on both repositories (see the builder report, section J).

## Appendix — generated inventories

See `docs/architecture/MODULE_INVENTORY_2026-09-14.md` (frontend routes, backend endpoints with mount status, tables, roles, connectors, jobs; generated from the sprint worktrees by `sh_inventory.py`, output reviewed but not hand-edited).
