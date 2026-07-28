# GitHub Repository Secrets

Secrets required by the deployment and scanning workflows. None of these belong in
`.env`, in code, or in a commit. Configure them at
**Settings → Secrets and variables → Actions → New repository secret**.

## Backend (`DocuAction/docuaction-backend`)

| Secret | Used by | Description | Where to get it |
|---|---|---|---|
| `AZURE_CREDENTIALS` | `deploy-backend.yml` (both deploy jobs) | Service principal JSON for `azure/login@v2`. Needs Contributor on `rg-docuaction-dev` and `rg-docuaction-prod`. | `az ad sp create-for-rbac --sdk-auth` (see below) |
| `FRONTEND_REPO_TOKEN` | `security-nightly.yml` | Read access to the frontend repo so the nightly scan covers both codebases. **Optional** — without it the nightly scans backend only. | Fine-grained PAT, `Contents: Read` on `docuaction-frontend` |

`SWA_PROD_TOKEN` / `SWA_DEV_TOKEN` are **not** needed in the backend repo — they
belong to the frontend repo only.

## Frontend (`DocuAction/docuaction-frontend`)

| Secret | Used by | Description | Where to get it |
|---|---|---|---|
| `SWA_DEV_TOKEN` | `deploy-frontend.yml` → `deploy-dev` | Deployment token for the **dev** Static Web App `docuaction-frontend-dev` (`rg-docuaction-dev`). | `az staticwebapp secrets list --name docuaction-frontend-dev --resource-group rg-docuaction-dev --query "properties.apiKey" -o tsv` |
| `SWA_PROD_TOKEN` | `deploy-frontend.yml` → `deploy-prod` | Deployment token for the **prod** Static Web App `docuaction-frontend` (`rg-docuaction-prod`). | `az staticwebapp secrets list --name docuaction-frontend --resource-group rg-docuaction-prod --query "properties.apiKey" -o tsv` |

## Creating `AZURE_CREDENTIALS`

```bash
az ad sp create-for-rbac \
  --name "gh-actions-docuaction-deploy" \
  --role contributor \
  --scopes /subscriptions/<SUB_ID>/resourceGroups/rg-docuaction-dev \
           /subscriptions/<SUB_ID>/resourceGroups/rg-docuaction-prod \
  --sdk-auth
```

Paste the entire JSON object — including the braces — as the secret value.

Scope it to the two resource groups, not the whole subscription. A CI principal
with subscription-wide Contributor is a standing path to every resource you own,
and it will be the first thing an assessor asks about.

## GitHub Environments

Two environments are referenced by the workflows and must exist at
**Settings → Environments**:

| Environment | Referenced by | Recommended configuration |
|---|---|---|
| `development` | `deploy-backend.yml` → `deploy-dev` | No approval needed |
| `production` | `deploy-backend.yml` → `deploy-prod`, `deploy-frontend.yml` → `deploy-prod` | **Add a required reviewer.** This is the only thing standing between a mis-clicked workflow_dispatch and a production deploy. |

Environment-scoped secrets override repository secrets of the same name, which is
the cleaner way to hold distinct dev and prod credentials if you later split them.

## What is deliberately NOT here

- **Application runtime secrets** (`SECRET_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`,
  SendGrid, Perigon) are **not** GitHub secrets. They live in App Service settings as
  Key Vault references, resolved at runtime by the app's managed identity. The
  pipeline never sees them, and the deployment artifact is verified to contain no
  `.env` file before it is uploaded.
- **Database credentials.** No workflow connects to a database. Migrations are not
  run by CI.

## Rotation

Rotate `AZURE_CREDENTIALS` on the same schedule as other privileged credentials, and
immediately if a workflow log is ever made public. SWA deployment tokens can be
regenerated from the portal or with `az staticwebapp secrets reset-api-key`; doing so
invalidates the old token, so update the GitHub secret in the same change.

Related: `docs/deployment/azure-deployment-guide.md` for the manual deploy recipe and
the `--clean` rule (prod only — it breaks Oryx-build dev).
