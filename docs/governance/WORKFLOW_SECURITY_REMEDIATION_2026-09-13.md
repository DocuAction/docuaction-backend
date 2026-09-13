# Workflow Security Remediation (Lane A, 2026-09-13)

Builder remediation record for audit findings AUD-20260913-01, -02 (documentation and recommendation only), -04, -06 (backend part), -15 and -16. Builder session = MAKER. Nothing here is an independent audit result; the independent checker re-verifies against the PR head SHA.

## 1. Script-injection review (4A)

Every `${{ ... }}` expression inside a `run:` block of the 13 backend workflows was classified. After this lane, no `inputs.*`, `secrets.*`, `github.event.*`, `github.head_ref` or `env.*`-of-input expression is expanded inside a shell script; the only expressions left in shell are workflow-defined `env.*` constants (registry names), `steps.*`/`needs.*` outputs derived from `git rev-parse` or Azure CLI output, `vars.*` identifiers, `github.repository`, `github.run_id` and `github.token`.

| Workflow | Expression | Classification | Change |
|---|---|---|---|
| prod-migration.yml | `inputs.handshake_issue` in `gh issue comment` | UNTRUSTED (operator text) | env `HANDSHAKE_ISSUE`, digits-only check, `"$HANDSHAKE_ISSUE"` |
| migration-preflight.yml | `inputs.handshake_issue`, `inputs.expected_current` (two steps) | UNTRUSTED | env vars; digits-only check; revision must exactly match a `revision = "..."` identifier present in `alembic/versions` (see 4B) |
| dev-release.yml | `inputs.handshake_issue` | UNTRUSTED | env var, digits-only check |
| container-release.yml | `inputs.ref` in the run summary | UNTRUSTED (echo only) | env `REF_BUILT` |
| deploy-backend.yml | `inputs.image_tag` (resolve digest, PROD import) | UNTRUSTED | env `IMAGE_TAG`, charset check `[A-Za-z0-9._-]` |
| deploy-backend.yml | `secrets.MIGRATION_DATABASE_URL` on three command lines | SECRET ON ARGV | step `env: DATABASE_URL`, plain `alembic` commands |
| stackhawk-scan.yml | `secrets.HAWK_API_KEY` in `[ -z ... ]`; `secrets.DAST_USER/PASSWORD` in a curl JSON body | SECRET ON ARGV | env vars; JSON built from the environment and piped to curl over stdin |
| container-release.yml | `env.DEV_REGISTRY`, `env.IMAGE_REPO`, `env.DEV_LOGIN_SERVER` | TRUSTED (workflow constants) | none |

## 2. Migration target validation (4B)

Arbitrary Alembic revision selection is not needed: every automated path upgrades to `head` only (`alembic upgrade head` in deploy-backend.yml; `alembic heads` must resolve to exactly one head in migration-preflight.yml). The only operator-supplied revision is `expected_current`, which is a *precondition* (the revision DEV must already be at), never a target. It is now accepted only if it exactly equals one of the revision identifiers parsed from the repository's `alembic/versions/*.py` files. No wildcard, prefix, regex or shell evaluation; unknown values exit non-zero with a safe message before anything executes.

## 3. Secret exposure (4C)

Backend DEV runtime secrets are Key Vault references in App Service settings (verified by name only; values never read). GitHub-side: the two remaining stored credentials are `MIGRATION_DATABASE_URL` (used only by the manual `run_migrations=true` path of deploy-backend.yml) and the DAST scan credentials. Recommendation (human): retire `MIGRATION_DATABASE_URL` in favour of the OIDC handshake path that prod-migration.yml already uses, then delete the secret.

## 4. GITHUB_TOKEN permissions (4D)

Top-level `permissions: contents: read` added to codeql.yml, deploy-backend.yml, pdf-linux.yml, security-nightly.yml and security-scan.yml; jobs that need more (`security-events: write`, `id-token: write`, `issues: write`) already declare it at job level and are unchanged. `id-token: write` exists only on jobs that log in to Azure.

## 5. Third-party action pinning (4E)

All third-party actions are pinned to full commit SHAs resolved from the upstream repositories through the GitHub API on 2026-09-13 (annotated tags dereferenced). No SHA was invented.

| Action | Current reference | Proposed reference | Upstream verified |
|---|---|---|---|
| actions/checkout | v4 | 11d5960a326750d5838078e36cf38b85af677262 (v4.4.0) | YES |
| actions/setup-python | v5 | a26af69be951a213d495a4c3e4e4022e16d87065 (v5.6.0) | YES |
| actions/upload-artifact | v4 | ea165f8d65b6e75b540449e92b4886f43607fa02 (v4.6.2) | YES |
| azure/login | v2 | 7184910d9eb2b1c5e48f7073824a90609bb9b6d6 (v2.3.1) | YES |
| github/codeql-action/{init,autobuild,analyze} | v3 | faaca9a8f6edddba5725ffe5adefdab6669a2eca (v3 tag object c20e34f4) | YES |
| actions/dependency-review-action | v4 | 2031cfc080254a8a887f58cffee85186f0e49e48 (v4.9.0) | YES |
| stackhawk/hawkscan-action | v2.1.3 | c1e45bcbf45150d9bb7b3c173267fb92f0e651e5 (v2.1.3) | YES |
| zaproxy/action-api-scan | v0.9.0 | 77dfa9a647bb0f583e39d4df8531634d6ddd8df4 (v0.9.0) | YES |

Dependabot (`.github/dependabot.yml`, if present) or a monthly review should refresh the pins; a pinned SHA does not update itself.

## 6. Workflow execution protection review (4F)

The backend repository is **public**. Facts that follow: `pull_request` runs from forks receive a read-only token and no secrets (GitHub default), so pr-tests, codeql, pdf-linux, security-scan and dependency-review are safe for fork PRs; no workflow uses `pull_request_target` or `repository_dispatch`; every privileged workflow (container-release, deploy-backend, dev-release, migration-preflight, prod-migration, stackhawk, zap) is `workflow_dispatch`, `workflow_call` or `push`-to-main only, and the Azure jobs additionally sit behind the `development` / `production` environments (1 required reviewer each).

```
SENSITIVE_WORKFLOWS = container-release, deploy-backend, dev-release, migration-preflight, prod-migration, stackhawk-scan, zap-scan
MANUAL_TRIGGER_WORKFLOWS = container-release, deploy-backend, dev-release, migration-preflight (call), prod-migration, stackhawk-scan, zap-scan, security-nightly, pdf-linux, convergence-fixture
PULL_REQUEST_TARGET_WORKFLOWS = none
UNTRUSTED_TRIGGER_RISK = LOW after this lane (fork PRs cannot reach secrets or privileged jobs; dispatch requires write access; inputs no longer reach shells unvalidated)
WORKFLOW_EXECUTION_POLICY_RECOMMENDATION = require approval for first-time contributors' workflow runs (repository setting); restrict `workflow_dispatch` to maintainers via branch protection on main; keep environment reviewers
HUMAN_CONFIGURATION_REQUIRED = YES (repository settings; not changed by this lane)
```

Additional governance question for the owner: confirm that public visibility of the backend repository is intended, given that `docs/` contains contract-programme material.

## 7. Branch protection (4G)

```
BRANCH_PROTECTION_CURRENT = none on main (API 404), rulesets [] (re-verified 2026-09-13)
BRANCH_PROTECTION_RECOMMENDED = PR required; 1 independent approval; dismiss stale approvals and require approval of the most recent reviewable push; require code-owner review; required checks: "Backend Tests / pytest", "CodeQL Analysis", "Dependency Review", "Security Scan / sast", "Linux PDF Rendering / render" (when app/reports/** changes); conversation resolution; force pushes and deletion disabled; include administrators; restrict who can push
BRANCH_PROTECTION_HUMAN_ACTION_REQUIRED = YES (repository administrator; the builder does not change repository governance)
```

CODEOWNERS now states this enforcement status instead of implying protection exists.

## 8. Other findings in this lane

- **AUD-15** (unauthenticated `/api/config`, `/api/tefca/status`): CONFIRMED, deliberately accepted. `/api/config` exists so a bundle can detect the backend it reached before login and returns only environment name, version and the host the caller already dialled; `/api/tefca/status` is consumed by the pre-login shell. No credentials or data. Recommendation only: rename the `pecos` connector label to reflect that it is an NPPES proxy (product wording, outside this lane).
- **AUD-16**: the workflow_call description and the dev-release header comment now describe the real guard; `deploy-prod` additionally requires `github.event_name == 'workflow_dispatch'`, so a reusable call cannot reach it even with `environment: production`. Evidence-only scan steps use `continue-on-error: true` instead of `|| true`, so their status is visible in the run.

## 9. Verification performed by the builder

- YAML parse of all 13 workflows after patching.
- Re-scan of every `run:` block for `inputs.*`, `secrets.*`, `github.event.*`, `github.head_ref`, `env.*` expressions: none remain except workflow-constant `env.*` values.
- No application code changed in this lane except `.github/CODEOWNERS` text; Task 1 to 6 paths untouched.
