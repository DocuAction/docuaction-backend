# DocuAction Controlled Remediation Builder Report (2026-09-13)

DATE = 2026-09-13. BUILDER_SESSION = MAKER (Claude Fable 5.1, session d85df1d1; the same session that built the audited code and wrote the audit report, so nothing here is an independent checker result). Every lane is a DRAFT pull request; nothing was merged, deployed, or authorized.

## A. Safety

TASK_1_CHANGED = NO. TASK_2_CHANGED = NO. TASK_3_CHANGED = NO (report templates changed only in heading markup; Task 3 data paths, categories, sampling and stratification untouched). TASK_4_CHANGED = NO. TASK_5_CHANGED = NO. TASK_6_CHANGED = NO. GOVERNMENT_DATA_CHANGED = NO. REAL_ONC_CASE_CHANGED = NO. SHARED_QA_DB_CHANGED = NO (no lane connected to any database; Lane D's scale harness is in-memory synthetic). PROD_TOUCHED = NO. METHODOLOGY_CHANGED = NO. SECURITY_WEAKENED = NO. ENTITY_INTELLIGENCE_ENABLED = FALSE, PLATFORM_INTERNAL.

## B. Findings

PRIOR_FINDINGS_TOTAL = 21 (19 in the audit report section O plus AUD-20/21 from section 7D; none dropped). CONFIRMED = 18. PARTIALLY_CONFIRMED = 1 (AUD-12: literals reproduced and fixed; visual runtime part BLOCKED). NOT_REPRODUCED = 0. INCORRECT = 0. BLOCKED_FROM_VERIFICATION = 1 (AUD-03, process). FIXED (code or workflow) = 11 (AUD-01, 04, 06, 07, 08, 09, 10, 11, 14, 16, 20). DOCUMENTED / ACCEPTED (no code change by design) = 6 (AUD-05, 13, 15, 17, 18, 19). HUMAN ACTION ONLY = 3 (AUD-02, 03, 12 runtime part). REMAINING (open after this sprint) = AUD-02, AUD-03, AUD-12 (runtime), AUD-13 (ecdsa, no upstream fix), AUD-21 (test coupled to shared-DB state; left untouched because it is a frozen Task 3 workflow test during team QA).

## C. Security

AUD_01 = CONFIRMED, FIXED (PR #58). AUD_04 = CONFIRMED, FIXED (PR #58). SCRIPT_INJECTION = none remaining in any backend or frontend `run:` block (re-scan). SECRET_COMMAND_EXPOSURE = none remaining. GITHUB_TOKEN_PERMISSIONS = top-level `contents: read` on every workflow (backend 13/13, frontend 4/4). ACTION_PINNING = all third-party actions pinned to upstream-resolved commit SHAs (table in `WORKFLOW_SECURITY_REMEDIATION_2026-09-13.md`). WORKFLOW_TRIGGER_RISK = LOW (backend repository is public; fork PRs get read-only tokens and no secrets; no pull_request_target; privileged workflows are dispatch/call/main-push only behind reviewer-gated environments). SECRET_SCAN = CLEAN (both repositories, built bundle). BRANCH_PROTECTION = none on backend main (re-verified); unavailable to read on the frontend (private, Free plan). BRANCH_PROTECTION_HUMAN_ACTION_REQUIRED = YES.

## D. DevOps

AUD_05 = CONFIRMED; deployment token retained by design (no OIDC path for SWA content upload on SKU Free / SwaCli), environment-recorded, rotation documented (PR #59 doc, PR #43). AUD_06 = CONFIRMED, FIXED (PR #58, PR #43). AUD_07 = CONFIRMED, FIXED at workflow level (PR #59, PR #43); current DEV backend image and DEV SWA content are declared MANUAL and unattributed until the next workflow deployment. BACKEND_OIDC = vars-based federated identities per environment (dev 350a6a54…, prod 1f8294fb…, prod migration 7cb6eb84…); federated-credential subjects not readable by the builder identity (human confirmation listed). SWA_AUTH_MODE = deployment token. SWA_TOKEN_DECISION = KEEP, scope to deploy jobs, rotate via `az staticwebapp secrets reset-api-key`. DEV_ENVIRONMENT_PROTECTION = backend: reviewer "DocuAction" required; frontend: `development` environment now declared, reviewers unavailable on plan. MIGRATION_CONTROL = head-only targets; `expected_current` exact-matched against real revision ids; secrets via env; PROD job dispatch-only. BACKEND_PROVENANCE_CHAIN = SHA -> container-release run -> `--build-arg GIT_SHA` -> tag = short SHA -> digest -> deploy-dev (environment) -> `/health.git_sha` verified by the deploy gate. FRONTEND_PROVENANCE_CHAIN = SHA -> deploy-frontend run -> `out/build-info.json` -> SWA upload (environment) -> served `/build-info.json` verified.

## E. Dependencies / reporting

AUD_08 = CONFIRMED, FIXED (frontend PR #41). NEXT_VERSION = 16.2.12 -> 16.3.5. SHARP_VERSION = 0.35.3 -> 0.35.4 (transitive via next, controlled by `overrides`). WEASYPRINT_VERSION = 70.0 in PR #61 (69.0 on main); Linux render job dependency set: pydyf 0.12.1, fonttools 4.65.0, tinycss2 1.5.1, cssselect2 0.10.1, Pillow 12.3.0, cffi 2.1.1, Pyphen 0.18.1. DEPENDENCY_CRITICAL = 0 after PR #41 (was 1). DEPENDENCY_HIGH = 0 after PR #41 (was 1). DEPENDENCY_MEDIUM = 1 remaining (ecdsa 0.19.2 PYSEC-2026-1325 via python-jose, no upstream fix; AUD-13 documented) plus weasyprint 69 on main until PR #61 merges. DOCX = PASS (regenerated from the Lane C tree: core title, author, en-US, 7 tables with repeating headers, TOC field, page field, DEVELOPMENT / TEST label, entities, contract). PDF = rendered in CI on PR #61 (15,148-byte probe: header, structure tree, marked tagged, language, document title, outline, declares pdf/ua-1; 71 PDF-gated tests passed); not rendered on the builder host. HTML = PASS (one h1 after AUD-14; lang, 14 h2, 8 captioned tables, print CSS, no theme hook). CSV = PASS (metadata header, 19 rows, BOM in package). ZIP = PASS (docx, html, csv, README, manifest; PDF omitted with recorded reason on Windows). REPORT_RECONCILIATION = PASS (report id, contract, task, deliverable, period, status, entities, four categories, payload hash, classification identical across formats; repository test passes). REPORT_TEMPLATE = PASS (cover, document control, contents, ten sections, accessibility, provenance; prepared for HHS, prepared by Alliance Global Tech Inc.). REPORT_ACCESSIBILITY = DOCX/HTML structure verified; PDF tagged structure present in CI; no PDF/UA or Section 508 certification claimed. GOVERNMENT_BRANDING_OFF = YES (no image, no mark element).

## F. Entity Intelligence (PR #60, base feat/entity-intelligence-foundation 4ef1bba, head 5cdb20b)

AUD_09 = CONFIRMED, FIXED (true duplicates collapse by source, subject, type, role, normalized value, period, record identity; distinct identifiers still count as candidates; 11 tests). AUD_10 = CONFIRMED (broader than reported: int state, non-US formats, full-width digits), FIXED (`as_text` coercion, `postal_comparison` with explicit zip_status incl. AMBIGUOUS_LEADING_ZERO; raw value preserved; addr-norm-1.2; 38 tests; `test_never_raises_on_garbage` now asserts). AUD_11 = CONFIRMED, FIXED (new `LocationSignal.SOURCE_ROLE_UNKNOWN`, non-corroborating; 5 role cases incl. historical role change). FEATURE_OFF = YES. PLATFORM_INTERNAL = YES. DUPLICATE_OBSERVATION = FIXED. POSTAL_NORMALIZATION = FIXED. UNKNOWN_SOURCE_ROLE = FIXED. SAME_ADDRESS_DIFFERENT_ROLE = ROLE_ASSIGNMENT_DIFFERS (unchanged, tested). HUMAN_DECISION_BOUNDARY = preserved (closed assessment vocabulary; service writes no determination; invariant tests). CORE_TEFCA_BOUNDARY = PASS (`tests/test_core_boundary.py`). GOVERNING_INVARIANTS_TOTAL = 37. INVARIANTS_TESTED = 36. INVARIANTS_PASS = 36. INVARIANTS_FAIL = 0. INVARIANTS_MISSING_TEST_COVERAGE = 7 (I-3, I-12, I-15, I-16, I-21, I-27, I-30; down from 11: I-31, I-33, I-34, I-36 now have committed tests, invariant 13 and 14 behaviour tests added). Scale (synthetic, `scripts/ei_perf.py`): 2K = 6.8 s (293 ent/s), 5K = 14.4 s (348), 10K = 21.9 s (457), 25K = 44.3 s (564), 50K = 102.1 s on the quiet re-run (490); comparisons 3 per entity, assessments 1 per entity, duplicates collapsed 0, tracemalloc peak 12.9 / 32.2 / 64.4 / 161.2 / 322.5 MB (linear, about 6.5 MB per 1K entities); no super-linear growth. PEAK_MEMORY = 322.5 MB tracemalloc at 50K (RSS not measurable by the harness on this host). Synthetic domain-processing performance only. External acquisition performance is not represented. 25,000 source records != 25,000 human reviews. GATE_H_AUTHORIZED = NO.

## G. UI / accessibility (frontend PR #42, base feat/global-theme-a11y-lms edff7dc, head d4e29f1)

AUD_12 = PARTIALLY_CONFIRMED: inline colour literals reproduced and 674 replaced with semantic tokens across 30 pages plus AppShell/UsersAdmin; 16 literals in hues without a token remain, allowlisted per file and listed; guardrail now fails on new literals. Runtime part BLOCKED. TOTAL_ROUTES = 79 (12 public, 67 authenticated). LIGHT_VERIFIED = 1 rendered (/login, prior run) + source-level for the rest. DARK_VERIFIED = same. DESKTOP / TABLET / MOBILE / ZOOM_200 / ZOOM_400 / KEYBOARD / FOCUS = BLOCKED (no credentialed DEV session available to automation; Chrome extension unresponsive; no workaround attempted, no credentials requested). AUTOMATED_ACCESSIBILITY = login only (prior axe run). MANUAL_ACCESSIBILITY = NOT_TESTED. VISUAL_RUNTIME_VERIFICATION_REQUIRED = YES. Per-route BLOCKED rows (ROUTE, TEST, BLOCKED_REASON, ACCESS_REQUIRED, REMAINING_VERIFICATION) are in `frontend/docs/DEV_ACCESSIBILITY_VERIFICATION_EVIDENCE_2026-09-13.md` on PR #42. Not stated: VISUAL_VERIFICATION_COMPLETE, MERGE_ELIGIBLE, ACCESSIBILITY_COMPLETE.

## H. Learning Center

MODULES = 16. LMS_VERSION = 1.2.0 (unchanged). PM_PATH / ANALYST_PATH / QA_PATH / ADMIN_PATH / VIEWER_PATH = present in content (labels Program Manager, BA/Analyst, QA lead, Administrator, Everyone) SOURCE only. VISUAL_EXPERIENCE = module cards, stepper, progress, pipeline diagram (text-first figure + ordered list with labelled human-decision boundary), callouts, role badges, authority tags: SOURCE only (build compiles, guardrails 76/76). SEARCH = labelled, live status region. NAVIGATION = previous/next `nav`, related training. DIAGRAMS = accessible HTML/CSS, list semantics restored (explicit list/listitem roles on the `display: contents` pipeline). KNOWLEDGE_CHECKS = present (`aria-pressed`, `role=status`). LIGHT_MODE / DARK_MODE = SOURCE only. MOBILE = NOT_TESTED. ACCESSIBILITY = SOURCE (guardrails). CONTENT_TRUTH = unchanged, PASS (no SLA, D2-accepted, synthetic-as-Government, NPI-as-credential, CMS-as-TEFCA, or KYP-as-adopted teaching). TRACEABILITY = presentation-only change; LMS_CONTENT_CHANGE_REQUIRED = NO.

## I. Source governance

NPPES_RIGHTS = ASSUMED_PUBLIC_DOMAIN, PENDING_HUMAN_REVIEW. PPEF_RIGHTS = public CMS data, DOCUMENTED, PENDING_HUMAN_REVIEW, NOT AUTHORIZED FOR INTEGRATION. IQVIA_RIGHTS = TERMS_REVIEW_REQUIRED / AWAITING_TERMS. GOOGLE_RIGHTS = RESEARCH_ONLY, no key, no call, no persistence. STATE_RIGHTS = DESIGN_ONLY per jurisdiction, no records marked reviewed. HUMAN_RIGHTS_REVIEWS_COMPLETED_BY_BUILDER = 0. IQVIA_SCHEMA = AWAITING_SCHEMA. IQVIA_TERMS = AWAITING_TERMS. IQVIA_INTERPRETATION_USED = NO. GOOGLE_OPERATIONAL = NO. LIVE_STATE_ADAPTERS = 0. KYP = not adopted; unchanged.

## J. Pull requests

| Lane | PR | Branch | Base SHA | Head SHA | Files | Ready for re-audit |
|---|---|---|---|---|---|---|
| A | backend #58 | fix/security-workflow-governance | 75383cf (main) | 2f74dee | 17 | YES |
| B | backend #59 | fix/deployment-provenance | 75383cf (main) | 78fd28c | 6 | YES |
| C (backend) | backend #61 (supersedes #56) | fix/report-security-c | c405031 + main 75383cf | f301931 | 4 (+ weasyprint bump) | YES |
| C (frontend) | frontend #41 | fix/deps-next-sharp | 0f99578 (main) | 3e28d42 | 3 | YES |
| D | backend #60 | fix/ei-correctness | 4ef1bba (feat/entity-intelligence-foundation) | 5cdb20b | 10 | YES |
| E | frontend #42 | fix/ui-a11y-lms-remediation | edff7dc (feat/global-theme-a11y-lms) | d4e29f1 | 35 | YES (non-runtime part) |
| A/B (frontend) | frontend #43 | fix/workflow-hardening | 0f99578 (main) | 5245842 | 5 | YES |
| audit docs | backend #57 | audit/independent-ai-audit-2026-09-13 | 75383cf | this commit | docs only | n/a |

CI at hand-off: #59 all checks green; #61 green except one pending job; #58 re-running after a workflow-structure test was updated to assert env injection (local: 57 passed); #60 dependency-review green (base branch has no other required workflows); frontend #41/#42/#43: `audit` green, CodeQL and Dependency Review fail as they do on PR #40 and on main ("Code scanning is not enabled" / "Dependency review is not supported": private repository on the Free plan, pre-existing, not caused by these PRs).

## K. Human actions required

1. Enable branch protection / a ruleset on backend `main` (public repository, available on the current plan): PR required, 1 independent approval, latest-push approval, code-owner review, required checks (Backend Tests, CodeQL, Dependency Review, Security Scan, Linux PDF Rendering for `app/reports/**`), conversation resolution, no force push, no deletion, administrators included (AUD-02).
2. Confirm that public visibility of the backend repository is intended.
3. Run the independent checker session against the head SHAs in section J (AUD-03); this report is builder output.
4. Human PR approval decisions for #58, #59, #60, #61, frontend #41, #42, #43; close #56 if #61 is accepted; decide when to merge main into `feat/entity-intelligence-foundation` (PR #54) so PR #60 can follow.
5. Source Rights human review for NPPES and PPEF records; IQVIA terms; Google and state-registry authorization remain closed.
6. Approve or cancel the pending DEV release run 34743956579; rotate SWA deployment tokens; confirm Azure federated-credential subjects; decide on GitHub Pro/Team for the private frontend repository (environment reviewers, code scanning, dependency review).
7. Credentialed visual and accessibility verification of the 67 authenticated routes in both themes before any UI merge.
8. Future Gate H authorization (after PR #60 and the rights review). Not requested here.

## L. Next control

TEAM_TASK_1_6_QA_CAN_CONTINUE = YES (no lane changes the QA baseline; DEV untouched). NEW_FEATURE_DEVELOPMENT_ALLOWED = NO. GATE_H_ALLOWED = NO. MERGE_AUTHORIZED = NO. PROD_AUTHORIZED = NO. READY_FOR_INDEPENDENT_REAUDIT = YES.

## M. Final status

TOP_REMEDIATIONS_COMPLETED = (1) no workflow input or secret reaches a shell unvalidated, least-privilege tokens, SHA-pinned actions; (2) commit-attributable images and deployments with a deploy-time provenance gate; (3) frontend critical/high advisories cleared; (4) Entity Intelligence duplicate, postal and unknown-role defects fixed with 596 EI tests and linear scale to 50K; (5) single-h1 deliverables on WeasyPrint 70 with reconciliation intact. TOP_REMAINING_BLOCKERS = branch protection (human), independent checker (human), visual runtime verification (credentialed), PR #54 merge of main, ecdsa without upstream fix. TOP_HUMAN_DECISIONS = protection and visibility of the backend repository, PR approvals, rights reviews, plan upgrade for the frontend repository. STATUS = BUILDER_FIX_COMPLETE, BUILDER_TESTS_PASS, READY_FOR_INDEPENDENT_REAUDIT; INDEPENDENT_AUDIT_PASS not claimed.

## N. Finding classification table (independent checker input)

| FINDING_ID | ORIGINAL_SEVERITY | LANE | REPRODUCED | CLASSIFICATION | REPRODUCTION_EVIDENCE | FIXED | PR | HEAD_SHA | TEST_EVIDENCE | REMAINING_LIMITATION | REQUIRES_REAUDIT | HUMAN_ACTION |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AUD-01 | HIGH | A | yes | CONFIRMED | 8 `inputs.*` expansions inside run blocks (prod-migration:67, migration-preflight:119/120/123/245, dev-release:239/240, container-release:254, deploy-backend:310/313/500/501) | yes | #58 | 2f74dee | YAML parse; run-block re-scan = 0 risky expressions; test_release_pipeline 57 passed | validation executes on next real dispatch | YES | none |
| AUD-02 | HIGH | A | yes | CONFIRMED | protection API 404, rulesets [] | no (governance) | #58 doc + CODEOWNERS text | 2f74dee | n/a | protection must be configured by an admin | YES (re-read API) | enable protection |
| AUD-03 | MEDIUM | process | n/a | BLOCKED_FROM_VERIFICATION | same session built and audited | no | n/a | n/a | n/a | needs a different session | YES | run checker |
| AUD-04 | MEDIUM | A | yes | CONFIRMED | secrets on argv at deploy-backend:351/371/372, stackhawk:42/74 | yes | #58 | 2f74dee | test_release_pipeline asserts env injection | MIGRATION_DATABASE_URL still exists as a secret | YES | retire the secret |
| AUD-05 | MEDIUM | B | yes | CONFIRMED | SWA tokens, deploy-dev without environment | partly (environment, docs); token kept by design | #59 doc, frontend #43 | 78fd28c / 5245842 | workflow parse | no OIDC alternative; reviewers unavailable on plan | YES | rotate tokens; plan decision |
| AUD-06 | MEDIUM | A/B | yes | CONFIRMED | 5 backend + 3 frontend workflows without top-level permissions; 11 unpinned actions | yes | #58, frontend #43 | 2f74dee / 5245842 | workflow parse; pin table | pins need periodic refresh | YES | none |
| AUD-07 | MEDIUM | B | yes | CONFIRMED | manual ACR build, cancelled runs, pending run, no SHA in /health | yes (workflow + code) | #59, frontend #43 | 78fd28c / 5245842 | test_health_provenance 3 passed; full suite 2818/381/0 | current DEV artifacts remain manual until next workflow deploy | YES | approve/cancel run 34743956579 |
| AUD-08 | MEDIUM | C | yes | CONFIRMED | npm audit: next critical, sharp high | yes | frontend #41 | 3e28d42 | npm audit 0/0; build 81 pages; guardrails pass; bundle grep clean | none | YES | none |
| AUD-09 | MEDIUM | D | yes | CONFIRMED | duplicate identical observations -> MULTIPLE_CANDIDATE_ENTITIES | yes | #60 | 5cdb20b | 11 tests; EI 596 passed; full 3408/381/0 | one NPI + identical entity type from two records = one candidate by design | YES | none |
| AUD-10 | MEDIUM | D | yes | CONFIRMED (broader) | int postal -> TypeError; int state -> AttributeError; non-US -> bogus key | yes | #60 | 5cdb20b | 38 tests | short ZIPs yield no key (safer) | YES | none |
| AUD-11 | MEDIUM | D | yes | CONFIRMED | unknown role -> NORMALIZED_LOCATION_MATCH | yes | #60 | 5cdb20b | 5 tests | none | YES | none |
| AUD-12 | MEDIUM | E | partly | PARTIALLY_CONFIRMED | 674 inline literals; 78 routes source-only | non-runtime part yes | frontend #42 | d4e29f1 | build 81 pages; guardrails 76/76 | runtime BLOCKED for 67 routes; 16 residual literals | YES | credentialed visual verification |
| AUD-13 | LOW | C | yes | CONFIRMED | pip-audit ecdsa PYSEC-2026-1325, weasyprint 69 | weasyprint yes (#61); ecdsa no fix | #61 | f301931 | CI render 71 passed | ecdsa has no upstream fix | YES | consider replacing python-jose |
| AUD-14 | LOW | C | yes | CONFIRMED | 2 h1 in generated HTML | yes | #61 | f301931 | test_report_heading_structure; reconciliation 31 passed; full 2816/381/0 | none | YES | none |
| AUD-15 | LOW | A | yes | CONFIRMED | /api/config and /api/tefca/status 200 unauthenticated | no (accepted, rationale in doc) | #58 doc | 2f74dee | n/a | connector label wording | NO | product wording decision |
| AUD-16 | LOW | A | yes | CONFIRMED | wrong workflow_call comment; `\|\| true` scans | yes | #58 | 2f74dee | workflow parse; test_release_pipeline | none | YES | none |
| AUD-17 | INFO | D | yes | CONFIRMED | main 3196 collected vs builder claim | documented; trial merge of main into PR #60 clean | #60 | 5cdb20b | merge-tree clean | PR #54 still lacks PR #55 | NO | merge main into PR #54 |
| AUD-18 | INFO | B | yes | CONFIRMED | "" / "maybe" -> ValidationError | documented | #59 | 78fd28c | n/a | none | NO | none |
| AUD-19 | INFO | gov | yes | CONFIRMED | no AGENTS.md / CLAUDE.md | no (policy created in #57; AGENTS.md is a human decision) | #57 | 3698e6b+ | n/a | none | NO | decide on AGENTS.md |
| AUD-20 | INFO | D | yes | CONFIRMED | 11 invariants without committed tests | yes for EI-testable ones (I-31, I-33, I-34, I-36, inv. 13, inv. 14) | #60 | 5cdb20b | 6 tests | 7 invariants remain outside EI | YES | none |
| AUD-21 | INFO | tests | yes | CONFIRMED | test asserts >= 43 legacy rows; fails on empty DB | no (frozen Task 3 test during QA) | n/a | n/a | ephemeral-DB run: 1 failed / 202 passed | measures shared-QA state | NO | decide after QA closure |

## O. Independent checker hand-off (per PR)

Common: SECURITY_REVIEW_REQUIRED = YES for #58, #59, #43; VISUAL_REVIEW_REQUIRED = YES for #42; RUNTIME_VERIFICATION_REQUIRED = YES for #59 (first workflow-built deployment exercises the gate), #43 (first workflow deployment serves build-info.json), #42 (67 authenticated routes). Test commands use `SECRET_KEY` = 64 characters, `DATABASE_URL` = unreachable placeholder, `-p no:cacheprovider`.

- **#58** (2f74dee, base 75383cf): AUD-01/04/06/16 + AUD-02/15 documentation. `python -m pytest tests/test_release_pipeline.py tests/test_data_provenance.py -q` -> 57 passed; `python -c "import yaml,glob;[yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"`; CI: Backend Tests re-run pending at hand-off (previous run: 1 failed on the updated test, 2816 passed).
- **#59** (78fd28c, base 75383cf): AUD-07 + AUD-05/18 docs. `python -m pytest tests/test_health_provenance.py tests/test_health.py tests/test_security_headers.py -q` -> 11 passed; full `python -m pytest tests/ -q` -> 2818 passed / 381 skipped / 0 failed; CI all green.
- **#61** (f301931, base main; includes #56): AUD-13 (weasyprint) + AUD-14. `python -m pytest tests/test_report_heading_structure.py tests/test_sow_report_generation.py tests/test_report_cross_format_reconciliation.py -q` -> 31 passed; full suite 2816 / 381 / 0; CI Linux PDF Rendering green (tagged, language, title, outline, pdf/ua-1 declared; 71 PDF-gated tests).
- **#60** (5cdb20b, base 4ef1bba): AUD-09/10/11/20. `python -m pytest tests/test_entity_intelligence_*.py tests/test_core_boundary.py -q` -> 596 passed; full suite 3408 / 381 / 0; `python scripts/ei_perf.py --sizes 2000 5000 10000 25000 50000`; `git merge-tree origin/main HEAD` clean.
- **frontend #41** (3e28d42, base 0f99578): AUD-08. `npm ci && NEXT_PUBLIC_API_URL=https://docuaction-dev.azurewebsites.net npm run build && node scripts/ui-guardrails.mjs && npm audit --omit=dev` -> 81 pages, guardrails pass, 0 vulnerabilities.
- **frontend #42** (d4e29f1, base edff7dc): AUD-12 non-runtime. Same build command; `npm run test:ui` -> 76 checks pass; evidence doc lists 67 BLOCKED routes.
- **frontend #43** (5245842, base 0f99578): AUD-05/06/07 frontend. Workflow parse; provenance file and served-file check execute on the next dispatch.

The classification table in section N is the complete reconciliation; the checker should not reconstruct state from prose.
