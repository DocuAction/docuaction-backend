# Operator prerequisite checklist — one concrete list (2026-10-04, R27-6)

Every item below is prepared and ready to hand to an operator. None of it
has been executed by this session — publication, deployment, account
creation on DEV, and flag changes on DEV are explicitly outside this
session's authorization (local code, local commits, disposable databases
and synthetic browser testing only).

## 1. Final branches and full SHAs

- Backend `feature/preflight-exceptions`, HEAD `a6beb500053a7adf2bfe90a853d65ba40eda520f`.
- Frontend `feature/preflight-exceptions`, HEAD `de961ed973e27b80f96ebbd08d7ada1317f99096`.
- Neither branch has been pushed this session. Independent review and CI
  both need the branches pushed first — an operator action, not performed
  here.

## 2. CI required on the integrated candidate

- The project's `.github/workflows/pr-tests.yml` must run green on these
  exact SHAs once pushed, including its own `journey-live` job (the
  dedicated job that starts a real server and runs the six tests named in
  `docs/review/REGRESSION-MANIFEST-2026-10-04.md` section 2) — this
  session's local reproduction of that job passing is evidence, not a
  substitute for CI itself running.
- No CI run has been triggered this round (nothing was pushed).

## 3. Migration order and compatibility — CORRECTED 2026-10-04 (R29-1)

**This section previously said "no new migration was added this
round," comparing against the wrong reference point (Round 25's own
starting point, not this PR's actual base against which an independent
reviewer compares). That was wrong and is corrected here.** Full
account: [`docs/review/MIGRATION-INVENTORY-2026-10-04.md`](./MIGRATION-INVENTORY-2026-10-04.md).

- **Against this PR's real base** (`ea92ea5`, PR #110/#66's head —
  Alembic head `20261003_preflight_shadow`), this candidate adds
  **three** new revisions, reaching head `20261004_recheck_jobs`:
  two CHECK-constraint widenings
  (`20261004_preflight_exec_held`, `20261004_stage_event_preflight`,
  neither adding or removing a column or table) and one that creates
  two new tables (`20261004_recheck_jobs`:
  `rce_recheck_job`/`rce_recheck_item`, with a `SELECT, INSERT,
  UPDATE`-only grant, never `DELETE`, to the app role).
- Standard order, migration role `docuaction_owner`, confirmed command:
  `DB_APP_ROLE=docuaction_app python -m alembic upgrade head` (the
  `DB_APP_ROLE` guard is enforced by migration `20260828_area1_privilege_
  correction` AND by `20261004_recheck_jobs` itself, which refuses to
  run without it — rediscovered this round, recorded in
  `tests/fixtures/seeded/LIVE-PARTB-RECIPE-2026-10-04.md`).
- **Compatibility and rollback, evidenced this round, not assumed**:
  all three new revisions upgrade and downgrade cleanly against an
  empty throwaway database
  (`tests/test_recheck_and_preflight_migrations_2026_10_04.py`, new
  this round). Downgrade WAS exercised, with a real, demonstrated
  asymmetry: `recheck_jobs` explicitly refuses its own downgrade once a
  row exists (a named precondition error); the two CHECK-widening
  migrations do not, and fail with a raw Postgres `CheckViolation`
  instead if dependent data exists. No existing column, table, or
  grant outside the two new tables is touched by any of the three.

## 4. DEV deployment procedure and rollback triggers

- Per this project's established pattern (prior rounds' governed-deploy
  recipe, unchanged this round): a governed GitHub Actions run with
  `apply_migrations=true` and the production/DEV handshake, DB-phase
  first (migration only, zero-DDL check), then the app-deploy phase.
- Rollback triggers, per the same established pattern: the deploy
  workflow's own health-check step and `gov-verify` (governance
  integrity check) failing auto-rolls back to the last known-good
  revision. No change to this trigger or procedure was made or proposed
  this round.
- Not performed this round: no deployment, no migration apply, no
  firewall or account change on any shared environment.

## 5. Exact flags required for the integrated QA scope

Compiled from this round's own setup (each one verified by actually
needing it, not assumed):

| Flag | Required value | Why |
|---|---|---|
| `ENTITY_RESOLVER_SOURCE` | `db` | Required on BOTH the process that seeds verification data AND the live server process serving browser traffic — they are separate processes and the mock default on either one silently empties every completeness/recheck/evidence result. Known since Round 25; re-confirmed the hard way this round when only the seeding script had it set. |
| `ENABLE_CONTROLLED_RECHECKS` | `true` | Without it, the recheck panel renders as if recheck support were off entirely for every job, including a genuinely-requested one — discovered this round rebuilding the Part A/B proof. |
| `ENABLE_IQVIA_SOURCES` | `true` | Required for the IQVIA upload/match journeys (SUN-08/09, INT-21/22). |
| `ALLOWED_ORIGINS` | must include the deployed frontend's own origin | Otherwise every browser request fails CORS preflight with a generic "Cannot reach server" message unrelated to whether the server is actually reachable — hit twice this round before being documented. |
| `SEED_RULES_V4` | inactive (leave as-is) | Unapproved; must stay inactive per every prior round's explicit instruction. |
| `ENFORCE_COMPLETE_EXCLUSION_SCREENING` | inactive (leave as-is) | The proposed, not-yet-approved policy (P1) — see section 7's decision table. Turning this on is a policy decision, not a deployment step. |

No other flag used by this round's work differs from its documented
default.

## 6. Accounts: Admin, Program Manager, Analyst, a SECOND distinct QA
   Lead, Viewer

**Supported procedure, verified from code** (`app/api/admin_users.py`):
one existing Admin account calls `POST /api/admin/users` (`create_user`,
behind `require_admin`) once per account needed. This is the single
supported direct-provisioning path for this QA campaign — no self-
registration-then-approval flow is needed or used, so there is no
self-approval question for THESE five accounts (the self-register/
approve path exists for other use and is untouched here).

- **No direct SQL.** Every account this round and in every prior round
  was created either through this endpoint or through the test suite's
  own direct-ORM synthetic-fixture pattern (disposable databases only,
  never a shared one) — never a hand-written `INSERT` against a real
  environment.
- **No unnecessary admin privilege.** Only the ONE account doing the
  provisioning needs to be Admin; the five accounts being provisioned
  get exactly the role the campaign needs (program_manager, reviewer,
  qalead ×2, viewer) via `PATCH /api/admin/users/{id}/role` or the `role`
  field on creation — never admin.
- **Two distinct QA Leads, specifically.** INT-45 (QA return) and the
  Part A/B journey G both require the person approving/returning to be
  a DIFFERENT account from whoever made the determination or requested
  the recheck — the server itself refuses a self-approval on these
  specific actions (`qa_gate`, `rechecks.approve_recheck`); two real,
  separate QA Lead accounts are needed to exercise the refusal and the
  approval both.
- Not performed this round: no account was created on DEV or any shared
  environment. Every account used this round is a disposable, synthetic
  `*@synthetic-test.docuaction.invalid` identity in a local, disposable
  database.

## 7. Isolated synthetic fixture setup

- The recipe used throughout this round and documented in full:
  `tests/fixtures/seeded/LIVE-PARTB-RECIPE-2026-10-04.md` (backend repo).
  Disposable Postgres database, `docuaction_owner`/`docuaction_app` role
  split, migrated to head, seeded via a script that goes through the
  real application pipeline (never a bare `INSERT` of a finished state).
- An engineer repeating this on DEV's own isolated fixture database
  (never DEV's live data) would follow the same recipe, substituting the
  DEV database connection details an operator provides.

## 8. Deployed-version confirmation

- Same established check as every prior round: compare the page footer /
  Overview panel's "Build" SHA against the SHA an operator states was
  deployed. This round found the local health endpoint reports
  `"git_sha": "unknown"` when not built with that metadata baked in —
  confirm with the operator which deploy step sets it, since an
  `"unknown"` build SHA on the LIVE page would make this confirmation
  step impossible to perform from the browser alone.

## 9. Ordered smoke/browser checks (post-deployment)

In the order a reviewer should run them, each already proven locally
this round or last and ready to repeat against DEV once deployed, DEV
accounts exist, and the flags in section 5 are confirmed set:

1. Sign in as each of the five roles; confirm the menu entries each role
   is offered (`live-nav-audit.spec.mjs`).
2. Viewer: open the deliveries list from the menu immediately after
   sign-in, then a delivery detail page immediately after that — confirm
   no rate limit (INT-40/43).
3. Program Manager: register a small synthetic delivery end to end
   (INT-44 / Journey A).
4. Analyst: open the delivery's Overview/Exceptions tabs — source
   readiness and its four-column findings (INT-16/19).
5. Analyst: open the Verification tab — completeness split, SAM.gov
   named, request a recheck (INT-23/28).
6. QA Lead #1: approve and run that recheck (INT-30).
7. Analyst: make a determination; QA Lead #2 (NOT QA Lead #1, and not
   the analyst): return it; confirm the analyst's own event is still
   present, not overwritten (INT-45).
8. Analyst or QA Lead: open Supervisor Operations, tick the exclusion-
   candidate filter, open a flagged case if one exists (INT-41/47).
9. Analyst: generate a delivery report and download its CSV (INT-46).
10. Any role: confirm no page visited in steps 1–9 ever showed raw JSON
    (checked continuously by every spec above, not a separate pass).

Each step above is "go/no-go" for the next: do not proceed past a
failing step without logging it as a defect first.
