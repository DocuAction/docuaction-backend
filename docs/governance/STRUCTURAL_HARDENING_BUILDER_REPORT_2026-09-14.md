# DocuAction — Release 1.0 Structural Hardening — Overnight Final Builder Report

DATE = 2026-09-14
ROLE = BUILDER / MAKER (AI-assisted session; this session must NOT act as the final checker — see section U)

Companion documents on the same branch: `docs/architecture/FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT.md` (v0.1, PENDING_REVIEW), `docs/architecture/MODULE_INVENTORY_2026-09-14.md` (generated), `docs/security/AUTHENTICATION_TARGET_ARCHITECTURE.md` (v0.1, PENDING_REVIEW), `docs/deployment/DEPLOYMENT_PROGRAM_PROFILE.md`.

All runtime evidence comes from a local synthetic runtime (ephemeral Postgres, synthetic users, no Government data, no DEV deployment). Nothing was merged or deployed.

--------------------------------------------------
A. SOURCE CONTROL
--------------------------------------------------

BACKEND_MAIN_SHA = 75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96
BACKEND_END_SHA = e781020fe8ae31b95fbc5d93cae694616f397200 (branch `feat/structural-hardening-1.0`, draft PR #62, base main)
FRONTEND_MAIN_SHA = 0f995782fbbe90585da839c25aec7fb7c3006b3d
FRONTEND_42_START_SHA = 52f91d5 (unchanged during the sprint)
FRONTEND_END_SHA = bfa7902 (branch `feat/structural-hardening-1.0`, draft PR #44, base `fix/ui-a11y-lms-remediation` = PR #42 head; commits 82dd5ff → 2a38f12 → 7b61d3c → 46f9dab → 13f5307 → bfa7902)
SHA_DRIFT = NO (main and #42 heads identical to the checker-qualified baseline at start and end)
RE_AUDIT_REQUIRED = YES for the two new heads (genuinely independent checker, new session); NO for the #42 baseline itself

--------------------------------------------------
B. BASELINE / #42 RESIDUAL
--------------------------------------------------

REGISTRY_DUPLICATE_MAIN_REPRODUCED = YES (every `/tefca-registry/*` route rendered `div#app-main[role=main]` from AppLayout plus `main#app-main` from the registry layout: two main landmarks, one duplicated id)
REGISTRY_DUPLICATE_MAIN_FIXED = YES (registry layout owns `<main id="registry-main">`; AppLayout yields `role=main` on tefca-arc, tefca-registry and healthcare routes; guardrail added; verified: one `main#registry-main`, one `#app-main` on all five registry routes, both profiles)
TEFCA_REGRESSIONS_FROM_FIX = 0

--------------------------------------------------
C. TEFCA REGRESSION
--------------------------------------------------

Method: `scratchpad/pw/verify_routes.js` (same harness as the #42 checker run) — every exported route, light and dark, 1366 / 834 / 390 / 640 / 320 px, axe-core (wcag2a/aa/21aa/22aa), eight-stop keyboard walk, CSS-zoom proxies; admin session; backend e781020 with the default profile; compared route-by-route with the pre-sprint checker matrix of 52f91d5.

TEFCA_ROUTES_TOTAL = 30 (25 `/tefca-arc/*`, 5 `/tefca-registry/*`) + legacy `/tefca-dashboard`
TEFCA_ROUTES_PASS = 29 (full ALL-profile matrix on the 2a38f12 build: 28 PASS + 1 FAIL that was `/tefca-arc/reports/`; TEFCA-profile matrix on 7b61d3c: 29 PASS; targeted re-runs at 46f9dab / 13f5307 / bfa7902: reports 0 serious nodes)
TEFCA_ROUTES_PARTIAL = 1 (`/tefca-arc/dashboard/`: CSS-zoom-400 proxy overflow only; 320 px viewport clean; unchanged from baseline)
TEFCA_ROUTES_FAIL = 0 at the final head (the one FAIL seen mid-sprint, `/tefca-arc/reports/` axe `target-size` on 14 row download buttons, appears only when reports exist in the database — the synthetic runtime generated four — and was repaired in 46f9dab: 24 px minimum targets; re-verified 0 serious nodes both themes)
TEFCA_ROUTES_BLOCKED = 0
TEFCA_REGRESSIONS = 0 against the 52f91d5 baseline (axe serious, contrast 0/0, dark islands 0, overflow 0, h1 = 1, main present, focus visible on all 30; business-truth walk 0 prohibited-meaning hits on 31 rendered routes)

--------------------------------------------------
D. MODULE ISOLATION
--------------------------------------------------

CORE_ROUTES = 3 core + 2 admin + 9 core-optional (documents, decisions, validation, analytics, compare, action center, healthcare, case management, bulletin) + 10 public
TEFCA_ROUTES = 31
GOVCON_ROUTES = 25
UNKNOWN_ROUTES = 0

CORE_ENDPOINTS = 161 mounted (+37 declared in `app/api/{admin,compliance,cross_intel_routes,governance_routes,security}.py` that `app/main.py` never mounts)
TEFCA_ENDPOINTS = 124 mounted
GOVCON_ENDPOINTS = 204 declared in `app/routers/` (26 files), 0 mounted — the GovCon backend does not exist in this repository's running application; its 47 model tables are not in the Alembic chain
UNKNOWN_ENDPOINTS = 0 (120 further mounted endpoints belong to the Core-optional modules: bulletin 60, case management 22, migration 12, document automation 12, healthcare 9, meetings 5)

ALLOWED_MODULES_CURRENTLY_ENFORCES = client-side navigation rendering and the client-side "Access restricted" page state for non-TEFCA ids not in `ALWAYS_ALLOWED`
ALLOWED_MODULES_DOES_NOT_ENFORCE = API authorization, data access, background jobs, module initialization, bundle contents, role visibility, and anything for TEFCA ids (all in `ALWAYS_ALLOWED`)

CLIENT_SIDE_ONLY = NO after this sprint (was YES)
SERVER_SIDE_ENFORCED = YES — `app/core/modules.py` ModuleGateMiddleware, `DOCUACTION_PROGRAM=ALL|TEFCA_ARC` (default ALL = unchanged behaviour; unknown value fails closed to TEFCA_ARC); 18 tests
SECURITY_BOUNDARY_REAL = YES under `TEFCA_ARC` (two instances of the same build on one database: GovCon and Core-optional paths answer 404 for admin, program_manager, reviewer, qalead, viewer and unauthenticated callers; TEFCA and Core paths 200; `/api/admin/users` 403 for non-admin, unchanged). Under the default profile the boundary is the pre-existing one (GovCon routers unmounted → 404; Core-optional modules reachable). Evidence: `scratchpad/sh_direct_api_evidence.txt`.

TEFCA_GOVCON_NAVIGATION_EXPOSURE = NONE with `NEXT_PUBLIC_PROGRAM=TEFCA_ARC` (Core-optional entries also removed: 26 vs 29 admin nav items); pre-sprint: GovCon not in the platform nav but Core-optional entries shown to every TEFCA user
TEFCA_GOVCON_ROUTE_EXPOSURE = NONE with the profile (every GovCon and Core-optional route renders "Not available in this deployment": one h1, `role=status`, inside a main landmark, no GovCon shell); pre-sprint: every GovCon route directly routable
TEFCA_GOVCON_API_EXPOSURE = NONE with the profile (404 before routing); the GovCon API client sends no request
TEFCA_GOVCON_DATA_EXPOSURE = NONE (no GovCon data returned under either profile; GovCon tables are not provisioned by this repository)
TEFCA_GOVCON_JOB_EXPOSURE = NONE (no GovCon job exists; the bulletin scheduler is Core-optional and off by default — must stay unset in a TEFCA deployment)
TEFCA_GOVCON_ROLE_EXPOSURE = NONE (no GovCon roles in the platform role table; the GovCon shell's `Admin/Manager/Recruiter/Sales` display-name gates match nothing)

--------------------------------------------------
E. ROLE MODEL
--------------------------------------------------

ROLE_DOMAIN_ASSESSMENT = PLATFORM: admin, manager, contributor, viewer · TEFCA: program_manager, qalead, senior_analyst, reviewer (viewer doubles as COR read-only) · GOVCON: none defined in the platform (LEGACY display-name gates in the GovCon shell) · UNKNOWN: none
ROLE_RUNTIME_CHANGED = NO
ROLE_SCHEMA_CHANGED = NO

--------------------------------------------------
F. AUTHENTICATION
--------------------------------------------------

CURRENT_AUTH_RISK = MEDIUM-HIGH for a federal deployment (bearer JWT in `localStorage`, HS256 with one shared key and no `kid`, 24-hour admin tokens, no refresh flow, one token namespace across three domains); sound parts preserved: database-authoritative role per request, server-side revocation epoch, bcrypt, auth audit
TOKEN_LIFETIME = 15 minutes for every non-admin role; 24 hours for admin
REFRESH_FLOW = NONE in effect (`create_token_pair` mints a refresh token but `/api/auth/login` returns only `access_token`; the router exposing `POST /api/auth/refresh` is not mounted; the core dashboard's renewal code can never succeed)
AUTH_STORAGE_PATTERN = `localStorage` keys token, govcon_token, user, access_token, refresh_token; no cookie; CORS without credentials
TARGET_AUTH_PATTERN = E (hybrid): stage 1 = current architecture hardened (issue refresh at login, mount the refresh router, silent renewal, shorter admin token, `kid`); stage 2 = Microsoft Entra ID identity (PKCE, JWKS, role mapping; HSPD-12 path); stage 3 = HttpOnly session cookie or BFF chosen with the custom-domain plan
MIGRATION_COMPLEXITY = LOW (stage 1) / HIGH (stages 2–3)
QA_IMPACT = LOW (stage 1: sessions stop expiring at 15 min) / HIGH (stage 2)
RECOMMENDED_RELEASE = stage 1 in Release 1.0 as a separately authorized change; stages 2–3 in Release 1.1
AUTH_RUNTIME_CHANGED = NO

REGISTRY_PM_401_REPRODUCED = YES, as token expiry (fresh program_manager / reviewer / qalead / viewer tokens read `/api/tefca/registry/*` 200 and the page renders; 15 minutes later `/api/auth/me` → 401 → redirect to `/login`, token cleared, 2 requests, no request storm)
REGISTRY_RBAC_DEFECT = NO
SESSION_ARCHITECTURE_FINDING = YES

RATE_LIMIT_ROLE_MAPPING = before: admin→enterprise, manager→business, contributor→pro, viewer→free, everything else free (program_manager, qalead, senior_analyst, reviewer fell to 60/min, burst 10 per 5 s); after: every platform role explicit (TEFCA operational roles → business), aliases resolved like RBAC, unknown roles still free
429_REPRODUCED = YES (12 rapid authenticated GETs → 2 × 429 for pm / analyst / qa, 0 for admin, on the pre-sprint code; browser probes on the pre-sprint backend showed failed Mission Control requests and a "Verifying session…" stall for qalead; on the committed backend the same probes show 0 failed requests and 0 × 429 for all four roles)
RATE_LIMIT_FIX = YES — `app/core/rate_limiter.py`: TIER_BY_ROLE + `tier_for_role()`, CORS preflights (OPTIONS) exempt, middleware order changed so a 429 carries CORS headers; limits not raised globally, limiter not disabled; 10 tests. OPTIONS_COUNTED = was YES, now NO. CLIENT_IP_BEHAVIOR = rightmost X-Forwarded-For entry (App Service-appended), else peer address — unchanged.

--------------------------------------------------
G. SOURCE TRUTH
--------------------------------------------------

PECOS_ACTUAL_BACKING = CMS NPI Registry API (NPPES, `npiregistry.cms.hhs.gov`); no PECOS feed is connected; payment-suspension data requires a COR-provisioned source
PECOS_UI_LABEL = "PECOS — Enrollment via NPPES proxy — payment-suspension data requires a COR-provisioned feed and is not covered"; Medicare Enrollment badge state `partial` "NPPES-proxy verification" (truthful, unchanged)
PECOS_IMPLEMENTATION_IDENTIFIER = class `PECOSConnector`, health key `PECOS`, public snapshot key `pecos`, evidence source name `PECOS` (identifiers unchanged)
FALSE_PECOS_IMPLICATION_FOUND = YES: `/api/tefca/status` reported `pecos: "available"` (rendered Live) whenever the NPPES proxy answered, and the health note read "Provider Enrollment — CMS"
SOURCE_NAMING_CORRECTED = YES: snapshot now `pecos: "partial"` when the proxy answers / `"unavailable"` otherwise, plus `pecos_backing: "nppes_proxy"`; health note "Provider Enrollment — NOT CONNECTED. NPPES (CMS NPI Registry) proxy answers this probe; a PECOS feed (payment suspension) requires a COR-provisioned source" (`PECOS_BACKING_NOTE` constant); 5 tests
DEPENDENT_CODE_REVIEWED = YES: `qa_engine._VALID_SOURCES`, evidence assembly / evidence service `pecos` handling, `reporting._chart_connector_health_trend`, frontend `resolveStatus` vocabulary (`partial` already mapped) — no schema, meaning or capability change; no external integration activated
NPPES_LABEL_STATUS = truthful (NPI Registry — CMS/HHS)
OTHER_MISLABELS = none found (SAM note states the key-configured / upstream distinction; IQVIA and the RCE directory are intentionally not health-probed; `/api/tefca/status` marks the dataset `MOCK — demonstration data only` on this runtime)
SOURCE_LABEL_TRUTH = PASS

--------------------------------------------------
H. ACCESSIBILITY
--------------------------------------------------

TEFCA_ACCESSIBILITY = PASS (DEV-style: 30 routes, both themes, 0 serious/critical axe nodes, 0 contrast nodes, one h1, main landmark, 0 unlabeled controls, 0 overflow at five widths, focus visible; registry duplicate-main resolved; reports row targets ≥ 24 px)
CORE_REQUIRED_ROUTES = `/`, `/login`, `/dashboard`, `/actions-inbox`, `/documents`, `/decisions`, `/admin/users`, `/settings` (8)
CORE_REQUIRED_CONTRAST_BEFORE = light/dark nodes 33/35, 33/35, 33/35, 24/22, 6/4, 5/4, 2/24, 3/7 (checker matrix of 52f91d5)
CORE_REQUIRED_CONTRAST_AFTER = 0/0 on all eight at bfa7902 (root causes: white-alpha rail text, `#CBD5E1` and `#1E3A5F` literals on light cards, `#0F172A` KPI value on a dark-remapped card, `text-slate-500`, emerald/white Tailwind block, users-admin literal palette and the global `th { background: #FAF9F8 }`; all moved to `--nav-rail-text`, `--text-muted`, `--text-secondary`, `--text-primary`, `--status-*`, `--surface-secondary`, `--text-on-brand`). Final re-verification at bfa7902 (ALL export, both themes): /admin/users 0/0, /dashboard 0/0, /settings 0/0, /tefca-arc/my-reviews 0/0, /tefca-registry/entities 0/0, /tefca-arc/reports 0/0 with no other serious nodes.
APPLICATION_WIDE_ACCESSIBILITY = NOT PASS: 34 legacy GovCon / public routes still fail on colour contrast only (legacy total 122/152 nodes light/dark, from 328/371; every other gate passes on 79/79 routes: h1, main, labels, overflow, islands, focus, error boundary). LEGACY_CONTRAST_DEFERRED = YES (GovCon does not ship in the TEFCA profile; public/auth pages are Release 1.1 design-system work)
ZOOM_200_ACTUAL = NOT_VERIFIED (no browser-zoom command in the DevTools protocol; 640 px viewport proxy clean)
ZOOM_400_ACTUAL = NOT_VERIFIED (320 px viewport — the WCAG reflow-equivalent width — clean on 79/79; CSS-zoom proxy flags 28 legacy routes plus the TEFCA dashboard, unchanged from baseline)
SECTION_508_CERTIFICATION = NOT_CLAIMED

--------------------------------------------------
I. PUBLIC ENDPOINTS
--------------------------------------------------

PUBLIC_ENDPOINTS_REVIEWED = `/health`, `/api/config`, `/api/tefca/status` (plus `/api/auth/sso/status`, unchanged)
PUBLIC_SAFE = all three after the change (secret-shape scan 0 hits: no connection strings, keys, tokens, passwords or e-mail addresses)
AUTH_REQUIRED_RECOMMENDED = none (each answers a question a caller must have before login)
ADMIN_REQUIRED_RECOMMENDED = none
DISCLOSURE_FINDINGS = (1) `/health` exposed the operator alert e-mail (`scheduler.alert_email`) — removed, test added; (2) `/api/tefca/status` implied a connected PECOS feed — corrected (section G); (3) `/api/config` now adds `program`, `enabled_modules`, `disabled_modules` (module ids only); (4) flagged, not changed: `NEXT_PUBLIC_SAM_API_KEY` is inlined into the GovCon opportunities page bundle (pre-existing; GovCon does not ship in the TEFCA profile; a build-time public key is still a key — human decision)

--------------------------------------------------
J. GITHUB GOVERNANCE
--------------------------------------------------

Observed read-only on 2026-09-14 (no settings changed):
BRANCH_PROTECTION = backend main NONE ("Branch not protected", rulesets []); frontend main unavailable to the API ("Upgrade to GitHub Pro or make this repository public")
PR_REQUIRED = NO (both)
REQUIRED_APPROVAL = NO (both)
STATUS_CHECKS = NO required checks (both)
CODEOWNERS = present in both repositories
CODEOWNERS_ENFORCED = NO (needs branch protection)
LATEST_PUSH_APPROVAL = NO
FORCE_PUSH = allowed (unprotected)
DELETE_BRANCH = allowed (delete_branch_on_merge false)
ADMIN_BYPASS = not applicable without protection
ENVIRONMENT_PROTECTION = backend `development`: 1 required reviewer, prevent_self_review false; `production`: 1 required reviewer, prevent_self_review true; the positive-enchantment environments have no reviewers
PLAN_LIMITATIONS = frontend is a private repository on the Free plan: branch protection, rulesets, code scanning, dependency review and secret scanning are unavailable; the backend is public and can be protected today
HUMAN_ACTIONS_REQUIRED = protect backend `main` (PR required, 1 independent approval, dismiss stale approvals, required checks: backend-ci / security scans, no force-push, no deletion, CODEOWNERS enforced); decide frontend plan upgrade or visibility; set prevent_self_review on `development`; remove or configure the reviewer-less environments

--------------------------------------------------
K. DEV RELEASE PROVENANCE
--------------------------------------------------

CHECKER_RUNTIME_VERIFICATION_DEPLOYMENT = NO (no DEV deployment was made; environment approvals are human-gated and none was requested)
RUNTIME_VERIFICATION_SHA = n/a
RUNTIME_VERIFICATION_WORKFLOW_RUN = n/a
RUNTIME_VERIFICATION_PURPOSE = n/a (all runtime evidence is local synthetic)

REPRODUCIBLE_DEPLOYMENT = BLOCKED
EXPECTED_GIT_SHA = none for this sprint (nothing deployed); DEV's last intended source is backend 75383cf (run 34743956579)
RUNTIME_GIT_SHA = DEV `/health` exposes none (PR #59 adds `git_sha`; unmerged); DEV reports version 6.0.0
WORKFLOW_RUN_ID = dev-release 34743956579 (75383cf) still `pending` behind the obsolete waiting run 34555888905 (2eb11dd) holding the `dev-release` concurrency slot; 34661300993 and 34573512155 cancelled
ARTIFACT_ID = none captured by the current workflow
IMAGE_TAG = 0cf0284 (manual `az acr build`, 2026-09-12)
IMAGE_DIGEST = sha256:4384b211… (DEV App Service container)
DEPLOYMENT_TIME = SWA Last-Modified 2026-09-12 02:56:48 GMT; App Service image 2026-09-12

DEV_PROVENANCE_CHAIN = BLOCKED (obsolete run must be cancelled and the pending run approved by a human; runtime SHA identity requires PR #59)

--------------------------------------------------
L. LMS
--------------------------------------------------

LMS_MODULES = 16 (registry; API scope per role: program_manager 16, qalead 16, reviewer 14, admin 14, viewer 12)
MODULES_VERIFIED = 16 (module titles rendered for PM and QA lead; 14 / 14 / 12 for reviewer / admin / viewer, matching the API), across light/dark × desktop/mobile for all five roles
ROLE_PATHS = program-manager 16, analyst 11, qa-lead 10, administrator 8, viewer 6 module slugs; stepper with 5 paths and 51 items rendered for every role
SEARCH = present (module filter/search control on the landing page; GlobalSearch command bar on every TEFCA route)
NAVIGATION = module view: next 2 / previous 0 on the first module, 15 module links after the Program-modules tab, 12 tab stops, focus visible
KNOWLEDGE_CHECKS = 7 rendered with questions in the first module view (API: 15–19 checks per role scope)
LIGHT = PASS · DARK = PASS (data-theme, canvas, no islands) · KEYBOARD = PASS · FOCUS = PASS · workflow diagram: 1 pipeline figure with figcaption and role list
LMS_CHANGE_REQUIRED = NO (no instructional content touched; only the badge token colour from #42)
KNOWLEDGE_VERSION = 1.2.0

--------------------------------------------------
M. REPORTS
--------------------------------------------------

Fresh synthetic generation on the sprint backend (admin): DA-ARC-2026-001 verification (technical family), DA-ARC-2026-002 Task 3 D3.1 Weekly, DA-ARC-2026-003 Task 4 D4.1 Bi-Weekly, DA-ARC-2026-004 Task 5 D5.1 Priority Status.
DOCX = PASS (Task 3/4 families: 19-entry package, Title / Heading1 styles, 4 tables, contract number; the verification family has no DOCX form by design → 404)
PDF = BLOCKED locally (503: WeasyPrint's native libraries are absent on the build host; the container image is unaffected — see PR #61 / #56)
HTML = PASS with one note: the cover renders two `<h1>` (cover title + running title) on `main`; PR #61 (unmerged) makes the running title a `doc-subtitle`
CSV = PASS (BOM, identity header naming the report)
ZIP = PASS (contract-numbered filenames: DOCX + HTML + CSV + README + manifest)
Identity verified in the HTML: DocuAction, TEFCA ARC, Task / Deliverable, Contract 7571MN26F80064, Prepared for the Office of the National Coordinator for Health Information Technology / Department of Health and Human Services, Prepared by Alliance Global Tech Inc., Report ID, Period, Version, Status, Date, Document Control, Contents, provenance, development/synthetic marking; no HHS seal or logo; no Section 508 claim
GOVERNMENT_BRANDING = OFF
REPORT_REGRESSION = NONE (backend report code untouched; frontend `/tefca-arc/reports/` verified: h1 "Contract Reports", controls Generate draft / Produce workbook / Preview / Refresh, selects labelled, development-data notice, 0 serious nodes after the target-size fix)

--------------------------------------------------
N. TESTS
--------------------------------------------------

BACKEND_TESTS = full suite on the branch: 3109 passed / 10 failed / 111 skipped (`python -m pytest -q -p no:cacheprovider -rfE`, ephemeral Postgres). The same 10 fail identically on untouched `main` in this environment: `test_human_review_workflow::test_government_rows_are_untouched`, `test_phase7_report_data::TestDerivedFromPersistedEvidence` ×6, `test_phase8_reconciliation::…::test_the_legacy_population_is_entirely_synthetic`, `test_ppef_jobs::test_partial_unique_index_refuses_a_second_active_job`, `test_report_cross_format_reconciliation::test_every_format_states_the_same_identity_and_rows` — they assert against the populated QA dataset (188 528 / 8 584 rows, `phase6-bulk-1.1.0`) or need a PDF engine. Pre-existing, environment-dependent, not introduced. New: 35 passed (module gate 18, limiter 10, disclosure 7).
FRONTEND_TESTS = `npm run test:ui` all checks passed at every commit; production build 81/81 pages under both profiles (`npm run build`)
ACCESSIBILITY_TESTS = full 79-route matrix (ALL export, 2a38f12 build, clean run: 34 PASS / 10 PARTIAL / 35 FAIL of which 34 contrast-only legacy + the data-dependent reports target-size); TEFCA-profile 44-route matrix (7b61d3c); targeted re-runs at 46f9dab (11 + 7 routes), 13f5307 (2) and bfa7902 (6) for the routes changed after the full runs
MODULE_ISOLATION_TESTS = 18 unit + runtime probes on two backend instances (profiles ALL / TEFCA_ARC) + browser probes on both exports (16 + 4 + 10 routes)
DIRECT_API_TESTS = `scratchpad/sh_direct_api_evidence.txt` (20 endpoints × 4 roles + unauthenticated, both profiles)
RESPONSIVE_TESTS = 0 overflow at 1366 / 834 / 390 / 640 / 320 on 79/79 (ALL) and 44/44 (TEFCA)
THEME_TESTS = data-theme dark on every dark render, 0 light islands, 0 dark islands
SECURITY_TESTS = bundle secret scan 0 (52f91d5, unchanged deps); production `npm audit` 2 pre-existing advisories (next, sharp — PR #41); public-endpoint secret-shape scan 0; workflow files untouched
REPORT_TESTS = 4 synthetic generations, 4 formats each (section M)
LMS_TESTS = API per role + UI per role (section L)

TOTAL_PASS = backend 3109 + new 35 (included) · frontend guardrails all · matrices as above
TOTAL_FAIL = backend 10 (pre-existing, environment) · frontend routes 34 contrast-only legacy (deferred)
TOTAL_SKIP = backend 111

--------------------------------------------------
O. CONTRACT / SECURITY FREEZE
--------------------------------------------------

TASK_1_CHANGED = NO
TASK_2_CHANGED = NO
TASK_3_CHANGED = NO
TASK_4_CHANGED = NO
TASK_5_CHANGED = NO
TASK_6_CHANGED = NO

GOVERNMENT_DATA_MODIFIED = NO
REAL_ONC_CASE_MODIFIED = NO
SHARED_QA_SCHEMA_CHANGED = NO (no migration; ephemeral database only)
PROD_TOUCHED = NO
METHODOLOGY_CHANGED = NO
AUTH_WEAKENED = NO
RBAC_WEAKENED = NO
SECURITY_WEAKENED = NO (limiter tiers widened only for named roles; preflights exempt; nothing disabled)

ENTITY_INTELLIGENCE_ENABLED = FALSE
GATE_H = CLOSED

--------------------------------------------------
P. STRUCTURAL ASSESSMENT
--------------------------------------------------

FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT = `docs/architecture/FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT.md`
DOCUMENT_VERSION = 0.1
STATUS = PENDING_REVIEW
REVIEWED_BY = PENDING
APPROVED_BY = PENDING

BUILD_TIME_MODULE_EXCLUSION_POSSIBLE = PARTIAL · RECOMMENDED = RUNTIME_GATE (1.0) → BUILD_TIME_EXCLUSION via route groups (first 1.1 step) → SEPARATE_APP_SHELL (1.1 end state); no repository split

FEDERAL_DEPLOYMENT_DEFENSIBILITY = PARTIAL — with `DOCUACTION_PROGRAM=TEFCA_ARC` and `NEXT_PUBLIC_PROGRAM=TEFCA_ARC` a TEFCA user cannot see, route to, call or retrieve GovCon (verified); GovCon code still ships inside the bundle, the database is shared, the session layer is unchanged, and none of this is deployed yet

--------------------------------------------------
Q. GOVERNING INVARIANTS
--------------------------------------------------

Per the repository map (`docs/governance/DOCUACTION_INVARIANT_MAP.md`, PR #57 branch; not re-derived):
GOVERNING_INVARIANTS_TOTAL = 37
INVARIANTS_TESTED = 36
INVARIANTS_PASS = 36
INVARIANTS_FAIL = 0
INVARIANTS_MISSING_TEST_COVERAGE = 11 (I-3, I-12, I-15, I-16, I-21, I-27, I-30, I-31, I-33, I-34, I-36)
This sprint's full backend run re-executed every committed invariant test green (Entity Intelligence isolation and Core→TEFCA boundary included); the business-truth walk over 31 rendered TEFCA routes found 0 prohibited-meaning strings.

--------------------------------------------------
R. REMAINING WORK
--------------------------------------------------

PROBLEMS_FOUND = 12: registry double main; GovCon backend unmounted; allowed_modules client-only; no server-side module boundary; 15-min tokens without refresh (registry "401"); TEFCA roles on the free limiter tier; preflights counted per IP / 429 without CORS; /health operator e-mail; PECOS false-available; Core-required contrast (8 routes); reports row target size; users-admin / settings / global th literals
PROBLEMS_FIXED = 9 (registry main; module gate + program profile; limiter tiers + preflights + CORS order; /health e-mail; PECOS state and note; Core-required contrast; reports target size; global th surface; users-admin palette)
PROBLEMS_CONTAINED = 1 (GovCon exposure — gated at runtime, not excluded from the bundle)
PROBLEMS_DEFERRED = 3 (refresh flow / auth stage 1 — separate authorization; legacy GovCon/public contrast — 1.1; TEFCA navigation role-scoping — 1.1)
PROBLEMS_BLOCKED = 2 (DEV provenance chain — human approval + PR #59; repository protection — human)
PROBLEMS_REMAINING = GovCon router decision (mount behind the gate or retire), build-time exclusion, shared-database separation, Entra identity, cookie/BFF session, admin token lifetime, `NEXT_PUBLIC_SAM_API_KEY`

RELEASE_1_0_REMAINING = independent checker on PR #62 + #44 (new session); human merge decisions for the #40 → #42 → #44 stack and PR #62; auth stage 1 (separately authorized); merge PR #59 (provenance) and PR #58 (workflow governance) after their checks; repository protection; cancel obsolete DEV run and approve the pending one; decide Core document-automation routes in the federal profile
RELEASE_1_1_ITEMS = build-time exclusion (route groups) → separate app shells; TEFCA nav role-scoping; GovCon router / model retirement or mounting decision; Entra ID identity; cookie or BFF session; legacy design-system modernization (GovCon/public contrast, 11 px text, three colour systems)
RELEASE_1_2_ITEMS = 25K ingestion, QHIN workload, assignment, throughput, aging, forecasting, operational reporting
RELEASE_2_0_ITEMS = NPPES / What Changed / identity–location evidence / IQVIA / state evidence under source-rights and program gates

--------------------------------------------------
S. HUMAN ACTIONS
--------------------------------------------------

HUMAN_GITHUB_ACTIONS = protect backend `main`; frontend plan or visibility decision; prevent_self_review on `development`; required status checks; clean reviewer-less environments
HUMAN_DEV_APPROVAL_ACTIONS = cancel run 34555888905 (obsolete, holding the `dev-release` slot); approve run 34743956579 or re-dispatch at the intended SHA; do not deploy PR #62 / #44 to the shared QA slot without notice
HUMAN_SECURITY_DECISIONS = authorize auth stage 1 (refresh at login + mounted refresh router + silent renewal); admin token lifetime; `NEXT_PUBLIC_SAM_API_KEY` in the GovCon bundle; keep `ENABLE_SCHEDULER` unset in any TEFCA deployment
HUMAN_ARCHITECTURE_DECISIONS = approve `DOCUACTION_PROGRAM=TEFCA_ARC` / `NEXT_PUBLIC_PROGRAM=TEFCA_ARC` for the future federal DEV/PROD slots; whether Core document-automation routes ship in the federal profile; GovCon router future; domain plan (decides cookie vs BFF); review and approve the two v0.1 assessments
HUMAN_SOURCE_RIGHTS_ACTIONS = none new (PECOS remains NOT CONNECTED until a COR-provisioned feed exists; NPPES/PPEF source-rights reviews unchanged)
OTHER_HUMAN_ACTIONS = merge decision for the frontend stack (#40 → #42 → #44) and PR #62 after the independent checker; PDF engine verification in the container (PR #61) before any deliverable PDF is handed over

--------------------------------------------------
T. FINAL GATES
--------------------------------------------------

TEFCA_ZERO_REGRESSION_GATE = PASS (0 regressions; 29 PASS + 1 PARTIAL proxy-only at the final head)
UI_ACCESSIBILITY_GATE = PASS_WITH_BOUNDED_LIMITATIONS (TEFCA + Core-required clean; 34 legacy GovCon/public routes contrast-only; actual browser zoom unverified)
MODULE_ISOLATION_GATE = PASS under the TEFCA_ARC profile (server-side 404 + frontend gate, verified per role) / PARTIAL as a deployment (runtime gate, not build-time exclusion; not yet deployed)
SOURCE_TRUTH_GATE = PASS
SECURITY_GATE = PASS (nothing weakened; two disclosure fixes; pre-existing dependency advisories tracked in PR #41)
DEV_PROVENANCE_GATE = BLOCKED
GITHUB_GOVERNANCE_GATE = FAIL (no enforced protection on either repository; human)
LMS_GATE = PASS
REPORT_GATE = PASS_WITH_BOUNDED_LIMITATIONS (DOCX/HTML/CSV/ZIP verified; PDF blocked on this host)
BUILD_GATE = PASS (backend suite as documented; frontend build both profiles; guardrails)
FUNCTIONAL_REGRESSION_GATE = PASS (per-role probes: registry, Mission Control, My Reviews, Learning Center, GovCon guards; expired-token behaviour clean)

READY_FOR_GENUINELY_INDEPENDENT_CHECKER = YES
READY_FOR_TEAM_QA = NO (nothing merged; the shared QA deployment is unchanged and must stay so until the checker and the human merge decision)
READY_FOR_RELEASE_1_0_HUMAN_DECISION = NO

MERGE_AUTHORIZED = NO
PROD_AUTHORIZED = NO
GATE_H_AUTHORIZED = NO

--------------------------------------------------
U. INDEPENDENT CHECKER HANDOFF
--------------------------------------------------

BACKEND_PR = #62 https://github.com/DocuAction/docuaction-backend/pull/62 (draft)
BACKEND_START_SHA = 75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96
BACKEND_END_SHA = e781020fe8ae31b95fbc5d93cae694616f397200

FRONTEND_PR = #44 https://github.com/DocuAction/docuaction-frontend/pull/44 (draft, base `fix/ui-a11y-lms-remediation`; merges only as the #40 → #42 → #44 stack)
FRONTEND_START_SHA = 52f91d57cf46690389e560ba871626c21c46a9f7
FRONTEND_END_SHA = bfa7902

EXACT_FILES_CHANGED = backend (12): app/core/modules.py (new), app/main.py, app/core/rate_limiter.py, app/Tefca/connectors.py, app/Tefca/routes.py, tests/test_module_gate.py, tests/test_rate_limit_roles_and_preflight.py, tests/test_public_endpoint_disclosure.py, docs/architecture/FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT.md, docs/architecture/MODULE_INVENTORY_2026-09-14.md, docs/security/AUTHENTICATION_TARGET_ARCHITECTURE.md, docs/deployment/DEPLOYMENT_PROGRAM_PROFILE.md (+ this report). Frontend (16): src/lib/program.js (new), src/lib/api.ts, src/components/AppLayout.js, src/components/AppShell.tsx, src/components/UsersAdmin.js, src/app/tefca-registry/layout.js, src/app/dashboard/page.js, src/app/actions-inbox/page.js, src/app/documents/page.js, src/app/decisions/page.js, src/app/settings/page.js, src/app/tefca-arc/reports/page.js, src/app/globals.css, scripts/ui-guardrails.mjs, docs/FRONTEND_DEPLOYMENT.md
EXACT_TEST_COMMANDS = backend: `python -m pytest -q -p no:cacheprovider -rfE` (DATABASE_URL to an ephemeral migrated Postgres, SECRET_KEY ≥ 64 chars); `python -m pytest tests/test_module_gate.py tests/test_rate_limit_roles_and_preflight.py tests/test_public_endpoint_disclosure.py`. Frontend: `npm ci && npm run test:ui && NEXT_PUBLIC_API_URL=<api> npm run build` and again with `NEXT_PUBLIC_PROGRAM=TEFCA_ARC`; route matrix: `ROUTE_BASE=<static> ROUTE_SESSION=<login json> npm run test:routes`. Runtime: two backend instances of the same image with `DOCUACTION_PROGRAM=ALL` and `=TEFCA_ARC`; curl the endpoints listed in section D with each role's token.
EXACT_TEST_RESULTS = section N
DIRECT_API_EVIDENCE = `scratchpad/sh_direct_api_evidence.txt` (to be reproduced by the checker; local file, not committed)
ACCESSIBILITY_EVIDENCE = `scratchpad/sh_matrix_all.json` (79 routes, 2a38f12 build), `sh_matrix_tefca.json` (44 routes, 7b61d3c), `sh_matrix_targeted_all.json` / `_tefca.json` (46f9dab), `sh_matrix_last2.json` (13f5307), `sh_matrix_last3_all.json` (bfa7902), `sh_probes_roles.json`, `sh_help_<role>.json`; pre-sprint baseline `chk42b_matrix.json` (52f91d5)
SECURITY_EVIDENCE = section I + N; `tests/test_public_endpoint_disclosure.py`
DEV_RUNTIME_EVIDENCE = none (no deployment); section K
KNOWN_LIMITATIONS = the full 79-route matrix ran on the 2a38f12 build; the commits after it (7b61d3c, 46f9dab, 13f5307, bfa7902) touch only the routes that were re-verified in targeted runs (core dashboard/action center/documents/decisions/settings/users admin, reports, registry) plus the global `th`/hover surfaces — a checker should re-run the full matrix at bfa7902. PDF generation not verified on this host. Actual browser zoom not verified. The PECOS evidence-source identifier (`PECOS`) is unchanged; only its state and note are truthful now. Bundle still contains GovCon code under the TEFCA profile.
BLOCKED_VERIFICATIONS = DEV runtime provenance; PDF engine; browser zoom; GovCon backend boundary (no GovCon backend exists here); independence (this session built the code)

CHECKER_PRIORITY_1 = FEDERAL DEPLOYMENT DEFENSIBILITY: run two instances with `DOCUACTION_PROGRAM=ALL` and `TEFCA_ARC`, call the GovCon and Core-optional endpoints with TEFCA-role tokens and prove 404 with no data; open the TEFCA-profile export and prove no GovCon request leaves the browser.
CHECKER_PRIORITY_2 = TEFCA ZERO REGRESSION: rediscover the 30 TEFCA routes from the export and run the full matrix at bfa7902 against the 52f91d5 baseline.
CHECKER_PRIORITY_3 = AUTHENTICATION: diff the auth files (none changed), reproduce the 15-minute expiry and the missing refresh flow, and review `docs/security/AUTHENTICATION_TARGET_ARCHITECTURE.md`.
CHECKER_PRIORITY_4 = DEV PROVENANCE: nothing deployed; verify DEV still serves 0cf0284 / 6.0.0 and that the pending run is human-gated.
CHECKER_PRIORITY_5 = GITHUB GOVERNANCE: verify enforcement, not documentation.

FINAL_CHECKER_MUST_USE_NEW_SESSION = YES
FINAL_CHECKER_MUST_NOT_SHARE_BUILDER_CONTEXT = YES

NEXT_ACTOR = GENUINELY INDEPENDENT CHECKER

STOP.

DO NOT MERGE.
DO NOT DEPLOY PROD.
DO NOT ENABLE GATE H.
DO NOT START RELEASE 1.1 IMPLEMENTATION.
DO NOT START ENTITY INTELLIGENCE EXPANSION.
