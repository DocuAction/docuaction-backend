# DocuAction Independent AI Audit + DevOps + Product Verification Report (2026-09-13)

**Independence statement.** This audit was executed by the same AI session (Claude Fable 5.1, session d85df1d1) that produced the builder work under review. Under `docs/governance/DOCUACTION_INDEPENDENT_AI_AUDIT.md` section 1 this is a **builder self-review performed in auditor mode**: `INDEPENDENCE = NOT_ESTABLISHED`. It cannot confer `INDEPENDENT_AUDIT_PASS`. Every statement below is nevertheless based on fresh, independently re-executed evidence (re-run tests, regenerated artifacts, live runtime reads), not on the builder's own reports. A second, separate checker session should re-run the gates in section G before any merge decision.

```
AUDIT_STARTED                      = 2026-09-13T07:17:11Z
AUDIT_COMPLETED                    = 2026-09-13T07:40Z
AUDITED_BACKEND_SHA                = 75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96 (main)
AUDITED_FRONTEND_SHA               = 0f995782fbbe90585da839c25aec7fb7c3006b3d (main); PR #40 head edff7dca76e58a491b32751e2a10af831d0a294b
AUDITED_ENTITY_INTELLIGENCE_SHA    = 4ef1bba5f366b5932b439315642f3ea57aaf8a48 (PR #54 head; base 0cf028405299b1a132b664d9495c3c4ab6a0a415)
AUDITED_WEASYPRINT_PR56_SHA        = c40503185d47edc41fd9129ecc8ec07c7c00608b
AUDIT_RESULT                       = FAIL  (driven by two HIGH repository-control findings that pre-date the audited changes; see note)
```

**Result note.** No CRITICAL finding. The two HIGH findings (AUD-01 workflow input injection in the PROD migration job, AUD-02 unprotected `main`) are pre-existing repository controls, not defects introduced by the audited changes. Under the result definitions (`HIGH = 0` required) the formal result is `FAIL`. If the human authority records those two items as accepted existing risk with a dated remediation plan, the code-level result of the audited changes would be `PASS_WITH_NONBLOCKING_FINDINGS`, still without merge authorization and still without independence.

## Freeze

| Item | Value |
|---|---|
| Backend main | 75383cf (PR #55 merged 2026-09-13T06:55:42Z) |
| Frontend main | 0f99578 |
| Builder branches | backend `feat/entity-intelligence-foundation` (4ef1bba, base 0cf0284), backend `chore/weasyprint-70` (c405031), frontend `feat/global-theme-a11y-lms` (edff7dc) |
| PRs | backend #54 OPEN draft, #55 MERGED, #56 OPEN (CI green); frontend #40 OPEN draft |
| Uncommitted / untracked | none / backend `docs/PROD_PHASE_B_RELEASE_EVIDENCE_2026-09-09.md` (untracked, pre-existing) |
| Deployed DEV backend | image `acrdocuactiondev.azurecr.io/docuaction-backend@sha256:4384b211ea62bbdd3412ed919ab205920e3da092176207347f966fbdbdc47d8d` (ACR tag `0cf0284`, built 2026-09-12T00:23Z by manual `az acr build`, not by a workflow run); `/health` 200, version 6.0.0, no SHA field |
| Deployed DEV frontend | SWA `Last-Modified` 2026-09-12 02:56:48 GMT (pre-sprint build; PR #40 not deployed) |
| Alembic head | `20260903_delivery_grants` (21 version files, none added by any audited branch) |
| PROD | not read, not touched |

## A. Severity

CRITICAL = 0, HIGH = 2, MEDIUM = 10, LOW = 4, INFORMATIONAL = 3 (19 findings, section O).

## B. Safety / contract baseline

TASK_1..6_CHANGED = NO (EI branch touches no `app/Tefca/**`, `app/api/**`, `app/models/**` Task path except `app/core/config.py` +12 flag lines; PR #55 = `app/reports/data/release.py` -2/+1 plus one test). GOVERNMENT_DATA_CHANGED = NO. REAL_ONC_CASE_CHANGED = NO. SHARED_QA_DB_CHANGED = NO (no DB connection made by the audit; DEV `STARTUP_SCHEMA_MUTATION_ENABLED=false`). PROD_TOUCHED = NO. SECURITY_WEAKENED = NO. METHODOLOGY_CHANGED = NO.

## C. Architecture

CORE_BOUNDARY = PASS (`tests/test_core_boundary.py`, 5 AST checks, platform-wide). CORE_TO_TEFCA_IMPORT = 0. ACQUISITION_INTERPRETATION_SEPARATION = PASS (identical assessment/basis across FILE_DOWNLOAD, DIRECT_QUERY, THIRD_PARTY_DELIVERY, OPERATOR_UPLOAD; reasoning modules contain no transport code). INVARIANTS_SPECIFIED = 31. INVARIANTS_EXECUTABLY_ENFORCED = 21 (2,4,5,6,7-in-app,8,9,10,11,13,14,17,18,19,20,22,23,24,25,26,28). PARTIAL / SOURCE-ONLY = 9 (3,12,15,16,21,27,29-platform,30,31-probe only). NOT_APPLICABLE = 1 (invariant 1 is permissive). INVARIANTS_FAILED = 0 at code level; invariant 7 (maker != checker) is enforced inside the application but **not** at repository level (AUD-02) and not by this audit's own independence (AUD-03).

## D. Database / migrations

ALEMBIC_HEAD = 20260903_delivery_grants. SHARED_QA_SCHEMA_CHANGED = NO. ENTITY_INTELLIGENCE_TABLES_IN_SHARED_QA = NO (models bind `EntityIntelligenceBase`; `migrations/apply.py` refuses non-local URLs; platform `create_all` sites bind `Base` only). ISOLATED_MIGRATIONS_PENDING_IN_BRANCH = YES (EI branch, applied only by its own local-only runner). ISOLATED_MIGRATIONS_IN_ALEMBIC_CHAIN = NO. ACCIDENTAL_UPGRADE_HEAD_RISK = LOW.

## E. DevOps

GITHUB_ACTIONS = 13 backend + 4 frontend workflows inspected; findings AUD-01, 04, 05, 06, 07, 16. OIDC = PASS (vars.* client/tenant/subscription, no `AZURE_CREDENTIALS`). DEV_PROD_SEPARATION = PASS (separate resource groups, separate environments, PROD reviewer-gated). BRANCH_PROTECTION = FAIL (backend `main` unprotected, rulesets empty; frontend unknown, API 403 on GitHub Free private). ENVIRONMENT_PROTECTION = PASS (development and production each require reviewer "DocuAction"). MIGRATION_CONTROL = PASS with AUD-01/AUD-04 (handshake-issue workflow; last stored DB credential `MIGRATION_DATABASE_URL` still used in `deploy-backend.yml`). CONTAINER_BUILD = NOT_TESTED (no Docker on the audit host; Dockerfile bakes no secret, CMD gunicorn/uvicorn). DEV_HEALTH = 200. ARTIFACT_PROVENANCE = PARTIAL (AUD-07). BACKEND_PROVENANCE_CHAIN = 0cf0284 -> (no workflow run) -> ACR tag 0cf0284 -> digest 4384b211 -> development -> manual container config -> /health 6.0.0 without SHA. FRONTEND_PROVENANCE_CHAIN = manual SWA CLI upload, no run id. REQUIRED_CHECKS = none configured.

## F. Security

SECRET_SCAN = CLEAN (auditor grep over both repos; only a DAST test string). FRONTEND_SECRET_SCAN = CLEAN (built bundle; only external host `docuaction-dev.azurewebsites.net`). API_SECURITY = PASS with AUD-15 (protected routes 401; two informational unauthenticated routes). CODEQL = 30 open alerts, all created 2026-07-20 to 2026-08-26 (pre-existing: 15 stack-trace-exposure, 11 url-substring, 1 clear-text-storage in seed.py, 2 ReDoS, 1 reflective XSS in bulletin); CODEQL_NEW = 0. DEPENDENCY_REVIEW = workflow present. DEPENDENCY_VULNERABILITIES = backend pip-audit 3 (weasyprint 69.0 PYSEC-2026-3940 fix 70.0 = PR #56; ecdsa 0.19.2 PYSEC-2026-1325 no fix, via python-jose 3.5.0); frontend npm audit prod: next 16.2.12 CRITICAL range (GHSA-p293-qw3h-jr36, GHSA-2xp9-vwfh-vxw4; static export, no Next server at runtime), sharp 0.35.3 HIGH (<0.35.4, build-time). RBAC = PASS (source + existing tests). AUTH = PASS. MAKER_CHECKER = PASS in-app (`test_an_analyst_cannot_approve_their_own_determination`, `test_the_analyst_cannot_qa_their_own_determination`, `test_sod_trigger_refuses_self_review`, `test_an_analyst_cannot_qa_their_own_priority_review`, `test_refuses_analyst_self_approval`); live role walk BLOCKED (no DEV credentials for automation). DEV secrets are all Key Vault references (7 names, values not read).

## G. Build / test

BACKEND_TESTS (EI head 4ef1bba, full suite, local) = 3307 passed / 333 skipped / 0 failed, 155 s. Main (75383cf) CI run 34743956490 = 2817 passed / 379 skipped / 0 failed, 52 s, 3196 collected (auditor `--collect-only` on a clean worktree of main = 3196, matching CI). FRONTEND_BUILD (PR #40 head) = 81 pages, 0 errors, 30 s; `npm run test:ui` 66/66. ARCHITECTURE_TESTS = PASS. SECURITY_TESTS = PASS. LMS_TESTS = PASS (16 modules asserted). REPORT_TESTS = PASS incl. cross-format reconciliation. ENTITY_INTELLIGENCE_TESTS = 436 passed, 0 assertion-free except `test_never_raises_on_garbage` (exception-only, see AUD-10). TEST_QUALITY = GOOD (synthetic data only, no external calls, no shared DEV mutation). UNEXPECTED_SKIPS = 0 (all skips are DB-unreachable or Azure-artifact env-not-set).

## H. UI / UX

TOTAL_ROUTES = 79 (81 prerendered pages). ROUTES_RENDERED = 1 (`/login`, builder run; auditor re-render BLOCKED, Chrome extension unresponsive after two attempts). ROUTES_SOURCE_ONLY = 78. ROUTES_BLOCKED = 78 (authenticated). VISUAL_ROUTES_FULLY_VERIFIED = 0. VISUAL_ROUTES_PARTIAL = 1. VISUAL_ROUTES_BLOCKED = 78. LIGHT_MODE = SOURCE_ONLY. DARK_MODE = SOURCE_ONLY (known residual: inline hex `style={{}}` on GovCon pages). THEME_LEAKAGE = EXPECTED on inline-literal pages, not measured. DESKTOP/TABLET/MOBILE/ZOOM_200/ZOOM_400/KEYBOARD/FOCUS/RESPONSIVE = BLOCKED / NOT_TESTED. **No visual PASS is asserted for any route.**

## I. Accessibility (DEV accessibility verification, not a 508 certification)

AUTOMATED_ACCESSIBILITY = login only (builder axe run; not reproduced by auditor). MANUAL_ACCESSIBILITY = NOT_TESTED. LIGHT_CONTRAST / DARK_CONTRAST = NOT_MEASURED. SEMANTICS = SOURCE (skip links, `#app-main`, `<main>`, `<h1>` via ActionBar). TABLES/FORMS/DIALOGS/CHARTS = NOT_TESTED. KNOWN_GAPS = the five items listed by the builder in `frontend/docs/DEV_ACCESSIBILITY_VERIFICATION_EVIDENCE_2026-09-13.md` stand.

## J. Learning Center

MODULES = 16 (`app/Tefca/learning_content.py`, asserted by `tests/test_learning_center.py`). KNOWLEDGE_VERSION = 1.2.0 (unchanged). ROLE_PATHS = SOURCE (PM/Analyst/QA/Admin/Viewer). VISUAL_EXPERIENCE = SOURCE_ONLY (build compiles; not rendered). SEARCH/NAVIGATION/DIAGRAMS/KNOWLEDGE_CHECKS = SOURCE_ONLY. TRACEABILITY = presentation-only change, LMS_CHANGE_REQUIRED = NO. CONTENT_TRUTH = PASS (grep: no SLA, D2-accepted, synthetic-as-Government, NPI-as-credential, or CMS-as-TEFCA teaching). STALE_LINKS = NOT_TESTED. KYP_CURRENT_STATUS = not adopted (RCE vetting requires legal name, DBA, corporate address, website, site of care, NPI, provider type, HIPAA-CE evidence; KYP absent from LMS). KYP_FALSE_CURRENT_REQUIREMENT_CLAIMS = 0.

## K. Reporting (auditor-generated synthetic D3.1, `retrospective_weekly`, persist=False)

REPORT_TEMPLATE = PASS (cover, Document Control, Contents, sections 1 to 10, Accessibility, Report Provenance; prepared for HHS, prepared by Alliance Global Tech Inc.; `<meta name="author">`). GOVERNMENT_BRANDING = OFF (no `<img>`, no gov-mark element; only an unused CSS rule). DOCX = PASS (Title/Heading 1/Heading 2/Caption styles, core title, author, language en-US, keywords, 7 tables with repeating header rows, TOC field, footer PAGE field, header text, DEVELOPMENT / TEST label, all three synthetic entities, contract, period). PDF = BLOCKED locally (`PDFEngineUnavailable` on Windows); Linux CI render job on PR #56 (WeasyPrint 70.0) passed. HTML = PASS (lang en, title, 14 h2, 8 tables with captions, 76 `th`, `@page` print CSS, no `data-theme` or `prefers-color-scheme` hook) with AUD-14 (two `<h1>`). CSV = PASS (metadata header lines, 19 rows, BOM added at package time). ZIP = PASS (docx, html, csv, README.txt, manifest.json; PDF omitted with recorded reason). PACKAGE_NAMING = PASS (`7571MN26F80064_Task3_D3.1_Weekly_2026-09-07_2026-09-13_DA-ARC-2026-001.zip`, members share the stem). CROSS_FORMAT_RECONCILIATION = PASS (report id, contract, task, deliverable, period, status "Draft, awaiting PM review", entities, four categories incl. "Minor or administrative discrepancies", data payload hash, classification NO_DATASET_LOADED identical across formats; repo test `test_report_cross_format_reconciliation.py` also passes). REPORT_ACCESSIBILITY = DOCX/HTML structure verified; PDF tagging NOT_VERIFIED on this host; no PDF/UA claim. PRINT_THEME_INDEPENDENCE = PASS (report HTML carries no theme hook; app print CSS forces light). WEASYPRINT_VERSION = 69.0 local (pydyf 0.12.1, fonttools 4.63.0, cssselect2 0.9.0, tinycss2 1.5.1, Pillow 12.3.0, cffi 2.0.0, pyphen 0.18.1); PR #56 moves to 70.0. WEASYPRINT_CVE_CLEAR = NO until PR #56 merges. UNEXPECTED_RENDER_NETWORK_CALL = none found (engines contain no HTTP fetch; `accessibility.py` enforces no `https://` in executable text). REPORT_GENERATION_REGRESSION = NONE. REPORT_DATA_CHANGED = NO.

## L. Entity Intelligence (feature OFF, branch only)

FEATURE_OFF = PASS (default False; false/False/FALSE/0/off/no/missing -> off; ""/"maybe" -> startup ValidationError, fail-closed; OFF -> `FeatureDisabled`; no route in OpenAPI: 411 paths / 106 schemas on base, main and EI head). EVIDENCE_PROFILE = PASS. SOURCE_AUTHORITY = PASS (MATRIX_V1, approved_by PENDING_HUMAN_APPROVAL; four weak sources vs one authoritative -> CONFLICTING_EVIDENCE, no vote). SOURCE_RIGHTS = model + records inspected: NPPES V2 = ASSUMED_PUBLIC_DOMAIN, no retention/redistribution right claimed, HUMAN_REVIEW_STATUS = NOT_REVIEWED; IQVIA OneKey = TERMS_REVIEW_REQUIRED, AWAITING_SCHEMA, `operational_use_permitted()` = False even with an authorization reference. SOURCE_RIGHTS_RECORDS_DOCUMENTED = 2. SOURCE_RIGHTS_REVIEWED_BY_HUMAN = 0. SOURCE_RIGHTS_ASSUMPTIONS_REMAINING = 2. LOCATION_ROLES = 15 members; same address + registered agent / HQ / mailing role -> ROLE_ASSIGNMENT_DIFFERS (PASS); unknown role -> NORMALIZED_LOCATION_MATCH (AUD-11). NORMALIZED_ADDRESS_CONTRACT = PASS (raw preserved, addr-norm-1.1 key separate; ZIP+4, suite, casing handled) with AUD-10 on non-string input. TYPED_RELATIONSHIPS = PASS. HISTORICAL_DELTA = PASS (SOURCE_NO_LONGER_REPORTS_OBSERVATION, RELATIONSHIP_PERIOD_DIFFERS). EXPLAIN_DIFFERENCE = PASS (deterministic templates). SYSTEM_ASSESSMENT = closed vocabulary, no numeric confidence terms in output. HUMAN_BOUNDARY = PASS. MULTI_JURISDICTION_PROOF / SAME_ADDRESS_DIFFERENT_ROLE / TEMPORAL_PROOF = PASS (repo tests + auditor probe). Unicode accent difference -> name not equal (by design); duplicate identical evidence -> AUD-09; malformed int postal code -> AUD-10; source unavailable + conflict -> CONFLICTING_EVIDENCE. 2K/5K/10K/25K/50K = builder figures in `ENTITY_INTELLIGENCE_PERFORMANCE_REPORT.md` reviewed, disclaimer present; not independently re-run (NOT_VERIFIED). PEAK_MEMORY / NONLINEAR_BEHAVIOR = NOT_VERIFIED.

## M. External sources

NPPES = file-based adapter, code 6 pointer / `<UNAVAIL>` handled. PPEF = NOT AUTHORIZED FOR INTEGRATION. IQVIA_SCHEMA_STATUS = AWAITING_SCHEMA. IQVIA_TERMS_STATUS = AWAITING_TERMS. IQVIA_RIGHTS_STATUS = TERMS_REVIEW_REQUIRED. IQVIA_PROFILE_ONLY = YES. IQVIA_INTERPRETATION_USED / ASSESSMENT_USED / REPORT_USED = NO. GOOGLE_STATUS = RESEARCH_ONLY (no code path, flag default False). GOOGLE_API_CALLED = NO. GOOGLE_KEY_PRESENT = NO. GOOGLE_DATA_PERSISTED = NO. STATE_PROVIDER = DESIGN_ONLY (AcquisitionMode enum). LIVE_STATE_ADAPTERS = 0.

## N. Builder claim matrix

| Claim | Auditor evidence | Result |
|---|---|---|
| Tasks 1 to 6 unchanged | diff of EI branch vs base; PR #55 diff | VERIFIED |
| Government data unchanged | no DB access by any sprint; DEV mutation flag false; report classification NO_DATASET_LOADED | VERIFIED |
| PROD untouched | no PROD workflow runs; PROD not read | VERIFIED |
| Security preserved | secret scans, CodeQL delta 0, OIDC intact | VERIFIED (pre-existing gaps AUD-01/04/05/06) |
| All-page light support | source guardrails only | NOT_VERIFIED (visual) |
| All-page dark support | source guardrails only | NOT_VERIFIED (visual) |
| Accessibility | login axe (builder); source landmarks | PARTIALLY_VERIFIED |
| Learning Center improvement | build + guardrails; content unchanged | PARTIALLY_VERIFIED (not rendered) |
| Report template | regenerated artifacts | VERIFIED |
| Report reconciliation | regenerated artifacts + repo test | VERIFIED |
| Entity Intelligence isolation | AST tests, OpenAPI diff, create_all audit | VERIFIED |
| Synthetic proofs | repo tests + adversarial probe | VERIFIED (with AUD-09/10/11) |
| Scale results | report reviewed, not re-run | NOT_VERIFIED |
| No secret exposure | greps, bundle scan, Key Vault references | VERIFIED |
| DevOps success | dev-release never succeeded; DEV built manually | CONTRADICTED (AUD-07) |
| Full suite 3310 passed on main | CI 2817/379 (3196 collected) on main; 3307/333 on EI head | CONTRADICTED as stated (AUD-17) |

VERIFIED = 9, PARTIAL = 2, NOT_VERIFIED = 3, CONTRADICTED = 2, BLOCKED = 0 (visual items counted under NOT_VERIFIED).

## O. Findings

Common fields: AFFECTED_SHA as listed; RETEST_REQUIRED = YES unless stated.

**AUD-20260913-01 | HIGH | devops** Workflow-dispatch inputs are interpolated directly into `run:` shell in jobs that hold PROD/DEV OIDC identity. REQUIREMENT: no untrusted input expansion in shell (GitHub script-injection guidance). EVIDENCE: `.github/workflows/prod-migration.yml:67` (`gh issue comment "${{ inputs.handshake_issue }}"`), `migration-preflight.yml:119,120,123,245`, `dev-release.yml:239,240`. AFFECTED_SHA 75383cf. REPRODUCTION: dispatch with `handshake_issue` = `1" ; <command> ; echo "`. EXPECTED: inputs passed via `env:` and quoted `$VAR`. ACTUAL: literal expansion. BUSINESS_IMPACT: a collaborator with dispatch rights can run arbitrary commands inside the PROD migration job (mitigated by environment reviewer approval and write-only trigger rights). FIX: move every `inputs.*` to `env:` and reference `"$HANDSHAKE_ISSUE"`; validate numeric issue ids.

**AUD-20260913-02 | HIGH | governance** Backend `main` has no branch protection and no rulesets; no required status checks; `.github/CODEOWNERS` describes a review policy the platform does not enforce. EVIDENCE: `gh api repos/.../branches/main/protection` -> 404; rulesets `[]`. REPRODUCTION: any collaborator can push directly to `main`. EXPECTED: PR required, 1 approval, latest-push approval, required checks, no force push, admins included. BUSINESS_IMPACT: maker/checker model exists only by convention; automated dev-release triggers on any push. FIX (human, not tonight): enable protection per section P; correct CODEOWNERS text. RETEST: auditor re-reads protection API.

**AUD-20260913-03 | MEDIUM | governance** Audit independence not established: builder and auditor are the same session. EVIDENCE: this session's history. EXPECTED: distinct checker. BUSINESS_IMPACT: `INDEPENDENT_AUDIT_PASS` unobtainable from this report. FIX: run a separate checker session against the frozen SHAs using section G commands. RETEST: N/A (process).

**AUD-20260913-04 | MEDIUM | security/devops** Secrets expanded on shell command lines (visible in process listings / error output). EVIDENCE: `stackhawk-scan.yml:42,74` (HAWK_API_KEY, DAST_USER/DAST_PASSWORD in curl body), `deploy-backend.yml:351,371,372` (`MIGRATION_DATABASE_URL`, the last stored DB credential). EXPECTED: secrets via `env:` and read by the tool; migration path via OIDC only. FIX: use `env:`; retire MIGRATION_DATABASE_URL in favour of the OIDC handshake path already used by prod-migration.

**AUD-20260913-05 | MEDIUM | devops** Long-lived deployment tokens and unprotected DEV frontend deploy. EVIDENCE: frontend `deploy-frontend.yml:104,152` (SWA_DEV_TOKEN, SWA_PROD_TOKEN); job `deploy-dev` (line 88) has no `environment:`; backend `FRONTEND_REPO_TOKEN`, DAST credentials as repo secrets. EXPECTED: SWA deploy via OIDC or environment-scoped secret; dev job under `development` environment. FIX: scope tokens to environments; rotate; evaluate SWA OIDC deploy.

**AUD-20260913-06 | MEDIUM | devops** Missing top-level `permissions:` and no SHA-pinned third-party actions. EVIDENCE: `security-nightly.yml`, `pdf-linux.yml`, `deploy-backend.yml` (build-and-test), frontend `security-scan.yml`, `deploy-frontend.yml`; zaproxy/stackhawk actions by tag. EXPECTED: `permissions: {contents: read}` default; pin by SHA. FIX: add least-privilege permissions; pin actions.

**AUD-20260913-07 | MEDIUM | devops** Provenance chain broken for DEV. EVIDENCE: DEV image built by manual `az acr build` (tag `0cf0284`), no workflow run; dev-release runs 34661300993, 34573512155, 34571780065, 34566719623 cancelled; run 34743956579 for 75383cf pending with 0 jobs; last successful container-release 2026-09-01; frontend deployed via SWA CLI; `/health` has no SHA; `dev-release` path filter allows drift. EXPECTED: every deployed artifact traceable to a run. FIX: approve or cancel the pending run; add `GIT_SHA` build arg into `/health`; remove manual build path from runbooks.

**AUD-20260913-08 | MEDIUM | deps (frontend)** `next` 16.2.12 in CRITICAL advisory range (GHSA-p293-qw3h-jr36 Windows-hosted RCE, GHSA-2xp9-vwfh-vxw4 image-optimization AVIF); `sharp` 0.35.3 HIGH (<0.35.4) and the `overrides` entry pins `^0.35.3`. Static export on SWA has no Next server, so runtime exposure is NOT_REPRODUCED; build-time and developer-machine exposure remains. FIX: upgrade next to >=16.3.3 and sharp to >=0.35.4; re-run `npm audit --omit=dev`, build, guardrails.

**AUD-20260913-09 | MEDIUM | ei** Duplicate identical identifier observations from one source produce `MULTIPLE_CANDIDATE_ENTITIES` and downgrade to EVIDENCE_PARTIALLY_CORROBORATES. EVIDENCE: auditor probe `duplicate_evidence`. EXPECTED: identical values de-duplicated before candidate counting (the CMS adapter design expects several enrollment rows per NPI). ACTUAL: false multi-candidate signal. IMPACT: systematic noise once multi-row sources arrive (fail-safe direction, no over-claim). FIX: dedupe by (source, type, role, normalized value) in `compare_identifiers`; add a test.

**AUD-20260913-10 | MEDIUM | ei** Non-string address field (int postal_code) raises an uncaught `TypeError` in normalization; `test_never_raises_on_garbage` contains no assertion and no non-string cases. EVIDENCE: probe `malformed` -> `TypeError("expected string or bytes-like object, got 'int'")`. EXPECTED: coerce or reject at intake with a typed absence/rejection. FIX: `str()` coercion or intake validation in `normalize.py` / `intake_safety.py`; extend the test with ints, None, nested dicts.

**AUD-20260913-11 | MEDIUM | ei** `UNKNOWN_SOURCE_ROLE` at the same address yields `NORMALIZED_LOCATION_MATCH` (unknown role treated as compatible). EVIDENCE: probe `unknown_source_role` -> EVIDENCE_CORROBORATES. EXPECTED: unknown role -> a role-unknown signal (or ROLE_ASSIGNMENT_DIFFERS-class signal) that cannot corroborate a care-site question; "unknown != false" must also mean "unknown != match". FIX: treat UNKNOWN_SOURCE_ROLE as non-corroborating for role-sensitive dimensions; add a test.

**AUD-20260913-12 | MEDIUM | ui/a11y** All-page light/dark and accessibility claims are SOURCE_ONLY for 78 of 79 routes; visual PASS is not established. EVIDENCE: builder evidence doc; auditor render BLOCKED. EXPECTED: rendered evidence in both themes before PR #40 merges. FIX: human or credentialed session renders the route inventory; capture axe results; tokenise inline hex on GovCon/dashboard pages.

**AUD-20260913-13 | LOW | deps (backend)** ecdsa 0.19.2 PYSEC-2026-1325 (no fix, via python-jose 3.5.0); weasyprint 69.0 PYSEC-2026-3940 (fix in PR #56). FIX: merge PR #56 after human review; evaluate replacing python-jose with PyJWT/cryptography-only path.

**AUD-20260913-14 | LOW | reporting/a11y** HTML report contains two `<h1>` elements (cover title and body title). EXPECTED: one document-level h1; cover title as `role="doc-title"` or `<p class="cover-title">`. FIX: template change; re-run reconciliation test.

**AUD-20260913-15 | LOW | security** `/api/config` and `/api/tefca/status` answer unauthenticated with environment, version, api host and connector health (pecos shown "available" although it is an NPPES proxy). FIX: confirm intended; trim connector detail or require auth.

**AUD-20260913-16 | LOW | devops** `deploy-backend.yml:43` comment misdescribes the workflow_call guard; `security-scan` steps end with `|| true` (evidence-only, never gating). FIX: correct comment; decide which scans gate.

**AUD-20260913-17 | INFORMATIONAL | tests** Builder-reported "3310 passed" on main is not reproducible against the frozen SHA (main collects 3196 tests; CI 2817 passed / 379 skipped; EI head 3307 / 333). EI branch lacks PR #55 and needs a rebase before merge. RETEST_REQUIRED = NO (documentation).

**AUD-20260913-18 | INFORMATIONAL | ei** Non-boolean flag strings ("", "maybe") cause a startup `ValidationError` (fail-closed). Document in the ops runbook so a typo is diagnosed quickly. RETEST_REQUIRED = NO.

**AUD-20260913-19 | INFORMATIONAL | governance** No AGENTS.md, CLAUDE.md, copilot-instructions or path instructions exist in either repository; no conflicting AI instructions; this audit created `docs/governance/DOCUACTION_INDEPENDENT_AI_AUDIT.md` (documentation only). RETEST_REQUIRED = NO.

## P. AI / GitHub governance

AGENTS_MD = MISSING. COPILOT_INSTRUCTIONS = MISSING. PATH_INSTRUCTIONS = MISSING. CLAUDE_MD = MISSING. CONFLICTING_AI_INSTRUCTIONS = NONE. INDEPENDENT_AUDIT_POLICY = CREATED (`docs/governance/DOCUACTION_INDEPENDENT_AI_AUDIT.md`). Recommended division (not created tonight): a short `AGENTS.md` at each repo root pointing to the policy and the standing safety boundaries; path instructions for `app/core/**` (Core never imports TEFCA), `app/Tefca/**` (Task 1 to 6 frozen, methodology owner), `app/reports/**` (branding OFF, reconciliation test mandatory), `frontend/src/**` (tokens only, guardrails), Learning Center (knowledge-version bump rules), `.github/workflows/**` (OIDC only, `env:` for inputs/secrets).

BRANCH_PROTECTION_CURRENT = none (backend); unknown (frontend, API 403). BRANCH_PROTECTION_RECOMMENDED (do not change tonight): PR required; 1 approval; dismiss stale approvals; require approval of most recent reviewable push; conversation resolution; required checks; block force push and deletion; include administrators; keep environment reviewers.

REQUIRED_CHECKS_CURRENT = none. REQUIRED_CHECKS_RECOMMENDED: backend `pytest` (pr-tests.yml, EXISTS, RELIABLE, 52 s), `lint`/architecture (`tests/test_core_boundary.py` runs inside pytest: EXISTS), security tests (inside pytest: EXISTS), secret-scan (gitleaks workflow: EXISTS), dependency-review (EXISTS), code-scanning CodeQL (EXISTS; reliability on PRs to confirm), lms-sync (inside pytest: EXISTS), report-reconciliation (inside pytest since PR #55: EXISTS), pdf-linux render (EXISTS, slower, recommend required for `app/reports/**` only), frontend build (EXISTS), frontend `test:ui` guardrails (EXISTS in script, MISSING as a workflow job), accessibility (MISSING, no runner). Do not make StackHawk/ZAP DAST required (UNRELIABLE, `|| true`).

## Q. Gate H (CMS PPEF via existing Task 3 snapshots)

GATE_H_RECOMMENDATION = builder recommends option "adapter over existing `tefca_ppef_snapshots` through an injected port" (see `docs/entity-intelligence/GATE_H_RECOMMENDATION.md`). GATE_H_EVIDENCE = PPEF already ingested by frozen Task 3; no new acquisition needed. GATE_H_ARCHITECTURAL_VALUE = high (first program-participation source, exercises the sixth dimension). GATE_H_ONC_VALUE = medium (Tier 2 vetting accepts a PPEF listing link until 2026-12-31). GATE_H_CROSS_HHS_VALUE = medium. GATE_H_DATA_RIGHTS = public CMS data; rights record must still be human-reviewed (currently 0 reviewed). GATE_H_EXTERNAL_DEPENDENCY = none new. GATE_H_IMPLEMENTATION_EFFORT = small (adapter + tests). GATE_H_RISK = low if read-only over existing snapshots and feature stays OFF; AUD-09 must be fixed first because PPEF has several enrollment rows per NPI. GATE_H_AUDITOR_VIEW = technically sound and safely sequenced after AUD-09/10/11 and a human rights review; not authorized by this report. GATE_H_AUTHORIZED_BY_IMRAN = PENDING.

## R. Morning actions

BUILDER_FIXES_REQUIRED = AUD-01, 04, 05, 06, 07 (workflow hygiene, separate PR); AUD-08 (frontend deps); AUD-09, 10, 11 (EI, on PR #54 after rebase); AUD-14, 15, 16 (low). HUMAN_DECISIONS_REQUIRED = AUD-02 branch protection; PR #56 merge; pending dev-release run 34743956579 (approve or cancel); accept/decline AUD-01/02 as existing risk; rights review for NPPES/IQVIA records; separate checker session. SAFE_TO_CONTINUE_TEAM_QA = YES (nothing audited changes the QA baseline; DEV unchanged). SAFE_TO_MERGE_UI = NO (AUD-12, AUD-08). SAFE_TO_MERGE_REPORTING = PR #55 already merged; PR #56 eligible on CI evidence, human decision. SAFE_TO_MERGE_ENTITY_INTELLIGENCE = NO (draft; AUD-09/10/11; rebase; gates A to H open). SAFE_TO_DEPLOY_DEV = only via the reviewer-gated workflow after a human approves; not from this report. SAFE_TO_DEPLOY_PROD = NO.

## S. Final

MERGE_ELIGIBLE = NO for PR #54 and PR #40; PR #56 = YES on evidence (CI green incl. Linux render). MERGE_AUTHORIZED = NO.

TOP_5_CONFIRMED_STRENGTHS: (1) Core boundary enforced by AST tests across the whole platform; (2) Entity Intelligence invisible while OFF (no route, no shared schema, fail-closed flags); (3) report artifacts reconcile across formats with Government branding OFF and deterministic naming; (4) OIDC-only Azure auth with reviewer-gated environments and Key Vault-referenced DEV secrets; (5) in-app maker/checker refusals covered by five existing tests.

TOP_5_BLOCKERS: AUD-02 unprotected main; AUD-01 input injection in PROD migration job; AUD-12 unverified visual claims for PR #40; AUD-07 broken DEV provenance; AUD-03 no independent checker.

TOP_5_NONBLOCKING_FINDINGS: AUD-09, AUD-10, AUD-11 (EI edge cases), AUD-08 (frontend deps), AUD-04 (secrets on command lines).

TOP_5_NEXT_ACTIONS: (1) human enables branch protection and required checks; (2) builder PR fixing workflow input/secret handling; (3) builder rebases PR #54 and fixes AUD-09/10/11; (4) credentialed visual acceptance of PR #40 in both themes; (5) independent checker session re-runs section G against the same SHAs.

## Auditor self-check

Verified rather than repeated: yes. Exact SHAs: yes. Independence: NO (declared). Application code modified: NO (two Markdown files under `docs/governance/` only). Task 1 to 6, migrations, DevOps, Actions, OIDC, provenance, secrets, bundles, CodeQL delta: inspected. Visual rendering per page: NO (blocked, not claimed). Tablet/mobile/zoom/keyboard: NOT_TESTED (not claimed). 16 modules: counted, not rendered. LMS truth and KYP: verified by content grep. Artifacts: generated and reconciled; PDF blocked locally. WeasyPrint: versions and dependency graph inspected. EI OFF, Core boundary, acquisition firewall, rights records, IQVIA, Google, state: verified. Adversarial cases: run. Scale: not re-run. Gate H: not authorized. Merge: none. PROD: untouched.
