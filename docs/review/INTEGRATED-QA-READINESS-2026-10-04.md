# Integrated QA readiness — Parts A and B, frontend, workbook, LMS, SSP impact
**Date:** 2026-10-04 (Round 25). **Prepared by the same session that made the
changes: this is supporting evidence, not independent approval.**

## Decision

**READY WITH NAMED LIMITATIONS — for independent review only.**

It is **not** ready for Adam. That requires, and none of these has happened:
independent review, authorised publication, authorised deployment,
confirmation of the deployed version, test accounts, seeded synthetic data
on the test environment, and the DEV smoke checks.

Named limitations that a reviewer must weigh:

1. With the active rules an organisation is still recorded `verified` while
   SAM.gov did not answer (3 of 12 corpus seeds). It is now **labelled**
   everywhere as "Verified — checks incomplete" and counted separately; it
   is **not prevented**. Preventing it is policy decision P1, unapproved.
2. The full regression ran at `8f5bfeb`; the five later backend commits were
   confirmed by an affected-test run (§4.4), not by a second full run.
3. Twelve failures in the full run are order-dependent (they pass alone on a
   new database at base and at head) and are **not fixed**.
4. One test needs live external sources and was not run (forbidden here).
5. Browser evidence is local runs with synthetic data. The menu click
   straight after sign-in was not confirmed (§7).
5a. A Viewer account can hit the request limit by opening the delivery list
   and then a delivery straight away, and is told "Rate limit exceeded … free
   tier" (§7). Pre-existing; not changed.
6. The submitted SSP could not be identified; an impact register was
   produced instead of a redline (§10).
7. No source policy is approved; `SEED_RULES_V4` inactive; "1,298" unresolved.

## 1. Exact state

| | Backend | Frontend |
|---|---|---|
| Repository | `DocuAction/docuaction-backend` | `DocuAction/docuaction-frontend` |
| Published candidate (unchanged; verified with `git ls-remote` this round) | PR #110 head `ea92ea5c33a5075dc40b1473a64152bb52a5c3e5` | PR #66 head `48031780ad4ce0b84126ae7187f49d1d1a8ee87b` |
| Local branch | `feature/preflight-exceptions` (no upstream) | `feature/preflight-exceptions` (no upstream) |
| Local head (code and tests) | `0f5cab344a8c2cf99fa30cce1a8bc30967b88082` | `16b50ed6fd6aa5e96921897b1495783c8550ca64` |
| Commits ahead of the published candidate | 19 (this document's own commit follows) | 3 |
| Worktree | `%TEMP%\combined-be` | `%TEMP%\combined-fe` |

The worktree path was not trusted: branch, head and ancestry
(`ea92ea5` is an ancestor of the backend head; `48031780` of the frontend
head) were read from git.

Nothing was pushed. No PR was edited. No merge, shared migration,
deployment, firewall or account change. No official finding changed. No
live external source was queried: every test process and the local API ran
with outbound HTTP pointed at a closed local port.

**Migrations on the branch after the published candidate's chain**
(disposable databases only): `20261004_preflight_exec_held`,
`20261004_stage_event_preflight`, `20261004_recheck_jobs` (head). No
migration was added in Round 25.

**Untracked files left untouched, not committed:** backend
`WORKFLOW_PROOF_LOG.txt`, `journey_backend.log`, `rollback_candidate.log`;
frontend `AGENTS.md`, `CLAUDE.md`, `journey_frontend.log`.

**Processes.** At the start: 786 MB physical memory free of 16 GB, commit
49.7 of 57.1 GB, almost all browsers (not touched). Stopped, because they
belonged to this task and held about 1.4 GB: two idle Next.js dev servers
(ports 3104, 3106) and one stale local API (port 8103, restarted later at
the current commit). The disposable PostgreSQL cluster on port 5534 (data
directory inside this job's scratch folder) was restarted after crash
recovery. A second cluster on port 5499 and the machine's own service on
5432 belong to other work and were not touched.

### Round 25 commits

| Commit | Repo | What |
|---|---|---|
| `d6a1d1c` | BE | Source outcome, classification and completeness reported as three facts; reports/registry/workspace/manual review/pipeline carry the qualified status; `reference_preflight` route exercised over HTTP; one live-network test made deterministic |
| `3083ced` | BE | Delivery completeness endpoint; recheck list endpoint; case workspace `verification_status`; source labels; **new regression fixed** (RBAC floor registration) |
| `de6ec8c` | BE | A recheck in which no entity was asked ends FAILED, not SUCCEEDED (found in the browser) |
| `ab485ad` | BE | Tests state their environment dependencies; one stale contract test updated |
| `f5002e4` | FE | Source readiness, verification completeness, rechecks, source policy on existing screens; no raw JSON on user-facing pages; IQVIA badges use design tokens |
| `18b0ea0` | FE | Live browser journey spec; recheck result summary by outcome |
| `0f5cab3` | BE | Cross-delivery isolation tests assert on their own delta, not on an empty table |
| `16b50ed` | FE | Live navigation audit by role |

## 2. Completed, partial, blocked

**Completed**
- Regression at `8f5bfeb` in 27 sequential batches; every failure classified (§4).
- False-pass semantics: implemented, tested, corpus re-run (§5).
- `reference_preflight` on the snapshot-status route: exercised through the
  real application for 401/403/404/200 and the feature-off refusal.
- Frontend items F1–F11 on existing screens (§6), with unit tests.
- Raw JSON removed from eleven on-screen sites (§7).
- One integrated workbook, 39 cases (§9). START-HERE updated.
- LMS draft against the implemented screens, marked proposed (§9).
- SSP impact register (§10).

**Partial**
- Browser coverage: 11 of the 24 new workbook cases were exercised in a
  browser; the rest by automated tests only (§6).
- Navigation audit: 24 distinct pages and all eight delivery tabs were opened in a
  browser as Analyst, QA Lead and Viewer (§7). Pages were opened and
  checked; multi-step actions on them (IQVIA upload, report generation and
  download, independent QA approve/return, exception disposition) were
  **not** performed in a browser this round.
- Confirmation after the last code change: affected tests only (§4.4).

**Blocked**
- Anything on DEV (not deployed).
- A redline of the submitted SSP (baseline not identified).
- `test_chunked_gather_correctness` (needs live sources).
- A usability read by someone unfamiliar with the product.

## 3. Method

- Database: PostgreSQL 18, disposable cluster on port 5534, a **new
  database per run** created by `mkdb.py`, migrated with
  `alembic upgrade head` as `docuaction_owner`, tests run as `postgres`.
- Network: `HTTP_PROXY`/`HTTPS_PROXY` set to `http://127.0.0.1:9`;
  `NO_PROXY=127.0.0.1,localhost`.
- Full run: detached worktree at exactly `8f5bfeb`
  (`%TEMP%\reg-be-8f5bfeb`), `tests/test_*.py` sorted, 10 files per batch,
  one `pytest` process per batch, never two at once, JUnit XML per batch,
  one shared database `rg_head` for the whole run (as a single full run
  would use).
  `python -m pytest <10 files> -q --tb=short -p no:cacheprovider --junitxml=bNN.xml`
- Baseline: detached worktree at exactly `ea92ea5` (`%TEMP%\base-be-ea92ea5`).
- Memory before each batch was 8.8–10.5 GB of commit headroom; no batch was
  stopped for memory.
- Evidence files (job scratch, not committed): `reg/head_8f5bfeb/`
  (`manifest.jsonl`, `bNN.log`, `bNN.xml`, `SUMMARY.txt`), `reg/classify/`,
  `reg/final_ab485ad/`.

## 4. Regression

### 4.1 Totals at `8f5bfeb`

| Collected | Deselected | Run | Passed | Failed | Skipped | Errors |
|---|---|---|---|---|---|---|
| 4,563 | 1 | 4,562 | 4,413 | 29 | 120 | 0 |

Batch 2 stalled for 11 minutes on
`test_chunked_gather_correctness::test_chunked_and_unchunked_gather_produce_identical_classifications`,
which calls the real source connectors; its process was stopped and the
batch was re-run with that one test deselected (216 passed, 2 skipped).

Per-batch selections and counts are in `reg/head_8f5bfeb/SUMMARY.txt` and
`manifest.jsonl`.

### 4.2 The interrupted run of the previous round

Its nine unnamed failures are now named: the five in
`test_automated_verification.py` and four in
`test_automated_verification_cross_delivery_isolation.py`. The ninth is
`test_no_review_record_is_ever_created_for_either_delivery`.

**Correction to the Part B package (§10 there).** It said the eight
coverage failures were caused by hand-built evidence using the literal
`D1_IDENTITY`. That was wrong. The cause is below.

### 4.3 Every failure, classified

"Alone" = the file run by itself on a new database, at `8f5bfeb` and at
`ea92ea5`.

| # | Test(s) | Count | Alone at head | Alone at base | Class | Cause | Action |
|---|---|---|---|---|---|---|---|
| 1 | `test_automated_verification` (5), `..._cross_delivery_isolation` (3) | 8 | fail | fail | **Reproduced pre-existing** — test depends on ambient environment | The tests never set `ENTITY_RESOLVER_SOURCE=db` (default `mock`), so no reference resolved and every entity was NOT_ELIGIBLE. Proven: with the variable set, 19/19 pass | **Fixed** in `ab485ad` (autouse fixture). No product change |
| 2 | `..._cross_delivery_isolation::test_no_review_record_is_ever_created_for_either_delivery` (the "ninth") | 1 | pass | pass | **Test isolation / pollution** | Asserts on whole-table state; fails only after earlier tests in the same database | Not fixed |
| 3 | `test_delivery_delta` | 8 | pass (23) | pass (23) | **Test isolation / pollution** | Same | Not fixed |
| 4 | `test_phase8_reconciliation::…::test_nothing_became_reportable` | 1 | pass | pass | **Test isolation / pollution** | Same | Not fixed |
| 5 | `test_qa_gate::test_no_fabricated_history_for_existing_determinations` | 1 | pass | pass | **Test isolation / pollution** | Same | Not fixed |
| 6 | `test_supervisor_operations::test_the_queue_shows_every_state_and_agrees_with_the_case_itself` | 1 | pass | pass | **Test isolation / pollution** | Same | Not fixed |
| 7 | `test_job_detail_contract::test_reviewer_gets_the_evidence_blocks` | 1 | fail | fail | **Reproduced pre-existing** — stale test | Predates QA108-20260927-002, which made records/lineage/audit opt-in via `?include=`. **Not an overlap** with any failure above; counted once | **Fixed** in `ab485ad` (asserts the deferred default and the included form) |
| 8 | `test_rbac_roles::test_no_tefca_read_endpoint_sits_above_the_viewer_floor` | 1 | — | n/a (routes do not exist at base) | **NEW REGRESSION** (introduced by `4878968`) | Two recheck drill-down GETs sat at the reviewer floor without being registered | **Fixed** in `3083ced`: registered as a documented exception, same precedent as the coverage drill-down. **Raised for review as an access decision** |
| 9 | `test_journey_iqvia_live`, `test_journey_qa_live`, `test_journey_reporting_live` | 3 | — | — | **Environment** | Need a local API on port 8103 bound to the same database | Run at `ab485ad` with that server present: 3 passed |
| 10 | `test_sam_e2e_delivery_path::test_clean_confirmed_entity_is_not_disqualified_by_sam` | 1 | fail | fail | **Environment** — unpatched live call | The CMS connectors were not faked; offline the exclusion dimension reads UNAVAILABLE | **Fixed** in `ab485ad` (deterministic CMS answers) |
| 11 | `test_sam_manual_review_asymmetry::test_regression_clean_fully_evidenced_entity_…` | 1 | — | — | **Environment** — same cause | Same | **Fixed** in `d6a1d1c` |
| 12 | `test_review_id_concurrency::test_four_concurrent_batches_all_unique` | 1 | fail | fail | **Environment** (reproduced pre-existing) | `RuntimeError: … Lock … is bound to a different event loop` on this Windows host | Not fixed; not caused by this branch |
| 13 | `test_workflow_proof_2026_10_03::test_combined_workflow_proof` | 1 | fail | fail (same assertion) | **Environment** (reproduced identically at base) | The proof drives the real pipeline and report with the CMS connectors unpatched; 12 blocked connection attempts in each run; the report then shows no B2 entity. Whether it passes with network access was **not** re-checked this round | Not fixed |
| — | `test_chunked_gather_correctness::…` (deselected) | — | not run | not run | **Unresolved** | Requires live external sources; forbidden in this round | Not run |

8 + 1 + 8 + 1 + 1 + 1 + 1 + 1 + 3 + 1 + 1 + 1 + 1 = 29.

**Impact of what is not fixed.** Rows 2–6 are twelve tests that assert on
the whole estate and fail when other tests have committed rows to the same
database. They pass on a new database at both revisions, so they are not
evidence of a product defect on this branch; they are evidence that a single
shared-database run of the whole suite cannot be green. Fixing them means
per-module cleanup or per-module databases, which is outside this round.

### 4.4 After the last code change (`ab485ad`)

Five backend commits followed the full run (`d6a1d1c`, `3083ced`,
`de6ec8c`, `ab485ad`, `0f5cab3`). They change eleven application files
(reports, registry queries, analyst workspace, pipeline result, manual
review response, rechecks, source policy, preflight/recheck routes, case
workspace, the new completeness module). A second full run was not done.
Instead every test file whose name touches those areas was run on a new
database (`rg_final`) at `ab485ad`, 12 files per batch, sequentially:

| Files | Run | Passed | Failed | Skipped |
|---|---|---|---|---|
| 82 | 1,395 | 1,345 | 15 | 35 |

The 15 failures are all already classified in §4.3 and none is new:
`test_delivery_delta` ×8, `test_qa_gate` ×1, `test_supervisor_operations`
×1 (order-dependent, not fixed); `test_workflow_proof` ×1 (environment);
and four coverage tests that read a whole table (order-dependent). Those
four **were then fixed** in `0f5cab3` (assert on the delta the test's own
run produced) and re-run: 19 passed on a new database and 19 passed on the
heavily used full-run database `rg_head`.

Also run at the current code against the running local API: the three
`test_journey_*_live` tests, 3 passed.

Not re-run after `0f5cab3`: nothing else — that commit changes two test
files only.

### 4.5 The 120 skips, explained

| Count | Reason given by the test |
|---|---|
| 21 | Azure artifact storage account not configured on this host |
| 9 | Needs `CONV_SUPERUSER_URL` (these convergence fixtures were run separately in Round 24: 9 passed) |
| 46 | Frontend sources are not beside this backend checkout (source-reading tests) |
| 8 | Requires the populated development dataset |
| 6 | No authenticated test account available |
| 9 | `BULLETIN_AUTH_ENABLED` is off (by design) |
| 7 | No sandbox database named `docuaction` for a concurrency test |
| 4 | Viewer is the lowest role; nothing below it to test |
| 4 | This Windows account cannot create symlinks |
| 4 | WeasyPrint / PDF native libraries missing on this Windows host |
| 2 | One `no briefing matches`, one `live run needs DEMO_EMAIL` |

No skip was added and no test was newly skipped to obtain a result.

### 4.6 Frontend

`npx vitest run`: **41 files, 310 tests passed.** `npm run test:ui`
(design guardrails): passed after one fix — it failed at the published
base too (colour literals on the IQVIA page). `npm run build` (static
export): succeeded.

## 5. False-pass semantics

Trace of the three official-view cases (B04 error body, B05 HTTP 429, B06
timeout):

| Stage | Before | Now |
|---|---|---|
| Connector | `SourceResult.unavailable` | unchanged — already correct |
| Evidence | SAM.gov item `UNAVAILABLE` | unchanged |
| Classifier input | `sam_gov: unavailable` | unchanged |
| Classification | B1 by RULE-002 | **unchanged** (rule text is approved; not edited) |
| Entity status | `verified` | **unchanged** (policy P1) — and described: `verification_completeness.overall_status = verified_checks_incomplete` |
| Pipeline result | `"verified": N` meant "processed" | adds `processed`, `entities_marked_verified`, `entities_marked_verified_checks_incomplete`; the legacy key is kept and documented as a misnomer |
| Coverage per source | SAM.gov counted unavailable | unchanged; the card now also states "Not checked" and that "Verified" counts only answered lookups |
| Reports | chart bar "verified" | "verified" split into `verified`, `verified checks incomplete`, `verified checks not recorded`; parts sum to the original; note and language note added |
| Registry list/detail/stats | `verification_status: verified` | stored value untouched; `verification_overall` and its label added; stats gain `by_verification_overall` |
| Case screen | no statement | "Verified — checks incomplete", sources named, "neither a pass nor a finding" |

Three things are now distinct in code (`verification_completeness.py`):
a source check that **successfully verified** (`CONFIRMED`, `NOT_LISTED`
only); the **classification** (untouched); **completeness** (`COMPLETE`,
`INCOMPLETE`, `NOT_RECORDED`). `UNAVAILABLE` is never mapped to an
exclusion, a non-compliance or a not-found (tested).

### Corpus gates, re-run (12 pipeline seeds + 16 component seeds, synthetic)

| Gate | Official view | Proposed (inactive) view |
|---|---|---|
| G1 lost seeded risk signals | **0** | 0 |
| G2a a source fault counted as a successful source check | **0** | 0 |
| G2b an unqualified overall "verified" on a fault seed | **0** | 0 |
| G2c entity status `verified` while a check did not answer | **3** (B04, B05, B06) | 0 |
| G3 changed outcomes unexplained | 0 (3 changed, each by `sam_gov: UNAVAILABLE`) | — |
| G4 original delivered data changed | 0 | 0 |
| G5 genuine findings / history lost | 0 | 0 |

G2c is not made green by relabelling. It stays 3 in the official view and is
the policy-dependent residue: **P1 — may an organisation be recorded
verified (and so be eligible for auto-completion) while an exclusion check
did not answer?** The proposed-view column is produced with
`ENFORCE_COMPLETE_EXCLUSION_SCREENING` on, in a disposable database only.

Corpus split of the 4 `verified` entities: 0 complete, 4 checks incomplete
(B01 too — the corpus fakes the CMS enrolment source as unavailable for
every record), 0 not recorded.

## 6. Part A/B integration — delivery path and manual path

| Behaviour | Delivery path (real pipeline) | Manual-review path |
|---|---|---|
| Preflight enforcement; shadow does not mutate | `test_preflight_enforcement_2026_10_04` (4: off → no stage; clean continues; bad id shape holds; missing column blocks), `test_seeded_fixture_corpus_2026_10_04` (9), `test_preflight::test_preflight_four_dimensions_and_untouched_originals` | Not applicable: preflight is a delivery-file check |
| Reference-snapshot preflight | `test_reference_preflight_2026_10_04` (19: IQVIA import with flag off and on), `test_iqvia_routes::TestSnapshotStatusOverHttp` (3, over HTTP) | Not applicable. **Only IQVIA is an implemented snapshot import path with preflight**; CMS PPEF ingest has its own schema validation (`test_ppef_bulk_ingest_gate`); NPPES data-file ingestion does not exist |
| Missing-NPI OIG name screening | corpus B09 (candidate → B4), B10 (clean name screen is not a pass) | `test_exclusion_name_screening_2026_10_04` (3 manual-path tests: candidate found; clean screen not verified; unreachable list unavailable, never clear) |
| SAM/OIG ambiguous and unavailable | corpus B03 (ambiguous → not B1), B04–B07; `test_reference_source_faults_2026_10_04` (18) | `test_sam_manual_review_asymmetry` (9): persisted exclusion disqualifies; unavailable stays unavailable; SAM never queried live on this path, disclosed as not evaluated; response now carries `verification_completeness` |
| Prior risk preserved when later evidence disappears | `test_prior_risk_not_cleared_2026_10_04` (5); browser: B02 second cycle | `test_case_a_persisted_bulk_exclusion_now_disqualifies_on_the_manual_path` (persisted exclusion evidence is consumed, worse wins) |
| NPPES findings reach the ledger | corpus (exactly `NPI_DEACTIVATED` and `NPI_VERIFICATION_UNAVAILABLE`) | `test_verification_findings::test_record_from_sources_reads_the_probed_nppes_dict` |
| No bulk compliance closure; no inherited clearance | `test_no_bulk_closure_2026_10_04` (4) | Reviews are decided one record at a time by route; no bulk route exists. UI: no multi-select on the review lists (code read; component tests) |
| Recheck authorisation, idempotency, crash recovery | `test_rechecks_2026_10_04` (18, including the new "nothing resolved is a failure"); browser: requester cannot approve, QA Lead approves and runs, stops when the source is still unavailable | Not applicable: rechecks are delivery-scoped |

Public-data only: no test or fixture carries or requires an EIN, TIN or
SSN; their absence raises no finding. Unresolvable potential hits stay
unresolved and actionable.

**Default-off settings** (all three default False):
`ENABLE_PREFLIGHT_ENFORCEMENT`, `ENABLE_CONTROLLED_RECHECKS`,
`ENFORCE_COMPLETE_EXCLUSION_SCREENING`. Enabled paths were demonstrated
only in disposable databases. Workbook cases that need a setting switched:
INT-17, INT-18 (enforcement on); INT-28–INT-31 (rechecks on); INT-32
(rechecks off). The third setting is a proposed policy and no case tests it
switched on.

**Gap found and fixed this round:** a recheck in which nothing resolved
reported success (`de6ec8c`). **Observation, not resolved:** the entity
resolver defaults to `mock`; a deployment that does not set
`ENTITY_RESOLVER_SOURCE` resolves nothing. What DEV sets was not checked
(operator item).

## 7. Frontend

| Item | Where | State |
|---|---|---|
| F1 readiness card | Delivery → Overview → "Source readiness" | Built; unit + browser |
| F2 timeline | Delivery → Processing Timeline: "Source readiness check"; held wording | Built; unit only |
| F3 four-column findings | Delivery → Exceptions → "Source readiness findings" | Built; unit + browser |
| F4 per-source counts, completeness | Delivery → Verification → "Overall verification status"; "Not checked" per source | Built; unit + browser. "Screened by name only" and "Not applicable" as separate per-source columns are **not** built (the coverage API does not return them) |
| F5/F11 rechecks | Delivery → Verification → "Rechecks" | Built; unit + browser |
| F6 dates and policy chip | Case workspace → "Evidence dates and source policy" | Built; unit + browser |
| F7 banner | Case workspace → "Verification status for this case" | Built; unit + browser |
| F8 individual exclusion candidates | Review lists | No change needed: no multi-select exists. A dedicated "exclusion candidates" list was **not** built |
| F9 IQVIA reference check | IQVIA page → "Reference file check" | Built; **not** exercised in a browser |
| F10 source policies | Sources & Connectors → "Source policies" | Built; unit + browser |

### Raw JSON on user-facing pages — reproduced and fixed

Reproduced by reading the code: eleven places printed a server object with
`JSON.stringify`. Fixed with one renderer (`lib/ReadableDetail.js`):
Audit & Decision History details; delivery Audit History detail; delivery
Records lineage; Changes and Lineage fallback; Supervisor Operations event
payload; Review Cycles, Findings and Platform Health detail rows; evidence
dimension field lists; the notifications widget; and the generic display
fallback in `present.js`. API responses, CSV exports and the explicit
"Copy technical detail" action are unchanged.

### Coverage, kept separate

**Browser-tested** (real Chromium against the static export in `out/` and a
real local API running the `de6ec8c` application code on a disposable database seeded
with the synthetic corpus; real sign-in form; real clicks;
`tests/e2e/live-partb.spec.mjs`, 2 tests passed): sign-in for Analyst and
QA Lead; deliveries list → "View details"; Overview readiness card;
"View readiness findings" → four columns; Verification completeness (4 of
12 "Verified — checks incomplete", 0 "Verified", SAM.gov named); recheck
requester cannot approve; a different QA Lead approves and runs; the job
stops "source still unavailable" with "Try again"; workspace banner for an
uncleared earlier concern; workspace "checks incomplete" and unknown
freshness; source policies read-only; "All deliveries" and browser Back;
no raw JSON on any page visited (12 page checks). Screenshots:
`qa-evidence/2026-10-04-sunday-qa-prep/precheck-evidence/`.

**Not confirmed in the browser:** clicking "ONC/RCE Deliveries" in the menu
on the first page after sign-in (the entry was not found within 15 seconds
and the address was used instead; the same menu worked on a later page).
Cause not established.

**API/component-tested only:** blocked delivery with enforcement on;
duplicate-header finding; IQVIA reference check and refusal; duplicate
recheck request; rechecks switched off; name-candidate on manual review;
report chart split; viewer messages; timeline held wording.

### Navigation audit by role (`tests/e2e/live-nav-audit.spec.mjs`)

Real browser, real sign-in, static export, local API. For every page:
a heading rendered and no error boundary; no uncaught script error; no raw
JSON in the visible text; whether a permission screen appeared.

| Role | Pages / controls | Checks | Result |
|---|---|---|---|
| Analyst | 18 menu pages; 2 pages not offered below QA Lead (by address); all 8 delivery tabs; SAM.gov "Unavailable" count → organisation list → CSV; recheck entity CSV; a failed delivery; an unknown delivery id; My Reviews → case → workspace → Back | 119 | all pass |
| QA Lead | Audit & Decision History (opened a row; details readable), Platform Health & Technical QA, Supervisor Operations, My Reviews, Contract Reports | 21 | all pass |
| Viewer | Deliveries; delivery detail (role message on the readiness card); Verification (overall status visible; recheck panel names the role needed; page not replaced by a permission screen); Exceptions ("Requires the reviewer role"); Sources & Connectors; IQVIA | 22 | all pass, after pacing — see finding 1 |

States seen: loading skeleton; empty (no recheck / no report); permission
denied as a whole-page screen (Analyst opening Audit & Decision History by
address — expected); role message inside a panel (Viewer); failed job
(stage, error reason, remediation guidance); unknown delivery id (stated
reason, "Back to deliveries"); source unavailable (coverage card and
recheck "Stopped — source still unavailable"); retry ("Try again" on a
failed load and on a stopped recheck).

**Findings from the audit**

1. **Viewer request limit (pre-existing, not changed).** The Viewer role is
   on the lowest request tier (60 a minute, 10 in any 5 seconds). Opening
   the delivery list and then a delivery straight away exceeded it: the
   detail page showed "Could not load the delivery — Rate limit exceeded.
   60 requests/minute allowed for free tier", with "Try again" and "Back to
   deliveries". The failed-load state itself is correct; the limit and the
   words "free tier" are a usability problem for a read-only Government
   viewer. Screenshot `nav-viewer-rate-limited.png`. A decision, not a code
   fix, in this round.
2. **Platform Health & Technical QA** is hidden from the menu below QA Lead
   but opened for an Analyst by address without a permission screen (its
   data calls are refused by the server). Observation only.
3. **IQVIA page** opens for a Viewer without a permission screen; actions
   on it are refused by the server. Observation only.
5. **Per-source counts on the Verification tab.** On the synthetic corpus the
   NPPES card showed Eligible 12, Verified 8, Not found 9, Unavailable 3 —
   more outcomes than organisations (the second cycle for one record and
   more than one evidence row per organisation are the likely reasons).
   Seen in a screenshot; **not investigated**. The SAM.gov card's "Not
   found 9" is the existing vocabulary for a clean name screen. Both are
   pre-existing coverage semantics, left as they are.
6. The audit's first attempt produced two false alarms from its own timing
   and selectors (reading a loading skeleton; a link-name pattern). Both
   were corrected in the audit, not in the application.

**Not done in a browser this round:** multi-step actions on those pages —
IQVIA upload and approval, report generation and each download format,
independent QA approve/return/escalate, exception disposition, delivery
registration, the validation/held queue's own actions. Their existing unit
and API tests pass; this round produced no new browser evidence for them.
"S-file navigation" was taken to mean the delivery (source-file) detail
tabs, as in the earlier readiness table; all eight were opened.

The known hydration issue of the local dev server was not used as evidence
either way: the deployable static export was built and served.

## 8. Unapproved policy decisions (unchanged; none approved)

P1 complete exclusion screening before `verified`; P2–P6 as listed in
`REVIEW-PACKAGE-PREFLIGHT-B-2026-10-04.md` §11. Retention of verification
evidence: no authority on file → `POLICY_UNAPPROVED`; no automatic deletion.

## 9. Workbook and LMS

- **Integrated workbook:**
  `qa-evidence/2026-10-04-sunday-qa-prep/DocuAction_Integrated_QA_Workbook_v2_2026-10-04.xlsx`.
  39 cases: SUN-01..SUN-15 carried forward unchanged, INT-16..INT-39 new,
  each cross-referenced to its PB id. Sheets: start here, test cases, test
  data, pre-check (automation), Adam's results (blank), defects, known
  limitations, settings and build, case-id map. No password. The earlier
  workbook is unchanged.
- Representative instructions were checked against the real screens through
  the browser run. **No independent person unfamiliar with the product has
  read them; no usability acceptance review occurred.**
- `START-HERE.md` updated (testing order, prerequisites, limitations).
- **LMS:** `…/LMS-UPDATE-PROPOSAL-PARTAB-2026-10-04.md` — five lessons,
  marked proposed; the frozen 1.2.0 baseline is untouched; nothing published.

## 10. SSP

`qa-evidence/2026-10-04-ssp-impact/SSP-IMPACT-REGISTER-2026-10-04.md`.
Seven candidate SSP files, six distinct, **none identified as the one
submitted to ONC** (no transmittal record found). Version 1.2 (16 July
2026) carries an ONC distribution list and is the most likely candidate;
that is an inference. Result: a 16-item impact register with severity,
owner and status, and draft amendment text to apply once the baseline is
confirmed. No SSP file was modified. Three items are High and two of those
are pre-existing gaps this work surfaced (external-source list; role table).
No FedRAMP-readiness, full-control-coverage or Section 508 claim is made.
Live Azure configuration was not verified.

## 11. Accounts and fixtures needed for QA

Program Manager (or Test Admin), Analyst, QA Lead, Viewer — four people.
Synthetic corpus seeded by an engineer on the test environment. Eight
readiness fixtures in `tests/fixtures/seeded/`. Two IQVIA files built fresh.
Details: workbook sheet 3.

## 12. Remaining operator actions and release blockers

1. Independent review of both branches (19 + 3 commits, plus this document), including the
   access decision in §4.3 row 8 and policy P1.
2. Decide P1–P6 and the retention authority.
3. Publication, CI on the published SHAs, deployment — each needs explicit
   authorisation.
4. Confirm `ENTITY_RESOLVER_SOURCE` and the three default-off settings on DEV.
5. Seed the synthetic corpus on DEV; provision the four accounts.
6. Locate the SSP transmittal; confirm the baseline.
7. Decide whether to fix the twelve order-dependent tests.
8. A second full regression at the final head, if the reviewer requires more
   than §4.4.
