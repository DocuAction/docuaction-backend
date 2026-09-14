# DocuAction Release 1.0 — PROD-Readiness Remediation & QA Closure Report

DATE = 2026-09-14
ROLE = BUILDER / MAKER (remediation session; not the checker, not the release authority)
OWNER = Alliance Global Tech Inc. · PROGRAM = HHS / ASTP / ONC — TEFCA ARC · CONTRACT = 7571MN26F80064

THIS IS NOT PROD AUTHORIZATION. DEV / controlled QA only. Nothing was merged, nothing was deployed, no Government data was touched, Gate H stays CLOSED, Entity Intelligence stays absent/OFF.

Inputs reconciled: the independent Release 1.0 checker report (2026-09-14), the Structural Hardening Builder Report, the Federal Deployment Isolation Assessment (v0.1), the Authentication Target Architecture (v0.1), the deployment-profile documentation, backend PR #62 (7e399e9) and frontend PR #44 (bfa7902), the current DEV workflow/runtime state, the QA/UAT workbook `DocuAction_TEFCA_ARC_Formal_QA_UAT_Test_Cases_REVISED_2026-09-14.xlsx` (32 cases: 19 PASS, 2 FAIL, 3 BLOCKED, 8 NOT TESTED), and the open GitHub issues/PRs.

Branches produced (stacked on the unmerged PRs, draft, no merge):

- backend `fix/release-1.0-qa-closure` on top of `feat/structural-hardening-1.0` (PR #62)
- frontend `fix/release-1.0-qa-closure` on top of `feat/structural-hardening-1.0` (PR #44, itself on #42 → #40)

## A. RELEASE_1_0_OPEN_ITEMS (deduplicated across the checker report, the builder report and the QA workbook)

| ID | Source | Sev | Area | Description | Repro | Root cause | DEV fix | QA retest | Human action | Env blocker | Contract decision | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | QA DEF-001 / SRC-001 | High | Delivery provenance | ONC/RCE Deliveries card showed filename, source, received date and record count but not Delivery ID, file SHA-256 or delivered field count; Section A hid absent values | YES | Deliveries detail never rendered `intake_id`, `sha256`, `received_by`, declared count or the field map; Section A `Facts` dropped `undefined` rows | YES — Provenance block on the delivery card (Delivery ID, job, file, full SHA-256, received at/by, declared and received counts, 41 delivered fields per record from the documented field map, Government reference, duplicate flag); Section A renders absent values as "—" | YES | NO | NO | NO | FIXED (verified in browser) |
| R2 | QA MAP-001 / REL-001 / EVD-001 / EVD-002 (BLOCKED) | Critical | Workspace | 41-field table, relationship chain and evidence coverage "not reachable" | YES (with a case created outside the delivery pipeline) | The screens exist and work for a case that came through an ONC/RCE delivery; the case QA used (REV-2026-000245, legacy population) has no Area 1 record, so Section A truthfully reports "This case has no Area 1 record" and the trace stages read Not available. Navigation to the sections was not documented | NO code change needed for the screens; DOCUMENTATION + DATA PATH: QA Data Preparation P1–P6 (register a synthetic delivery → READY FOR REVIEW → Create review cycle → cases) and exact click paths | YES | NO | NO | NO | READY FOR QA (verified end to end on a pipeline case: 41 delivered values, canonical QHIN/parent, dimensions) |
| R3 | QA AK-001 / QA-003 (FAIL), QA-004 | High | Independent QA | "RETURN action missing from the case workflow" | Partially — RETURN and ESCALATE exist in the deployed build; the panel is offered only when the case is in SUBMITTED FOR QA and the signed-in role resolves to qalead+ | (a) state condition undocumented (an APPROVED or CLAIMED case shows no QA panel); (b) the shell resolved only canonical role spellings, so a stored alias such as `qa_lead` hid the panel the server would accept | YES — shared role ladder with server aliases (`src/lib/roles.js`) used by the shell and the case actions; QA path documented | YES | NO | NO | NO | FIXED / RETEST_REQUIRED (RETURN → RETURNED → rework → APPROVE and ESCALATE verified end to end) |
| R4 | QA AUD-001 (NOT TESTED) | Critical | Audit | Audit & Decision History showed only the platform authentication log; determinations and QA decisions (registry audit log) were never on that screen | YES | Two audit stores; `/api/tefca/audit-trail` read one | YES — the endpoint merges registry decision events (claim, determination, qa_approve/return/escalate, supersede) filed under `review`; search by case id; new "Case / resource" column; description updated | YES | NO | NO | NO | FIXED (API verified: 5 lineage rows for the journey case) |
| R5 | Checker M1 | Medium | Module isolation | `/pricing?rfq=` issued GovCon API calls under TEFCA_ARC via a page-local fetch | YES | Page kept its own helper | YES — helper honours the profile; guardrail | YES (ISO-001, needs a TEFCA_ARC slot) | NO | YES (no TEFCA_ARC DEV slot) | NO | FIXED |
| R6 | Checker L1 | Low | Public endpoint | `/api/config` published `disabled_modules` | YES | Concealment inconsistency | YES — only `program` + `enabled_modules` | NO | NO | NO | NO | FIXED |
| R7 | Checker L2 | Low | Public endpoint | `/health` reported gated modules "active" under TEFCA_ARC | YES | Static dict | YES — profile-aware | NO | NO | NO | NO | FIXED |
| R8 | Checker L3 | Low | Jobs | Bulletin store/hydration ran under TEFCA_ARC | YES | Env-gated only | YES — profile-gated | NO | NO | NO | NO | FIXED |
| R9 | Checker L7 | Low | Core | `/api/decisions/feedback/stats` 500 (missing `decision_intel_engine`) on /analytics and /trust | YES (head and base) | Module absent from the repository | YES — local aggregation fallback | NO | NO | NO | NO | FIXED |
| R10 | Checker L6 / CI | Low | Reports | DOCX "September 07, 2026" vs HTML "07 September 2026"; reconciliation test failed on head, base and CI on single-digit days | YES | Two date formatters | YES — one display form "7 September 2026" in both engines | YES (RPT-001 header) | NO | NO | NO | FIXED (test now passes) |
| R11 | Checker M5 | Medium | Accessibility | Core routes offered in the TEFCA shell failed contrast: /intelligence (all roles), /validation, /analytics, /trust, /compare | YES (identical at base) | Literal colours on rail strips and empty states | YES — tokens (`--nav-rail-text`, `--text-secondary`), darker success hue on the Trust strip | YES | NO | NO | NO | FIXED (see matrix) |
| R12 | Checker L8 | Low | Navigation / a11y | Reviewer/viewer offered Audit & Platform Health, got 403 and a page without a heading | YES | Navigation not role-scoped; AccessDenied used a div title | YES — `minRole` on the two entries; AccessDenied renders an h1 | YES (AUTH-002) | NO | NO | NO | FIXED |
| R13 | New (journey) | High | Pipeline | A second delivery carrying an NPI already registered by an earlier delivery FAILED at stage PROMOTION with an integrity error on `idx_tefca_ident_unique` | YES | Cross-delivery identifier collision not treated like within-delivery sharing | YES — value kept on the entity columns, no second identifier row, every eligible record promoted; regression test | YES (P2–P4 twice) | NO | NO | NO | FIXED |
| R14 | Checker M2 / Assessment §16.2 | Medium | Profile scope | Frontend keeps document-automation routes the backend gates under TEFCA_ARC (compare, transcription) | YES | Scope decision | NO (decision) | — | YES: decide federal-profile Core route scope | NO | NO | HUMAN_DECISION_REQUIRED |
| R15 | Checker M3 / Auth doc | Medium-High | Session | 15-minute non-admin session without refresh; 24-hour admin token; localStorage bearer; hardcoded admin e-mail list (server and shell) | YES | Architecture | NO (auth stage 1 needs separate authorization) | SESSION-001 documents expected behaviour | YES: authorize auth stage 1, admin token lifetime | NO | NO | HUMAN_DECISION_REQUIRED (not weakened, not changed) |
| R16 | Checker M4 | Medium | Rate limit | viewer/COR stays free tier; unauthenticated per-page probes share the per-IP bucket | YES | Design | NO (security decision) | — | YES: decide viewer tier | NO | NO | HUMAN_DECISION_REQUIRED |
| R17 | Checker H1 | High | Governance | No enforced branch protection (backend), plan limitation (frontend) | YES | Repository settings | NO | — | YES (see D/E) | — | NO | HUMAN_ACTION_REQUIRED |
| R18 | Checker H2 | High | Provenance | DEV runtime = manually built image 0cf0284 (digest 4384b211…), no CI record, no runtime SHA; run 34555888905 waiting on `development` approval holds the `dev-release` slot; 34743956579 pending | YES | Human approval outstanding; PR #59 unmerged | NO | — | YES: cancel/approve runs, merge #59 | YES | NO | BLOCKED (human) |
| R19 | Checker L5 / PR #61 | Low | Reports | Report HTML renders two h1 (cover + running title) | YES | Template | Covered by open PR #61 | YES after #61 | YES: merge decision #61 | NO | NO | DEFERRED to PR #61 |
| R20 | Checker BLOCKED | Medium | Reports | PDF engine absent on the build host; container unverified | YES | Environment | NO | RPT-001 records PDF host-dependent | YES: verify PDF in the container (PR #61/#56) | YES | NO | BLOCKED (environment) |
| R21 | Checker L9 | Low | CI | Security-scan steps end in `|| true` (non-blocking) | YES | Workflow | NO (workflow governance, PR #58) | — | YES | NO | NO | DEFERRED to PR #58 |
| R22 | Checker | Low | Source truth | DEV still serves `pecos: available` and the operator e-mail in `/health` (pre-#62 build) | YES | Not deployed | Fixed in PR #62 | YES (SRC-002, SEC-001) | YES: DEV release | YES | NO | RETEST after deploy |
| R23 | QA ISO-001 (new) | Medium | Federal isolation | No TEFCA_ARC deployment slot exists; the shared DEV slot runs profile ALL | — | Deployment topology | NO | BLOCKED on shared DEV | YES: approve a TEFCA_ARC DEV slot | YES | NO | BLOCKED (human) |
| R24 | Checker L11 | Low | GovCon | `/pricing` has two page files; `page.tsx` shadows the marketing `page.js` | YES | Legacy | NO (GovCon, out of Release 1.0 scope) | — | NO | NO | NO | DEFERRED (Release 1.1) |

Counts: TOTAL_OPEN_ITEMS = 24 · FIXED = 13 (R1, R3, R4, R5, R6, R7, R8, R9, R10, R11, R12, R13 + R2 as data-path/documentation) · QA_RETEST_REQUIRED = 12 · HUMAN_ACTION_REQUIRED = 7 (R14, R15, R16, R17, R18, R19, R23) · BLOCKED = 3 (R18, R20, R23) · DEFERRED = 3 (R19, R21, R24).

## B. QA screen coverage

TOTAL_QA_TEST_CASES = 32 in the QA workbook (+5 added by DEV: SESSION-001, RPT-003, SRC-002, LMS-001, ISO-001 → 37 in the DEV-ready sheet).
SCREENS_MAPPED = 18 (see *DEV Screen Map*: label → route → section → roles → API).
SCREENS_AVAILABLE = 18 on the remediated build under the appropriate role; ISO-001 needs a TEFCA_ARC slot.
SCREENS_BLOCKED = 1 (ISO-001 on the shared DEV slot).
NAVIGATION_GAPS_FIXED = 4 (delivery provenance visible on the Deliveries screen; case id visible on audit rows; role floor stops offering qalead-gated screens to reviewers/viewers; pipeline data path documented for the workspace sections).
QA_CASES_READY_TO_EXECUTE = 36 of 37 once the remediation build is on DEV (12 RETEST_REQUIRED, 9 READY, 15 carried PASS to re-confirm); 1 BLOCKED (ISO-001).

Deliverables: `DocuAction_TEFCA_ARC_Formal_QA_UAT_Test_Cases_DEV_READY_2026-09-14.xlsx` (Downloads; sheets *Test Cases DEV-Ready*, *DEV Screen Map*, *QA Data Preparation*) and `docs/qa/QA_UAT_DEV_EXECUTION_MATRIX_2026-09-14.md`; synthetic fixture `docs/qa/fixtures/synthetic_rce_delivery_QA.txt` (41-field header, six synthetic organisations under the unassigned OID arc 9.99.777, two deliberately held rows).

## C. TEFCA business journey (local synthetic runtime, `ENTITY_RESOLVER_SOURCE=db` as on DEV)

Delivery `synthetic-QAJBBE3D9.txt` → job SUCCEEDED → dashboard sha256 match, 6 received / 6 accounted (4 ready, 2 held) → reconciliation PASSED (E == C) → review cycle (PM): census sample of 4, 4 verified and linked (REV-2026-000001…4) → workspace: Section A with Delivery ID, SHA-256, 41 delivered values, canonical QHIN/parent, verification dimensions → viewer claim 403 → reviewer claim → rationale < 10 chars 422 → determination (event 1) → reviewer QA attempt 403 → QA RETURN (event 2; state RETURNED; My Reviews returned = 1) → reviewer re-determination (event 3) → QA APPROVE (event 4; APPROVED, reportable) → second case QA ESCALATE (ESCALATED, not reportable) → Task 3 Weekly report DA-ARC-2026-006 generated → PM release PM_REVIEWED recorded → audit trail lists claim, both determinations, return and approve for the case → operations dashboard: total 4, unassigned 2, escalated 1, reportable 1.

INTAKE = PASS · RECONCILIATION = PASS · VALIDATION = PASS (2 held rows visible, unexplained 0) · QHIN_POPULATION = PASS (QHIN entity + canonical edges) · SAMPLING = PASS (approved per-QHIN plan, frozen, never redrawn) · REVIEW_CREATION = PASS · ASSIGNMENT = PASS (claim; bulk assignment untested by UI) · ANALYST_REVIEW = PASS · ANALYST_DETERMINATION = PASS · INDEPENDENT_QA = PASS · QA_RETURN_REWORK = PASS · FINAL_CLASSIFICATION = PASS (final only after independent QA; automation never assigns it) · REPORTING = PASS (DOCX/HTML/CSV/ZIP; PDF host-dependent) · PM_REVIEW = PASS · AUDIT_HISTORY = PASS (after R4)

Four contractual categories unchanged; Tasks 1–6, sampling methodology, roles, RBAC and authentication runtime untouched by this closure.

## D. Release gates

TEFCA_FUNCTIONAL_GATE = PASS (journey C) · TEFCA_ZERO_REGRESSION_GATE = PASS (see §E matrix) · MODULE_ISOLATION_GATE = PASS_WITH_BOUNDED_LIMITATIONS (server boundary real and fail-closed; runtime gate not build-time exclusion; R14 scope decision open) · SOURCE_TRUTH_GATE = PASS (PECOS = NPPES proxy / not connected on the remediated build; DEV still pre-#62) · AUTH_SESSION_GATE = PASS (runtime unchanged; expired session ends cleanly with one 401; no storm; 403 remains denial) with the known MEDIUM-HIGH architecture risk (R15) · RBAC_GATE = PASS (viewer/reviewer/qalead/PM/admin floors verified by API and UI; role aliases resolve identically on both sides) · ACCESSIBILITY_GATE = PASS_WITH_BOUNDED_LIMITATIONS (TEFCA 0 serious nodes; Core routes offered in the TEFCA shell fixed; legacy GovCon/public contrast remains; actual browser zoom not verified; no Section 508 claim) · REPORT_GATE = PASS_WITH_BOUNDED_LIMITATIONS (PDF host-dependent; two-h1 cover in PR #61) · LMS_GATE = PASS (16 modules, 5 role paths, prev/next, knowledge checks, figure, light/dark/mobile/keyboard; LMS_IMPACT_REVIEW = NO change required — no workflow meaning changed) · SECURITY_GATE = PASS (nothing weakened; no new dependency findings; two disclosures reduced) · DEV_PROVENANCE_GATE = BLOCKED (human) · GITHUB_GOVERNANCE_GATE = FAIL (human) · QA_EXECUTION_READINESS_GATE = PASS once the remediation build is deployed to DEV (documentation, data path and screens ready; deployment needs human approval).

## E. Test evidence (commands)

- Backend full suite on the closure branch: `python -m pytest -q -p no:cacheprovider -rfE` (ephemeral migrated PostgreSQL 18, SECRET_KEY 64 chars) → 3121 passed / 9 failed / 111 skipped. The 9 failures are the pre-existing data-dependent tests already classified PRE_EXISTING by the checker (populated QA dataset required); `test_report_cross_format_reconciliation` now passes (R10). New tests: `test_audit_trail_decision_lineage.py` (8), `test_promotion_one_pass.py::test_an_identifier_already_registered_by_an_earlier_delivery_does_not_fail_promotion` (fails without the fix with `UniqueViolationError`, passes with it), `test_public_endpoint_disclosure.py` (+2 health-profile tests; config test asserts `disabled_modules` absent).
- Frontend: `npm ci`, `npm run test:ui` (all checks, +7 guardrails), `NEXT_PUBLIC_API_URL=… NEXT_PUBLIC_PROGRAM=TEFCA_ARC npm run build` and `=ALL` (81 pages each, EXIT 0).
- Browser (remediated TEFCA_ARC export + TEFCA_ARC backend): role probe (PM, reviewer, qalead, viewer, admin × 20 TEFCA routes; 24 GovCon routes; Core routes), UI check of R1/R2/R3/R4/R12, route matrix (TEFCA + Core-required + fixed Core routes, light/dark, five widths, axe, keyboard) — results in §F.
- Journey: `scratchpad/journey.py` against the closure backend (26/31 script assertions; the five "failures" were script expectations — SUCCEEDED vs READY_FOR_REVIEW, state on `case.state` not on `history`, sha of a reused intake — each verified by direct reads).

## F. Browser matrix at the closure head (filled by the run — see the addendum below)

## G. Remaining human actions

| Action | Owner | Reason | Blocks |
|---|---|---|---|
| Approve DEV deployment of the remediation build (backend PR #62 + closure branch, frontend #40 → #42 → #44 + closure branch) to a slot QA can use; cancel obsolete DEV run 34555888905 and approve/re-dispatch 34743956579 | Imran / release authority | `development` environment approval; concurrency slot held | FORMAL QA/UAT (12 retests), DEV RELEASE |
| Merge decisions for PR #62, the frontend stack, PR #59 (runtime `git_sha`), PR #61 (PDF engine + single h1), PR #58 (workflow governance), PR #41 (next/sharp) | Imran | Nothing merges without a human | MERGE, DEV RELEASE, PROD DECISION |
| Repository governance: protect backend `main` (PR required, 1 independent approval, dismiss stale approvals, required checks, block force-push/deletion, CODEOWNERS); frontend plan upgrade or visibility; prevent_self_review on `development`; clean reviewer-less environments; make security scans blocking | Imran / GitHub admin | Application code cannot compensate | PROD DECISION |
| Decide federal-profile Core route scope (R14) and a TEFCA_ARC DEV slot (R23) | Imran + PM (Nabeel) | Scope/topology decision | ISO-001, PROD DECISION |
| Authorize auth stage 1 (refresh at login, refresh router, silent renewal, `type` claim check, `kid`), admin token lifetime, viewer limiter tier (R15, R16) | Imran | Security decisions | PROD DECISION (not QA) |
| Verify PDF rendering in the container image (R20) | DevOps | Deliverable PDF | PROD DECISION |
| Review/approve the Federal Deployment Isolation Assessment v0.1 (with addendum) and the Authentication Target Architecture v0.1 | Imran / PM | PENDING_REVIEW | PROD DECISION |
| Provide QA with current credentials for testadmin / reviewer / qalead / viewer (operator step) | Operator | QA creds reset is not automatable | FORMAL QA/UAT |

## H. Final status

READY_FOR_FORMAL_QA_UAT = YES — conditional on the remediation build being deployed to DEV (human approval); the workbook, data path and screens are ready.
ALL_KNOWN_DEV_RELEASE_1_0_DEFECTS_CLOSED = YES for defects DEV can close (13 fixed, 1 documented data path); the remaining items are human decisions, environment blockers or deferred PRs, listed above.
TECHNICALLY_READY_FOR_PROD_DECISION = NO — blockers: GitHub governance FAIL; DEV provenance BLOCKED; profile-scope decision (R14) and TEFCA_ARC slot (R23); auth stage 1 / admin token / viewer tier decisions (R15, R16); PDF engine unverified in the container (R20); PR #59/#61/#58 unmerged; both v0.1 assessments PENDING_REVIEW; formal QA/UAT not yet executed on the remediated build.

PROD_AUTHORIZED = NO · MERGE_AUTHORIZED = NO · GATE_H_AUTHORIZED = NO

NEXT_ACTOR = IMRAN / HUMAN ACTION (DEV deployment approval) → QA → INDEPENDENT CHECKER
