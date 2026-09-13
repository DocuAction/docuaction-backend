# Deployment Provenance (Lane B, 2026-09-13)

Builder remediation record for AUD-20260913-05 (backend part), -06 (backend part is in Lane A), -07. Builder session = MAKER; the independent checker re-verifies against the PR head SHA.

## 1. Required chain

```
SOURCE SHA -> WORKFLOW RUN -> CONTAINER BUILD -> IMAGE TAG -> IMAGE DIGEST -> DEV DEPLOYMENT -> DEV RUNTIME
```

What this lane adds so every link is machine-checkable:

- `Dockerfile` takes `ARG GIT_SHA` (default `unknown`), exports it as `ENV GIT_SHA` and as the OCI label `org.opencontainers.image.revision`.
- `container-release.yml` passes `--build-arg GIT_SHA=<git rev-parse HEAD>` to both the local smoke build and the `az acr build` push.
- `/health` returns `git_sha` (from the image environment). An image not built by the workflow reports `unknown`.
- `deploy-backend.yml` gains a provenance gate after the health wait: the runtime `git_sha` must start with the deployed image tag; `unknown` or a different commit fails the deployment and triggers the existing rollback path.
- `tests/test_health_provenance.py` pins the contract.

## 2. State of the current DEV runtime (declared, not attributed to CI)

| Link | Value | Status |
|---|---|---|
| SOURCE SHA | 0cf028405299b1a132b664d9495c3c4ab6a0a415 (by ACR tag name only) | DECLARED |
| WORKFLOW RUN | none: image built by a manual `az acr build` on 2026-09-12T00:23Z | **MANUAL BUILD** |
| CONTAINER BUILD | ACR quick task (manual) | MANUAL |
| IMAGE TAG | `0cf0284` | ok |
| IMAGE DIGEST | `sha256:4384b211ea62bbdd3412ed919ab205920e3da092176207347f966fbdbdc47d8d` | ok |
| DEV DEPLOYMENT | App Service container config set manually 2026-09-12T17:08 | MANUAL |
| DEV RUNTIME | `/health` 200, version 6.0.0, no `git_sha` field (image predates this lane) | UNATTRIBUTED |

The dev-release runs 34661300993, 34573512155, 34571780065 and 34566719623 were cancelled; run 34743956579 (for main 75383cf) was still pending with 0 jobs at the time of writing. The last successful container-release run is from 2026-09-01. Until a workflow-built image is deployed, the DEV runtime must be described as manually built.

## 3. OIDC (5A)

Verified read-only: `azure/login` uses `vars.AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` from the `development` and `production` environments (distinct client ids; PROD has a separate `AZURE_MIGRATION_CLIENT_ID`); no `AZURE_CREDENTIALS` secret exists. The federated credential subjects on the Azure side (repository and environment restriction) were not read in this lane because it requires Graph directory permissions the builder identity does not hold; HUMAN_ACTION: confirm that each federated credential's subject is `repo:DocuAction/docuaction-backend:environment:<development|production>` and nothing broader.

## 4. Azure Static Web Apps (5B)

```
CURRENT_SWA_AUTH_MODE = deployment token (secrets.SWA_DEV_TOKEN / SWA_PROD_TOKEN) consumed by Azure/static-web-apps-deploy
SUPPORTED_ALTERNATIVE = none for content upload on SWA Free with provider SwaCli (both SWAs: docuaction-frontend-dev, docuaction-frontend; sku Free; provider SwaCli); the SWA CLI and the GitHub action both require the deployment token; Azure OIDC login does not upload content
IDENTITY_TOKEN/OIDC_SUPPORTED_FOR_CURRENT_SETUP = NO
DEPLOYMENT_TOKEN_REQUIRED = YES
TOKEN_ROTATION_AVAILABLE = YES (az staticwebapp secrets reset-api-key --name <swa> --resource-group <rg>, then update the GitHub secret)
ENVIRONMENT_PROTECTION_PRESENT = production job: environment "production" declared; required reviewers NOT available on the frontend repository (private, GitHub Free); dev job: environment "development" added by the frontend hardening PR (deployment recording only)
PR_SECRET_EXPOSURE_RISK = LOW: deploy-frontend.yml has no pull_request trigger; tokens are used only in with: inputs, never echoed
```

Decision: keep the deployment token. It stays a GitHub secret, is never printed, is scoped to the two deploy jobs, and rotation is documented above. HUMAN_ACTION: rotate both tokens on a schedule and after any contributor change; consider GitHub Pro/Team for environment reviewers on the private frontend repository.

## 5. Environment protection (5D)

Backend: `development` and `production` each require reviewer `DocuAction`; branch policy `custom_branch_policies: true`. Self-review prevention is not configurable on this plan for the reviewer role; HUMAN_ACTION: ensure the approving human is not the author of the release.

## 6. Migration safety (5E)

- No injection: operator inputs reach shells only as validated environment variables (Lane A).
- Explicit environment: `environment: development` / `production` on every Azure job.
- Safe target: `head` only; `expected_current` is a precondition validated against real revision identifiers.
- Ownership: schema owner identity via OIDC for PROD; `DB_APP_ROLE` required so grants target the runtime role.
- Entity Intelligence: `EntityIntelligenceBase` metadata is not part of the Alembic environment; no Alembic revision creates EI tables; the EI branch's own `migrations/apply.py` refuses non-local database URLs. `alembic upgrade head` on main or on the EI branch cannot create EI schema in the shared QA database. Feature flags are not relied on for this.

## 7. Frontend chain

Frontend hardening PR (separate repository) writes `out/build-info.json` (git_sha, run_id, run_attempt, ref, environment) into every workflow build and the DEV verification step checks that the served file names the workflow's commit. The current DEV SWA content (Last-Modified 2026-09-12 02:56:48 GMT) was uploaded with the SWA CLI by hand and is therefore MANUAL / UNATTRIBUTED until the next workflow deployment.

## 8. Runtime environment flags fail closed (AUD-20260913-18, documentation)

Boolean settings such as `ENTITY_INTELLIGENCE_ENABLED` accept only `true/false/1/0/yes/no/on/off` (case-insensitive). Any other string (an empty value, a typo such as `maybe`) makes `Settings()` raise a validation error at process start, so the container exits instead of guessing. Operators diagnosing a container that restarts immediately after an app-settings change should check the flag values first; the App Service log shows the pydantic `ValidationError` naming the field. The default for every Entity Intelligence flag is `false`; the flag is deliberately absent from the DEV App Service settings.
