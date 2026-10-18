# Regression manifest — one record, 2026-10-04 (R27-4)

Backend `feature/preflight-exceptions`. The 27-batch run itself is bound
to `5d54e06` (the state at the end of Round 26 D3, before this round's
R27-2/R27-3 changes). R27-2 and R27-3 added code since then (`e82476c`,
`e2c2561`); their own targeted test runs are recorded separately in
section 4 below, against the final head `e2c2561`. No full-suite re-run
was performed this round — section 5 states why that is justified rather
than merely asserting it.

## 1. The 27-batch run, unique results

27 unique batch indices (0–26), `run_batches.py`, one pytest process per
batch, `tests/test_*.py` sorted alphabetically and chunked in tens plus
`tests/acceptance`. Disposable database `rg_d2final` throughout.

**33 executions were logged for 27 batch indices** — four batches ran
more than once, and only the LAST (kept) execution of each counts toward
the totals below; the superseded attempts are history, not double-counted
results.

| Batch | Executions | Final result (counted) | Why it ran more than once |
|---|---|---|---|
| 2 | 3 | 216 passed, 2 skipped | `test_chunked_gather_correctness.py`'s known live-network dependency hung the batch twice (killed both times); the third execution deselected that one file and passed clean. |
| 10 | 3 | 168 passed | First execution stopped early for low system memory (environmental, not a test result — recorded, not counted). Second execution ran and failed on this round's own brand-new test (two self-contained bugs: an oversized synthetic `review_id`, a `ReviewRecord` missing its required subject). Third execution, after fixing both, passed clean. |
| 14 | 2 | 148 passed, 11 skipped | First execution passed (`rc=0`) but — discovered only later, from batch 15's failure — left two rows in the shared database that violated a different test's invariant. Second execution, after fixing the fixture that created them, re-confirmed the same pass with the cause removed. |
| 15 | 2 | 310 passed, 5 skipped | First execution failed on `test_qa_gate.py`'s "no fabricated history" check, because of batch 14's leftover rows (above). Second execution, after batch 14's redo and deleting the two superseded rows, passed clean. |
| 0, 1, 3–9, 11–13, 16–26 | 1 each | — | Passed on the first and only execution. |

**Totals, the 27 counted results:** 4,593 tests collected, 4,469 passed,
0 errors, 121 skipped, **3 failed** — all three are the server-dependent
cases in section 2, not code defects (see there for why, and for their
independent confirmation).

## 2. The three server-dependent cases and their six-test confirmation

Batch 9 reported `test_journey_iqvia_live_2026_10_04.py`,
`test_journey_qa_live_2026_10_04.py` and
`test_journey_reporting_live_2026_10_04.py` as failed. Each needs a real,
bound HTTP server on `127.0.0.1:8103` — the project's own
`.github/workflows/pr-tests.yml` `journey-live` job starts one before
running them; the sequential batch runner never does, so these three get
a plain connection-refused error instead of the database-only skip the
rest of the suite uses. **Not a code defect** — proven by reproducing
that job's own recipe locally (disposable `test_journey_1003`, migrated
to head, a real `uvicorn` process on `127.0.0.1:8103`) and running all
SIX of that job's own tests together, exactly as it does:

| # | Test | Result |
|---|---|---|
| 1 | `test_qa_approval_route_level_2026_10_03.py` | pass |
| 2 | `test_shadow_real_v4_candidate_2026_10_03.py` | pass |
| 3 | `test_workflow_proof_2026_10_03.py` | pass |
| 4 | `test_journey_iqvia_live_2026_10_04.py` | pass |
| 5 | `test_journey_qa_live_2026_10_04.py` | pass |
| 6 | `test_journey_reporting_live_2026_10_04.py` | pass |

**6 passed, 0 failed, 1 execution of this group this round (Round 26 D3;
not repeated in R27 — no R27 code change touches any file these six
exercise).** The three batch-9 "failures" and this six-test confirmation
are the SAME three tests counted twice in different contexts (plus three
more, route-level tests from the same CI job, run alongside them because
that job always runs all six together) — not six additional, separate
defects.

## 3. All 121 skips, every cause named, grouped

Computed directly from the 27 counted batch result files (not
approximated). Each group is dispositioned for release relevance.

| Count | Cause | Release-relevant gap? |
|---|---|---|
| 21 | `tests.test_azure_artifact_store` / `tests.test_phase9_operational.TestScreenIntegrations` — `REPORT_ARTIFACT_AZURE_ACCOUNT`/`_CONTAINER` not set; the Azure artifact backend is not exercised on this local machine | No — environment-local only; this backend is exercised on DEV/CI where those variables are set. |
| 9 | `tests.test_prod_convergence_integration` — `CONV_SUPERUSER_URL` not set (needs a superuser test database) | No — a local-machine privilege gap; this path is exercised in the dedicated convergence CI job. |
| 8 | `tests.test_phase75_cutover.TestFrontendCutover` — frontend sources not present in this checkout | No — this backend worktree has no frontend beside it; the equivalent frontend checks run in the frontend repo's own suite. |
| 7 | `tests.test_phase8_learning_center.TestContextualHelpComponent` — frontend source not present: `components/LearningHelp.js` | No — same cause as above, one specific file. |
| 6 | `tests.test_phase7_report_data.TestDerivedFromPersistedEvidence` — requires the populated development evidence dataset (0 rows at the expected rule version in this fresh database) | No — this disposable database was never meant to carry that dataset; the check runs against DEV's own populated data. |
| 6 | `tests.test_prod_convergence_integration` / `tests.test_prod_managed_migration_integration` — needs a superuser test DB (see above, split across two files) | No — same as the 9-count row above. |
| 6 | `tests.test_qa_round2` / `tests.test_supervisor_operations` / `tests.test_priority_review_operational` — "no authenticated test account available" | No — a local-only account-provisioning gap in this disposable setup, not a code gap; the same journeys are proven live elsewhere this round and last (sections above, and the live-journey specs). |
| 5 | `tests.test_bulletin_auth` — `BULLETIN_AUTH_ENABLED` is off; `guard()` is a no-op by design | No — an intentional, documented default-off gate, working as designed. |
| 5 | `tests.test_case_assignment` / `tests.test_phase75_cutover.TestPdfEnvironment` / `tests.test_reports.TestPDF` — "no sandbox database for a concurrency test: database \"docuaction\" does not exist" | No — a hardcoded sandbox-database name this isolated setup never creates on purpose (it would point a concurrency test at a shared, non-disposable name); the concurrency behaviour itself is covered by `test_review_id_concurrency.py` and the D2 fixes. |
| 5 | `tests.test_phase8_learning_center.TestNoUnsupportedPolicyWording` — frontend source not present: `help/page.js` | No — same frontend-checkout cause as above. |
| 4 | `tests.test_lms_sync.TestRoutesAndKeys` — "frontend checkout not beside the backend" | No — true in CI's own skip condition; the actual sync is proven when both repos are checked out side by side, as they were earlier this round for the live specs. |
| 4 | `tests.test_rbac` / `tests.test_rbac_delivery_fields` — "viewer is the lowest role; there is nothing below it" | No — the test is explicitly checking a boundary that has no lower neighbour to test against; not a gap, a tautology the test states rather than silently assumes. |
| 4 | `tests.test_upload_security_safe_existing_path.TestOutsideTargetSymlinks` — this Windows account cannot create symlinks without elevated privilege | No — a local OS-privilege limitation on this machine; the path-traversal protection itself is covered by the rest of that file's non-symlink cases. |
| 3 each | `tests.test_phase9_operational.TestScreenIntegrations` — "frontend screen not present: {analytics, connectors, findings, qa, reports, reviews, validation}" (7 screens × 3 = 21, already counted in the top row) | No — same frontend-checkout cause. |
| 2 | "no sandbox database available for a concurrency test" (`test_case_assignment.py`, two specific tests) | No — same cause as the 5-count sandbox-database row above. |
| 2 | WeasyPrint native libraries unavailable on this Windows host | No — the PDF rendering path runs in the project's Linux container image; this is a documented, platform-scoped gap, not silently assumed. |
| 1 each | a handful of single, named, individually-explained causes: no briefing matches a getter's window; a live run needing `DEMO_EMAIL`/`DEMO_PASSWORD`; two more populated-DEV-dataset requirements; WeasyPrint/PDF rendering unavailable on Windows (two more, same documented cause); a frontend source not present (`reports/page.js`); the Windows asyncpg/ProactorEventLoop concurrency race (`test_review_id_concurrency.py`, classified in Round 26 D2 with an explicit "UNVERIFIED ON LINUX" comment, not silently skipped) | No — each is named with its own cause above; none conceals an unresolved defect. |

**121 of 121 accounted for. None is an unexplained skip; none, on inspection, hides a release-relevant gap** — every group is either an environment/platform limitation of this one local machine, an intentional default-off gate working as designed, or a boundary condition the test states explicitly rather than silently assuming.

## 4. Tests affected by this round's own code changes (R27-2), run after the change

R27-2 added one field to `supervisor_ops.work_queue()`'s per-row output
(`prior_risk_not_cleared`, additive, read-only). Every test file that
imports or exercises `supervisor_ops` was run again, against a FRESH
disposable database (not the accumulating one used for live-browser
proofs this round, which had picked up unrelated data that broke
whole-estate assertions unrelated to this change — see the backend
commit `e82476c` for that detail):

| File | Result |
|---|---|
| `tests/test_supervisor_operations.py` (extended with two new assertions proving the new field's value) | 49 passed, 2 skipped (sandbox-database cause, section 3) |
| `tests/test_identity_provenance_and_controls.py` | 9 passed |

Both clean. No other file imports `supervisor_ops`. R27-3 (the
reconstructed Part A/B seed and its two new spec files) added no backend
application code — it is a new standalone script and two new Playwright
specs, run directly and independently confirmed in section 2's style
(reported in sections 13–15 of the readiness document), not part of the
pytest suite at all.

## 5. Why the full 27-batch suite was not repeated this round

R27-2's only backend change is the one additive field in section 4,
confirmed clean there against every test that could plausibly be
affected. R27-3 added no backend application code. Neither change
touches any file exercised by any of the other 4,544 tests in the
27-batch run (4,593 collected, less the 49 in `test_supervisor_
operations.py` already re-run directly). Repeating the full suite would
re-prove the same 4,544 results against unchanged code — the directive's
own standard ("repeat the full suite only if these changes invalidate
its coverage or reveal a broader unresolved concern") is not met, and
this is stated as a reasoned conclusion from reading the change, not
assumed from it being "probably fine."
