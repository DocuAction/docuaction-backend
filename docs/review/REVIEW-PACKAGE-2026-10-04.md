# DocuAction TEFCA ARC — Independent Review Package
**Prepared**: 2026-10-04 (Round 20) — **updated 2026-10-04 (Round 21:
containment fix + publication)** — **for independent review, not
self-certified**

> This package is built by the same session that wrote the code. Everything
> in it is a technical self-check, not an independent approval. The
> "INDEPENDENT REVIEW" gate below is marked **NOT STARTED** until a separate
> reviewer records a decision.

## 1. Exact candidate under review

**Application-code history on backend, most recent first** (each superseded
the one before it on PR #110's branch, `feat/reporting-architecture-2026-10-01`):

| SHA | What changed | CI (at time of this writing) |
|---|---|---|
| `2642525` (current HEAD) | Docs-only correction to two overclaiming comments in `upload_security.py` + one new TOCTOU-race test. **Application code behavior is unchanged from `02002c1`** — see §2.1a for the exact diff and why this is still an application-code commit (touches `app/` and `tests/`), not the documentation-only commit described in §8 below. | In progress — see §8 for the bounded check taken at publication time; this package does not claim a result it has not observed. |
| `02002c1` | Independent-review containment fix: `safe_existing_path()` rejects symlinks; new `open_no_follow()`; both IQVIA read call sites switched to it. Supersedes `896f47f`. | CodeQL confirmed pass (0 alerts) before the next commit landed; remaining jobs (pytest/fixture/isolation-postgres/journey-live/etc.) were superseded by `2642525`'s push before reaching a terminal result — their LAST observed state is in ledger Round 21, not re-asserted here as pass. |
| `896f47f` | Prior candidate HEAD (Round 20 package). Had the containment gap below. | 10/10 (stale — superseded) |

| | Backend (PR #110) | Frontend (PR #66) |
|---|---|---|
| Base (`main`) | `a6bf241bed0536533bacddd2adb3712d906762f7` | `84deeea9b87596cea999b342fef78be11ef38c88` |
| Merged? | No | No |
| Deployed? | No — `main` on both repos is identical to DEV's current live build, confirmed via `/health` and `build-info.json` |

**`main` on both repos is byte-identical to what DEV is currently running.**
This review package's diff is therefore also the exact diff this candidate
would introduce to DEV on deployment — not a stale comparison.

**Frontend is unchanged this round**: `48031780ad4ce0b84126ae7187f49d1d1a8ee87b`,
9 commits ahead of base, all checks pass (confirmed Round 16) — nothing in
this round's containment fix touches frontend code.

## 2. Changes grouped by area

### 2.1 IQVIA (new capability — largest single area)
**Files**: `app/tefca_registry/rce/iqvia_routes.py` (new, 665 lines),
`iqvia_import.py` (new, 401), `iqvia_match.py` (new, 197),
`iqvia_upload_jobs.py` (new, 248), `iqvia_upload_models.py` (new, 191),
`iqvia_import_scheduler.py` (new, 136), `snapshot_models.py` (new, 84);
migrations `20261002_iqvia_observations.py`, `20261003_iqvia_upload_durability.py`;
frontend `iqvia/page.js` (new), `IQVIA bulk-import browser journey` commit
`3c1a0af`, `63870a0`.

**What it is**: a chunked-upload + durable-job path for staging large
external IQVIA HCO/HCP/affiliation extracts, maker/checker approval
(reviewer stages, qalead approves — enforced server-side), and matching
against the registry with an explicit, honest "matching unavailable"
state when relationship data is absent (never a false match, never a
silent empty success).

**Security finding and fix (reviewer attention required)**: the stage
route (`POST /sources/{source}/stage`, reviewer role) originally accepted
a client-supplied `file_path` string and opened it directly — CodeQL
flagged 2 high-severity `py/path-injection` alerts. Fixed in
`app/core/upload_security.py`'s new `safe_existing_path()`: each allowed
directory is listed (`os.listdir`, no tainted input), and the returned
path is built only from a listed entry found to equal the requested name
— the client's string is used in an `==` comparison only, never passed to
a filesystem call in any form. Took three iterations to find a shape
CodeQL's taint tracker accepted (ledger Rounds 17–19 document all three
attempts and why the first two, while functionally correct, weren't
recognized). **0 open CodeQL alerts on this candidate as of this
package.**

#### 2.1a Containment gap found by independent review, and the fix (`896f47f` → `02002c1` → `2642525`)

An independent reviewer, working from this same package at `896f47f`,
reproduced a real gap the directory-listing fix above did not close: the
accepted entry's `Path.is_file()` call **follows symlinks**. A symlink
placed inside an allowed directory, named to match a requested filename,
and pointing at a file OUTSIDE every allowed directory would pass that
check and be returned as "safe" — then opened by the caller, outside
confinement. Verified with synthetic files before fixing anything (not
asserted from reading the code alone).

**Fix, `02002c1`** — two changes to `safe_existing_path()` plus a new
helper:
- `os.scandir()` (not `os.listdir()`) + `DirEntry.is_symlink()` rejects
  any symlink entry by name, using the same syscall that already knows
  the entry's type — no separate stat a symlink swap could race.
- New `open_no_follow()` (`os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`)
  closes the narrow window between that check and a caller's actual read
  (the file could be replaced with a symlink in between). Both IQVIA read
  call sites downstream of `safe_existing_path` — `file_sha256()`'s
  streamed hash and `_import_csv()`'s CSV read, both in
  `iqvia_import.py` — now use it instead of the `open()` builtin. A third
  `open()` call in `iqvia_routes.py` (the chunked-upload chunk-WRITE to a
  server-generated, UUID-named `temp_path`) was reviewed and intentionally
  left unchanged: it is never reached through `safe_existing_path`'s
  client-controlled `file_path`, and closing it would require a
  *different* vulnerability class (prior arbitrary filesystem write) to
  already exist — out of scope for this fix, stated here rather than
  silently skipped.

**Correction, `2642525`** — re-review of `02002c1`'s own comments found
two overclaims, fixed with no functional code change:
- The `os.path.commonpath` containment check does **not** detect a
  hardlink or a bind mount, despite what the first version of the
  docstring said. Neither has a reparse point for `Path.resolve()` to
  follow (a bind mount is transparent to every syscall used here,
  including `is_symlink()`), so both resolve to a path string that is
  still, correctly, "inside" the allowed directory — this check would not
  flag either one. What it actually adds, for this specific lookup
  (`entry.name` has no path separators, and the base directory is already
  fully resolved before the scan), is nothing beyond what `is_symlink()`
  already catches; it is cheap defense-in-depth, not a second detector
  with independent coverage. The docstring now says this plainly.
- `open_no_follow()`'s `O_NOFOLLOW` only guards the **final** path
  component — an intermediate directory in the chain above it is still
  followed like any other `open()`. The function's docstring now states
  this explicitly, and names what actually has to hold instead: the
  deployment's own trust boundary on who can write into an allowed
  directory at all (§2.1b).

**Test evidence**: `tests/test_upload_security_safe_existing_path.py`
(new) — three categories, as requested: a normal file is returned; an
outside-target symlink is refused (400, "symlinks are not accepted"); and
— the actual TOCTOU race, not just a pre-existing symlink —
`test_a_file_swapped_for_a_symlink_after_validation_is_refused_at_open_time`
validates a real file via `safe_existing_path`, THEN replaces that exact
path with a symlink to an outside file, THEN confirms `open_no_follow`
refuses it. Local (Windows, no Developer Mode): 8 passed, 4 skipped —
every symlink-dependent case skips cleanly rather than faking a pass; all
four run for real on CI's Linux runners, where creating a symlink needs
no elevated privilege. Also re-ran all six `journey-live` tests
end-to-end (real bound server, disposable PostgreSQL at the candidate's
exact migration head) to confirm the rewritten functions do not break the
real IQVIA staging/import flow: 6/6 passed.

#### 2.1b Deployment-design assessment (requested by review; not previously stated)

This app runs as a single non-root user per its own `Dockerfile` (`USER
appuser`, owning its whole tree) — no co-tenant process shares this
container's filesystem namespace, so a symlink cannot be planted here by
another process *inside* the container. The actual trust boundary this
fix defends is narrower and specific: `settings.IQVIA_IMPORT_DIR` is an
operator-local-path drop directory — content lands there from OUTSIDE the
running app (a human or a deploy/ops script with direct volume access),
which is not necessarily the same trust level as the `reviewer`-role API
caller who later names a file in it over HTTP. That gap — a lower-trust
drop-directory writer vs. a higher-trust API caller — is exactly what the
independent review's finding exploited, and is what `safe_existing_path`'s
checks are scoped to. It does not, and was never meant to, defend against
a second, already-present in-process attacker with arbitrary filesystem
write — that would be a different, already-worse vulnerability. **This
assessment is read from the repository's own `Dockerfile`, not inferred
or assumed; the actual host/volume permissions in the deployed Azure
environment have not been independently re-confirmed this round** — that
confirmation, if wanted, is an infrastructure-level check outside this
package's scope.

**Test evidence**: `test_journey_iqvia_live_2026_10_04.py` (new
`journey-live` CI job, real bound HTTP server, not just in-process ASGI) —
passed on `896f47f` and re-confirmed locally against `02002c1`/`2642525`
(§2.1a). Stages a snapshot, confirms analyst-approve is
refused (maker/checker), qalead-approve succeeds, exercises the
affiliation-unavailable path.

**Known limitation**: the completed 6,817,131-row real IQVIA import
(Release 1, prior round) is NOT re-exercised by anything in this candidate
or its tests — explicitly out of scope, never repeated.

### 2.2 SAM / manual-review evidence (correctness fixes)
**Files**: `app/Tefca/validation_engine.py`, `app/Tefca/connectors.py`,
`app/Tefca/evidence_assembly.py`.

**What changed, with before/after**:
- **Practice-vs-mailing address confusion (safeguard 5)**: previously, if
  NPPES had no `LOCATION`-purpose address, the code fell back to
  `nppes_addrs[0]` — frequently a MAILING address (lockbox/corporate
  office) — and compared it against the submitted PRACTICE address,
  manufacturing `ADDRESS_STATE_CONFLICT`/format findings from two
  addresses that were never meant to match. Now: no `LOCATION` row means
  `"NOT_COMPARED"`, explicitly, never a fabricated mismatch.
- **Manual review SAM evidence**: manual single-entity review previously
  never queried SAM at all (proven by a new test,
  `test_sam_never_queried_manual_review` — now reversed: manual review
  consumes PERSISTED SAM/LEIE exclusion evidence, and an honestly-clear
  exclusion-list name screen is treated as "clear," not as an automatic
  disqualifier).
- **8 SAM matching safeguards audited** (`scripts/exception_inventory.py`,
  `SAM_MANUAL_REVIEW_ASYMMETRY.md`-origin work) — each verified against
  real persisted evidence, not asserted.

**Test evidence**: `test_sam_e2e_delivery_path.py` (real pipeline, real
Postgres, `isolation-postgres` CI job — pass), `test_sam_manual_review_asymmetry.py`.

### 2.3 QA approval (the single most consequential correctness fix)
**File**: `app/tefca_registry/qa_gate.py` (commit `732b47b`).

**Before**: `QA_APPROVE` refused whenever
`TefcaRegEntity.verification_status == "in_review"` — but
`arc_pipeline.py` sets that status for **every** classification bucket
other than B1, not only when a genuine blocking finding exists. This made
independent QA approval structurally unreachable for every B2/B3/B4
entity, not just the narrower case the check was meant to cover.

**After**: checks `post_promotion_verification.has_unresolved_blocking_finding()`
directly — the real, specific condition. An entity merely tier-routed to
B2/B3/B4 with no open finding can now be approved (that tier **is** the
system's answer, which is exactly what independent QA exists to confirm);
an entity with a genuine open finding is still correctly refused, with
the refusal naming the finding, not a bucket.

**Test evidence bound to exact commits**: `test_qa_approval_route_level_2026_10_03.py`
(route-level, real HTTP via ASGI transport, two real entities — one clean,
one with a REAL `post_promotion_verification.record_finding` call against
it) — commit `83c2e68`, passes in `journey-live` CI. Self-approval
(segregation-of-duties) denial independently confirmed this session via a
LIVE DEV browser check (Round 13) against a real in-flight case, not just
a test.

### 2.4 Reporting (async jobs, PDF/UA accessibility, CSV reconciliation)
**Files**: `app/reports/routes.py` (+237), `app/reports/data/delivery_processing_data.py` (+129),
`app/reports/engine/pdf_engine.py` (new, 1,017 lines), `export_runner.py` (new),
`verification_drilldown.py` (new, 315), templates + `agt_design_system.css`.

**What changed**:
- Report generation moved to a durable background job (`202` + poll),
  reusing `report_export_jobs` — idempotent by content-hash identity,
  verified by direct code read (ledger R9-MT) and by the `journey-live`
  reporting test generating, polling, and downloading for real.
- New PDF/UA post-processing (`pdf_engine.py`): tags chrome/margin bands
  and decorative CSS fills as artifacts so screen readers don't narrate
  page furniture — closed 3 veraPDF-identified defects, validated
  106/106 rules on the application-downloaded artifact (commits `7b3671d`,
  `5a50123`).
- CSV exports now carry a consistent-snapshot row count and a registered
  manifest on every per-delivery route (commit `df16213`); formula-
  injection escaping verified by the `journey-live` reporting test
  directly inspecting downloaded CSV bytes.
- Verification-coverage drill-down: per-(source, outcome) entity list
  behind each coverage total, both backend (`verification_drilldown.py`,
  new) and frontend (`VerificationTab`, commit `5ed9387`).

**New runtime dependencies this introduced** (reviewer should confirm
these are acceptable to add): `pikepdf==10.16.0`, `beautifulsoup4==4.15.0`,
`pypdf==6.19.0` — all genuinely imported by `pdf_engine.py`, none with
known CVEs as of this check (`dependency-review` passes).

### 2.5 Preflight + shadow reassessment (new, local-only scope)
**Files**: `app/tefca_registry/rce/preflight.py` (new, 603),
`preflight_shadow_models.py` (new, 301), `preflight_shadow_routes.py`
(new, 219), `shadow_reassessment.py` (new, 676), `analyst_workspace.py`
(new, 436); migration `20261003_preflight_shadow_workspace.py`.

**What it is**: admin dry-run planning (schema/identifier/conditional-
blank/missing-context checks before final classification) and a shadow-
reassessment pilot comparing the ACTUAL preserved `SEED_RULES_V4`
candidate against production rules — explicitly **not activated**, local-
only scope, consistent with the standing "keep SEED_RULES_V4 inactive"
boundary. The shadow comparison test (`fdeaaed`) uses the real preserved
V4 file, not a mock.

**Security/privilege design**: the seven new tables are append-only-by-
grant (SELECT+INSERT only to the app role, matching the September
snapshot's established pattern) — verified by reading each migration's
own grant statements, not assumed (ledger Round 17).

### 2.6 Security and CI infrastructure (this session's own work, Rounds 14–19)
- 2 CodeQL path-injection alerts (IQVIA stage route) — fixed, 0 open.
- A real **production migration-safety gap**: `scripts/prod_legacy_convergence.py`'s
  `MANAGED_CHAIN_CREATES`/`AREA1_OWNER_TABLES` constants were stale for
  this candidate's 10 new chain-created tables. The REAL managed-migration
  gate (`missing_candidates` check, line ~626 of that script) would have
  correctly **refused** this candidate's actual deployment to PROD had
  this not been caught and fixed — not a test-only artifact.
- A new disposable CI job (`journey-live`) for six database/server-
  dependent tests that previously had no CI coverage at all (not even a
  clean skip — raw `ConnectionRefusedError`). Each test's own real root
  cause was found and fixed (a missing monkeypatch, missing fixture
  accounts, a hardcoded intake id from a personal environment) rather than
  skipped. Full account: ledger Rounds 15–19.
- Non-gating frontend `npm audit` step was actually gating on a pre-
  existing, unrelated, dev-only finding (`braces` via `tailwindcss`) —
  fixed with a genuine `|| true`.

### 2.7 Migrations — full chain and compatibility
Four new migrations, linear chain, single head (`alembic heads` returns
exactly one): `20261001_report_generation_jobs` → `20261002_iqvia_observations`
→ `20261003_iqvia_upload_durability` → `20261003_preflight_shadow` (local
alias `20261003_preflight_shadow_workspace`). All four tested via a REAL
`alembic upgrade head` against a disposable, freshly-initialized database
this session (not just reviewed as text) — clean run, no errors, 10 new
tables created with correct ownership per the fixed `MANAGED_CHAIN_CREATES`/
`AREA1_OWNER_TABLES` classification (Section 2.6).

**Rollback compatibility**: ledger Round 9 (R9-4) proved the PRIOR
application version starts, reads, and writes correctly against an
upgraded schema from this same baseline (`a6bf241` — unchanged since, so
that proof still applies). Rolling back to the prior code does NOT delete
any data and does NOT reverse the schema — a rollback is a code-level
redeploy only. One caveat carried forward unchanged: rolling back would
**reintroduce** the QA-approval bug (Section 2.3) for any entity that
relies on the fix, since the prior code's bucket-driven check is what the
fix replaced.

## 3. Files requiring the most reviewer attention (shortlist)

| File | Why |
|---|---|
| `app/tefca_registry/qa_gate.py` | The core correctness fix — confirm the new `has_unresolved_blocking_finding()` check is the right condition, not a new gap |
| `app/core/upload_security.py` | Security-critical path-confinement helper; already caught one real gap in independent review (§2.1a, symlink-following) — confirm the current `os.scandir`/`is_symlink`/`open_no_follow` combination is actually airtight against the threat model in §2.1b, not just CodeQL-satisfying |
| `scripts/prod_legacy_convergence.py` | Production migration-safety classification; confirm the 10 new tables' Area-1-vs-app-owned split matches intent, not just what made tests pass |
| `app/tefca_registry/rce/iqvia_routes.py` | New, large, security-sensitive (file staging, maker/checker); confirm role floors match the documented design |
| `app/Tefca/validation_engine.py` | SAM/NPPES address-matching logic; confirm the NOT_COMPARED semantics don't silently weaken a real check |
| `alembic/versions/20261003_preflight_shadow_workspace.py` | Largest new migration; confirm append-only grants are correctly scoped |

## 4. Test evidence summary, bound to exact commits/SHAs

| Area | Evidence | Where |
|---|---|---|
| Symlink containment (normal file / outside-target symlink / TOCTOU swap) | `tests/test_upload_security_safe_existing_path.py` | Local: 8 passed / 4 skipped (Windows, no Developer Mode); CI Linux runners execute all four symlink-dependent cases for real — see §8 for this round's bounded CI check |
| QA approval fix | `test_qa_approval_route_level_2026_10_03.py` | `journey-live` CI, `896f47f`, PASS |
| Self-approval denial | Live DEV browser check, real in-flight case | Ledger Round 13 (R13), build `a6bf241` (pre-candidate, confirms the CONTROL design, not this candidate's code) |
| IQVIA journey | `test_journey_iqvia_live_2026_10_04.py` | `journey-live` CI, `896f47f`, PASS |
| SAM pipeline | `test_sam_e2e_delivery_path.py` | `isolation-postgres` CI, `896f47f`, PASS |
| Reporting idempotency | Direct code read + `journey-live` reporting test | Ledger R9-MT + `896f47f`, PASS |
| PDF/UA accessibility | veraPDF CLI, 106/106 rules | Commit `7b3671d`/`5a50123`, local verification (not re-run this round) |
| Convergence/migration safety | `test_prod_convergence_integration.py` + `test_prod_managed_migration_integration.py` | `fixture` CI job, `896f47f`, PASS (9/9) |
| Full test suite | `tests/` (no-DB) + isolation-postgres (25) + journey-live (6) | `896f47f`, all PASS |
| CodeQL / dependency-review | 0 open alerts | `896f47f`, PASS |
| Frontend full suite | `48031780` | All checks PASS (Round 16) |

**Not covered by any automated test this round** (disclosed, not hidden):
live-browser UI click-through of the candidate itself (DEV is still on
`a6bf241`, not this candidate — the Sunday workbook's SUN-xx cases are
written for this but cannot execute until deployment); the six DEV smoke
checks (ledger R9-6); a genuine SAM-ambiguous/exclusion scenario (needs
seeded evidence, not yet prepared).

## 5. Known limitations and unresolved acceptance items (carried forward, unchanged by this round)

- **"1,298" interpretation**: still unresolved by design; not this
  candidate's call.
- **SEED_RULES_V4**: still inactive; the shadow-reassessment work compares
  against it but does not activate it.
- **Current-code performance at full scale** (24,589+/100M-record
  readiness): not measured this round; prior rounds' measurements at
  n=2,500/24,589 stand as the only evidence that exists.
- **Accessibility evidence** is scoped to what was actually tested
  (automated PDF/UA via veraPDF; a manual keyboard/focus walk of the
  changed ARC screens has NOT been performed against this exact
  candidate — SUN-15 in the Sunday workbook covers this, not yet run).
- **6 Blocked/Partial items** from the September QA closure package (prior
  rounds) are unrelated to this candidate and not re-litigated here.
- **Symlink-containment tests run unverified on this session's own
  platform** (Windows, no Developer Mode privilege) — the four
  symlink-dependent cases in `test_upload_security_safe_existing_path.py`
  skip cleanly here rather than execute; CI's Linux runners are the only
  environment where this round's fix has actually been exercised against
  a real symlink. See §8 for the specific CI run this was checked
  against.
- **Deployment-permission assumption (§2.1b) is read from the repo's own
  `Dockerfile`, not independently re-confirmed against the deployed Azure
  environment's actual filesystem/volume permissions this round** — flagged
  as a real gap for the reviewer, not asserted as closed.

## 6. Remaining security/workflow concerns

- **None open on this candidate** as of this package (0 CodeQL alerts,
  dependency-review passes on both repos). The one frontend Dependabot
  finding (`braces` via `tailwindcss`, low/high depending on scan,
  dev-dependency only) is pre-existing, unrelated to this candidate's
  diff, and does not ship to users.
- **Workflow-trigger safety**: re-confirmed this round (Section 7) that
  publishing this candidate triggers CI only — no deploy, no migration
  dispatch, no firewall change is reachable from a push to the PR branch.

## 7. Automatic workflow triggers (re-confirmed before any merge is proposed)

Read directly from every workflow file in both repos (22 total), not
assumed, per Round 11's original analysis and unchanged by anything since:
- Pushing to `feat/reporting-architecture-2026-10-01` (updating PR #110/#66)
  triggers CI only (CodeQL, pytest, fixture, isolation-postgres,
  journey-live, sast, render, previews, analyze, dependency-review on
  backend; equivalent set on frontend).
- **A merge to `main`** would additionally trigger `dev-release.yml`
  (backend) and would NOT by itself deploy — that workflow's own
  migration-gate defaults to STOP (DEV Postgres firewall blocks GitHub
  runners by design) and its deploy step requires `workflow_dispatch`
  with explicit inputs. Frontend's `deploy-frontend.yml` similarly needs a
  tag push or manual dispatch, never triggered by a merge to `main` alone.
- **This means Gate B (merge) does not, by itself, deploy anything** —
  but it does change `main`, which is the thing DEV's own `/health` and
  `build-info.json` currently match exactly. Gate B is still a real,
  separate, consequential action requiring its own explicit approval, not
  because it deploys, but because it changes the base everything else is
  measured against.

## 8. This package's own publication — application-code SHA vs. documentation-only SHA

**The application-code SHA this package's test evidence is bound to is
`2642525`** (backend, `feat/reporting-architecture-2026-10-01`) — the
containment fix (`02002c1`) plus the comment corrections and new TOCTOU
test (`2642525` itself). Bounded CI check taken at the time this section
was written (not a recurring poll): **8 of 10 jobs pass** (CodeQL,
analyze, dependency-review, journey-live, previews, pytest, render,
sast), **2 still running** (`fixture`, `isolation-postgres` — both
DB-backed, the slowest jobs in this suite). This package does not claim
a result for those two beyond "still running" — see PR #110's own checks
for their eventual terminal state.

**This file's own publication is a SEPARATE, later commit** that adds
`docs/review/REVIEW-PACKAGE-2026-10-04.md` to the backend repository so
it is reachable on GitHub (previously it existed only under this
project's `qa-evidence/` directory, which sits outside both git
repositories by design — see `CLAUDE.md` — and 404s when a reviewer
follows a repo-relative link to it from the PR description). That
publication commit is documentation-only: it adds exactly one new file
under `docs/review/`, with **zero changes to any file under `app/`,
`tests/`, `alembic/`, or any other application-code path** — verified by
`git diff --stat` against `2642525` before pushing, not asserted. Its
exact SHA is recorded in the ledger
(`FINAL-ACCEPTANCE-LEDGER-2026-10-03.md`, this round's entry) rather than
inline here, since editing this file to record its own next commit's
hash would move the target after writing it.

**Reviewer navigation**: this file at its current GitHub path is
`docs/review/REVIEW-PACKAGE-2026-10-04.md` on
`feat/reporting-architecture-2026-10-01` —
`https://github.com/DocuAction/docuaction-backend/blob/feat/reporting-architecture-2026-10-01/docs/review/REVIEW-PACKAGE-2026-10-04.md`.
