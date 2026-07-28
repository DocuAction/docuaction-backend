# CI/CD Review

> Read-only review of GitHub Actions, deploy mechanism, and release governance. Backend and frontend are **separate git repos**, each with its own `.github/`. Paths absolute.

## Workflow inventory — security-scan only, no build/test/deploy

Six workflows total (three per repo). **Every one is a scanner. There is no build, test, or deploy workflow anywhere.**

| Repo | Workflow | Does | Triggers | Blocking? |
|---|---|---|---|---|
| backend | `.github/workflows/codeql.yml` | CodeQL SAST (python, autobuild) | push+PR→main, weekly `0 8 * * 1` | advisory |
| backend | `.github/workflows/security-scan.yml` | Bandit + `pip-audit` + CycloneDX SBOM | push+PR→main, weekly `0 6 * * 1` | **`|| true` — non-blocking** |
| backend | `.github/workflows/dependency-review.yml` | `dependency-review-action`, `fail-on-severity: high` | `pull_request` | **blocking (high)** |
| frontend | `.github/workflows/codeql.yml` | CodeQL (javascript, `npm ci`+`npm run build`) | push+PR→main, weekly | advisory |
| frontend | `.github/workflows/security-scan.yml` | `npm audit` + ESLint(JSON) + SBOM | push+PR→main, weekly | **`|| true` — non-blocking** |
| frontend | `.github/workflows/dependency-review.yml` | identical to backend | `pull_request` | blocking (high) |

Plus **Dependabot** in both repos (`.github/dependabot.yml` — weekly pip + github-actions, grouped minor/patch, reviewer `imran-agt`).

**Assessment:** the *security-scanning* layer is genuinely good for a solo/small-team project (SAST + dependency audit + SBOM + Dependabot, on push and weekly). But `security-scan.yml` steps are all `|| true`, so Bandit/pip-audit findings **never fail the build** — they're report-only artifacts. Only `dependency-review` (PR-time, high-severity) can actually block a merge.

## Automated tests — NONE in CI

- No test job in any of the six workflows.
- Backend `tests/` directory is **empty**; exactly **1** `test_*.py` exists in the whole backend tree (corroborates Part 2's ~1.4/10 test-coverage finding).
- The PR template asks contributors to confirm *"All tests pass and CI is green"* — but **no CI runs tests**, so the checkbox is unenforced ceremony.

## Deploy pipeline — fully manual / click-ops

There is **no CD**. Deployment is documented manual procedure:
- **Backend:** operators build a zip locally (Linux cp312 wheels into `pydeps/`, set `PYTHONPATH`), then `az webapp deploy --type zip`. Oryx build disabled (`SCM_DO_BUILD_DURING_DEPLOYMENT=false`, `ENABLE_ORYX_BUILD=false`). Startup `python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker` (`--workers 4 --timeout 120` per the deploy guide).
- **Frontend:** SWA CLI, **no linked GitHub repo** (`staticWebApp.bicep`: *"repositoryUrl / branch intentionally left unset"*). So even the SWA GitHub-integration auto-deploy is not used.
- **IaC is not applied by any pipeline** — the Bicep under `backend/infra/` is documentation/drift-review, run manually with `az deployment` if at all → **config drift risk** (no continuous reconciliation).

## Rollback — documented, but not provisioned

`docs/deployment/rollback-procedures.md` is thorough (slot-swap, previous-zip redeploy, Alembic downgrade cautions, Postgres PITR). **But:**
- Slot-swap is qualified *"preferred if configured"* — and `appService.bicep` provisions **no deployment slot** (single production site). So slot-swap rollback is **not actually available**.
- Previous-zip rollback depends on operators **manually retaining** a known-good `deploy.zip` + checksum — there is **no artifact registry**.
- Net: rollback is a manual discipline, not an automated capability.

## Container strategy — present but unused for Azure

`backend/Dockerfile` (python:3.12-slim, ffmpeg, `uvicorn ... ${PORT:-8080}`) + `.dockerignore` exist — a **legacy/local** container (the `${PORT}`/8080 + Railway lineage). Azure runs the **Oryx zip path** (`linuxFxVersion: PYTHON|3.12`, gunicorn), **not** the container. No frontend Dockerfile, no compose, no registry.

## Branch protection & review — partial, single-owner

- `backend/.github/CODEOWNERS`: `* @imran-agt`, comment claims one-owner review required (cites NIST SA-15).
- Strong `pull_request_template.md` (security-impact + PHI/PII/CUI checklist, conventional commits) and `ISSUE_TEMPLATE/` (incl. `security_vulnerability`).
- **No ruleset JSON** in either `.github/` → live branch-protection settings are **not in-repo and cannot be verified from files** (flag for live-console check).
- **Segregation-of-duties gap:** single CODEOWNER (`imran-agt`) can author *and* be the sole approver — no independent review guarantee (bus factor).

## CI/CD maturity

**Level 1–2 of 5 (Scanned but not automated).** Security scanning and dependency governance are above-average for the team size; **build, test, and deploy automation are absent**. The single largest CI/CD lever is a **CD pipeline that runs tests → builds artifact → deploys to a slot → swaps**, which simultaneously fixes the no-tests-in-CI, no-artifact-registry, and no-slot-rollback gaps.

### Priority CI/CD recommendations
| # | Action | Closes | Effort |
|---|---|---|---|
| 1 | Add a **test job** (pytest) gating merge to main | no-tests-in-CI | 1–2d (once tests exist) |
| 2 | Add a **CD workflow**: build zip → publish as workflow artifact → `az webapp deploy` to a **staging slot** → swap | manual deploy, no artifact registry, no slot rollback | 3–5d |
| 3 | Provision an App Service **deployment slot** in `appService.bicep` | enables slot-swap rollback | 0.5d |
| 4 | Make `security-scan.yml` **blocking** on Bandit high/critical (drop `|| true` on the gate) | report-only SAST | 0.5d |
| 5 | Add a **second reviewer**/CODEOWNER or a ruleset requiring non-author approval | segregation of duties | 0.5d |
| 6 | Run **Bicep in a pipeline** (`what-if` on PR, apply on main) | IaC drift | 2–3d |
