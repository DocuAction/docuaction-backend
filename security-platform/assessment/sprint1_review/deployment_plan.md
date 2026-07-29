# Sprint 1 — Deployment Plan

**Status: NOT DEPLOYED.** Code is merged, tagged, and pushed. Awaiting production deploy approval.
**Date:** 2026-07-26 · **Release tag (both repos): `sprint1-security-v1.0`**

| | Backend | Frontend |
|---|---|---|
| Repo | `DocuAction/docuaction-backend` | `DocuAction/docuaction-frontend` |
| Branch | `security/pre-azure-hardening` | `main` |
| Merge commit | **`fafd02f`** | **`ed6c035`** |
| Tag | `sprint1-security-v1.0` → `fafd02f` | `sprint1-security-v1.0` → `ed6c035` |
| Pushed | ✅ `da577f1..fafd02f` | ✅ `b3fde75..ed6c035` |

**Recommended sequence: DEV FIRST, validate, then PRODUCTION.**

---

## 1. Backend deployment plan

### 1.1 Pre-deploy — record the rollback point

**Current active production deployment (read live 2026-07-26):**

| Field | Value |
|---|---|
| Active deployment ID | **`Docuaction/6`** |
| Deployed | 2026-07-17T15:32:20Z |
| Method | **OneDeploy** (`az webapp deploy --type zip`) |
| Status | 4 (success) |
| Deployment records retained | 7 |

**This is the rollback reference. Record it in the release ticket before touching anything.**

Current production site configuration — capture for rollback comparison:

```bash
az webapp show -n Docuaction -g rg-docuaction-prod \
  --query "siteConfig" -o json > prod-siteconfig-preSprint1.json
```

Verified values as of 2026-07-26:

```json
{ "linuxFxVersion": "PYTHON|3.12",
  "appCommandLine": "python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000",
  "alwaysOn": true, "healthCheckPath": "/health",
  "minTlsVersion": "1.2", "ftpsState": "FtpsOnly" }
```

> ⚠️ **Configuration drift noted, not corrected.** The live startup command lacks the
> `--workers 4 --timeout 120` flags that `azure-deployment-guide.md` §6 specifies. Sprint 1
> does not change the startup command and **this deploy must not "fix" it** — a worker-count
> change is a separate, load-tested decision. Recorded here so it is not mistaken for
> drift introduced by this release.

### 1.2 Pre-deploy GATE — Key Vault reference resolution

**Mandatory.** SEC-01 (`9e041df`) makes an unresolved Key Vault reference a hard startup
failure. If a reference has broken since the last check, **the site will not come back up
after restart.**

```bash
SUB=$(az account show --query id -o tsv)
az rest --method get --uri \
 "https://management.azure.com/subscriptions/$SUB/resourceGroups/rg-docuaction-prod/providers/Microsoft.Web/sites/Docuaction/config/configreferences/appsettings?api-version=2022-03-01" \
 --query "value[].{name:name,status:properties.status,detail:properties.details}" -o table
```

**Required result: 4 rows, every `status` = `Resolved`.** Verified 2026-07-26:

| Setting | Vault | Secret | Status |
|---|---|---|:--:|
| `SECRET_KEY` | `docuaction-kv-prod` | `SECRET-KEY` | ✅ Resolved |
| `ANTHROPIC_API_KEY` | `docuaction-kv-prod` | `ANTHROPIC-API-KEY` | ✅ Resolved |
| `AZURE_AD_CLIENT_SECRET` | `docuaction-kv-prod` | `AZURE-AD-CLIENT-SECRET` | ✅ Resolved |
| `SENDGRID_API_KEY` | `docuaction-kv-prod` | `SENDGRID-API-KEY` | ✅ Resolved |

**Re-run this immediately before deploying.** The table above is a point-in-time snapshot;
an RBAC or vault-firewall change since then would turn a green check into a failed startup.

**If any row is not `Resolved`: STOP. Do not deploy.** Fix the reference first — do not
work around it by reverting SEC-01, which would let the app boot signing JWTs with a
publicly derivable constant (the exact defect the guard exists to prevent).

### 1.3 Deploy — per `azure-deployment-guide.md` §4 and §7

Build the artifact with dependencies vendored as Linux cp312 wheels:

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git checkout sprint1-security-v1.0      # deploy the tag, not a moving branch

# §4.1 — resolve Linux cp312 wheels into ./pydeps
python -m pip download --requirement requirements.txt --dest ./wheelhouse \
  --only-binary=:all: --platform manylinux2014_x86_64 \
  --python-version 312 --implementation cp --abi cp312

python -m pip install --requirement requirements.txt --target ./pydeps \
  --no-index --find-links ./wheelhouse

# §4.2 — assemble the zip
zip -r deploy.zip app/ alembic/ alembic.ini requirements.txt pydeps/ \
  -x '*/__pycache__/*' '*.pyc' '.env' '.env.*' 'tests/*'

sha256sum deploy.zip          # RECORD in the release ticket
```

> **Do not rename `pydeps/` to `antenv`** — the Oryx/App Service startup optimiser treats a
> top-level `antenv` as a managed virtualenv and can skip or override it (guide §4).

> **`-x '.env' '.env.*'`** is a security control, not tidiness: the working-tree `.env`
> holds live Anthropic and OpenAI keys. Confirm with
> `unzip -l deploy.zip | grep -c '\.env'` → must be **0**.

Deploy (OneDeploy — matches the current production method):

```bash
az webapp deploy \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --src-path deploy.zip \
  --type zip

az webapp log tail --resource-group rg-docuaction-prod --name Docuaction
```

### 1.4 Post-deploy verification

| # | Check | Expected |
|--:|---|---|
| 1 | `curl -sSf https://api-prod.docuaction.io/health` | **200** |
| 2 | `curl -sSf https://docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net/health` | **200** (a 400 = `ALLOWED_HOSTS` gap) |
| 3 | Startup logs: `Loaded:` count | **22**, with **zero** `Skipped` |
| 4 | Startup logs: `UNRESOLVED Azure Key Vault reference` | **absent** |
| 5 | Startup logs: `Traceback` / `CRITICAL` / `FATAL` | **absent** |
| 6 | `GET /api/v1/case-management/info` **no token** | **403** |
| 7 | Same **with** a valid token | **200** |
| 8 | `GET /api/tefca/registry/stats` | **200** |
| 9 | `GET /api/tefca/registry/entities` | **200** |
| 10 | `POST /api/auth/login` bad creds | **401** (not 500) |
| 11 | Entra SSO login | succeeds |
| 12 | `GET /api/tefca/dashboard/summary` | **200** |
| 13 | `GET /api/v1/bulletin/health` | **200** |
| 14 | APScheduler jobs registered (`ENABLE_SCHEDULER=true`) | present in startup logs |
| 15 | `python -m alembic current` via App Service SSH | expected head (unchanged — no migration in this release) |
| 16 | **Wait 5 minutes**, then App Insights | no new exception types |
| 17 | Swagger gating matches `ENABLE_DOCS=false` | `/docs` not exposed |
| 18 | Defender for Cloud | no new high-severity alerts |

> **Rate-limiter artifact — do not misread it.** The in-memory limiter is **10 requests /
> 5-second window** on the free tier. Running checks 6–13 back-to-back will produce **429s
> that are not failures**. Pace at ≤8 requests per 6 seconds, or the results are worthless.
> This bit me during local validation.

> **Expected and correct:** a rise in 403s on `/api/v1/case-management/*` in App Insights.
> That is AUTHZ-01 working, not a regression.

### 1.5 Backend rollback

**Identify the previous artifact** — the pre-deploy record from §1.1 (`Docuaction/6`,
2026-07-17T15:32:20Z). List deployments any time:

```bash
SUB=$(az account show --query id -o tsv)
az rest --method get --uri \
 "https://management.azure.com/subscriptions/$SUB/resourceGroups/rg-docuaction-prod/providers/Microsoft.Web/sites/Docuaction/deployments?api-version=2022-03-01" \
 --query "value[].{id:name,active:properties.active,end:properties.end_time,msg:properties.message}" -o table
```

**Rebuild-and-redeploy is the reliable path** (OneDeploy history does not retain the zip):

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git checkout e3f9e5b            # the pre-Sprint-1 commit
# repeat the §1.3 build, then:
az webapp deploy --resource-group rg-docuaction-prod --name Docuaction \
  --src-path deploy.zip --type zip
```

Verify after rollback: `/health` → 200; `Loaded:` → 22; `/api/v1/case-management/info`
**without** a token returns **200 again** (confirming the gate is gone — i.e. the rollback
took effect, and the Critical finding is reopened).

**Per-finding rollback** if only one change is at fault — no full-stack revert needed:

| Symptom | Revert |
|---|---|
| Site won't start, `UNRESOLVED ... Key Vault reference` | **Do NOT revert.** Fix the reference or set the app setting to a real value. |
| `/case-management` 403s for logged-in users | Deploy the frontend (`ed6c035`). Only if impossible: revert `4879e3e`. |
| Notes contain `[PATIENT_LAST]` / read wrongly | Revert `da9ae7c`, or one-line disable: `build_phi_map()` → `{}` |
| Admin user deletion fails | Revert `4893f1f` (cleanest — stack tip) |
| Anything else | `git revert -m 1 fafd02f` (whole sprint) |

Revert in **reverse stack order**; `4879e3e` may conflict in `routes.py` if `da9ae7c` is
still present. Follow `docs/deployment/rollback-procedures.md` for the operational wrapper.

---

## 2. Frontend deployment plan

Now documented in `backend/docs/deployment/azure-deployment-guide.md` **§13** (added
2026-07-26, closing the gap flagged in the pre-merge review).

### 2.1 Pre-deploy — record current state

| | Production | Development |
|---|---|---|
| SWA | `docuaction-frontend` | `docuaction-frontend-dev` |
| RG | `rg-docuaction-prod` | `rg-docuaction-dev` |
| Host | `witty-tree-0a448a70f.7.azurestaticapps.net` | `witty-dune-0dd70870f.7.azurestaticapps.net` |
| Custom domain | **`app.docuaction.io`** (live) | none |
| SKU | **Free** | **Free** |
| Linked repo/branch | **none** | **none** |

**Two constraints that follow, both verified:**

1. **No CI.** `.github/workflows/` has only `codeql.yml`, `dependency-review.yml`,
   `security-scan.yml`. Nothing deploys on push; SWA CLI is the only path.
2. **Free SKU keeps no deployment history and has no environment rollback.** There is no
   previous version to promote. **Record the currently-deployed commit before deploying** —
   without it you have no rollback target. The last frontend release before this one is
   `b3fde75`.

### 2.2 Build

```bash
cd "C:/Imran_Coding projects/DocuAction/frontend"
git checkout sprint1-security-v1.0
npm ci
npm run build            # -> static export into ./out
```

Expect **exit 0** and exactly **3** warnings (the `redirects`/`rewrites`/`headers` notices
that don't apply under `output: 'export'`). More than 3 = investigate.

### 2.3 Tokens

```bash
export SWA_DEV_TOKEN=$(az staticwebapp secrets list --name docuaction-frontend-dev \
  --resource-group rg-docuaction-dev --query "properties.apiKey" -o tsv)

export SWA_PROD_TOKEN=$(az staticwebapp secrets list --name docuaction-frontend \
  --resource-group rg-docuaction-prod --query "properties.apiKey" -o tsv)
```

Use env vars, not literal arguments — a token pasted on a command line lands in shell
history. If one is exposed: `az staticwebapp secrets reset-api-key`.

### 2.4 Deploy — DEV first

```bash
# DEV
npx @azure/static-web-apps-cli deploy ./out \
  --deployment-token "$SWA_DEV_TOKEN" --env production

# --- validate dev (§2.5) before continuing ---

# PRODUCTION
npx @azure/static-web-apps-cli deploy ./out \
  --deployment-token "$SWA_PROD_TOKEN" --env production
```

`--env production` is correct for **both** — it names the environment *within* each Static
Web App, not the tier. On Free SKU it is the only environment available.

> **These commands have not been executed against these resources.** Treat the dev run as
> a validation run and correct guide §13 from what actually happens.

### 2.5 Post-deploy verification

1. `https://witty-tree-0a448a70f.7.azurestaticapps.net` loads
2. `https://app.docuaction.io` loads
3. Login succeeds; token stored under the `token` localStorage key
4. **`/case-management` while logged in** — DevTools → Network shows
   `Authorization: Bearer …` on `/api/v1/case-management/*` and responses are **200**, not
   403. *This is the check that proves AUTHZ-01's two halves are in sync.*
5. TEFCA Registry pages load: `/tefca-registry`, `/entities`, `/issues`, `/verification`
6. No CORS errors (API `ALLOWED_ORIGINS` = `https://app.docuaction.io`)
7. **Hard-refresh (Ctrl-F5)** at least one page — otherwise you may be validating a cached
   bundle and learning nothing

### 2.6 Frontend rollback

No deployment history on Free SKU → rebuild and redeploy from the known-good commit:

```bash
cd "C:/Imran_Coding projects/DocuAction/frontend"
git checkout b3fde75          # last pre-Sprint-1 frontend commit
npm ci && npm run build
npx @azure/static-web-apps-cli deploy ./out \
  --deployment-token "$SWA_PROD_TOKEN" --env production
git checkout main
```

Or revert on `main`: `git revert ed6c035 && npm ci && npm run build && <deploy>`.

⚠️ **Rolling back the frontend alone re-breaks `/case-management`** if the backend gate is
live — the page would stop sending the header while the backend still requires it. Roll the
**backend** back first, or both together.

---

## 3. Deployment sequence

```
1. Pre-deploy gate: Key Vault references 4/4 Resolved     ← STOP if not
2. Record rollback points: backend Docuaction/6 · frontend b3fde75
3. DEV:  backend → validate → frontend → validate
4. PROD: backend → verify /health + 22 modules → frontend → verify §2.5
5. Wait 5 minutes → App Insights review
```

**Ordering rule, non-negotiable:** backend **first or simultaneously**. Never frontend
alone before the backend.

| Order | Result |
|---|---|
| Backend first, then frontend | ✅ Brief window where the page 403s; resolves on frontend deploy |
| Both together | ✅ Safest |
| Frontend first | ✅ Safe — unused header is ignored by the old backend |
| **Backend only, frontend never** | ❌ `/case-management` 403s for every user, indefinitely |

**Estimated time:** ~60–80 min per environment including validation (backend zip build
15–20 min dominates), plus the 5-minute App Insights soak.

---

## 4. Concerns before production deploy

1. **No automated test covers any Sprint 1 change.** The 17 passing tests are for bulletin
   intelligence. Post-deploy verification is the only safety net — treat §1.4 and §2.5 as
   mandatory, not advisory.
2. **The frontend deploy commands are documented but unexecuted.** Validate on dev first.
   This is the strongest argument for the dev-first sequence.
3. **SEC-01 converts a silent security failure into a loud availability failure.** The §1.2
   gate is mandatory, and any future Key Vault work can now take the site down.
4. **DP-02 can alter generated clinical content.** If the patient population plausibly
   includes surnames colliding with clinical vocabulary (Stone, Rash, Long, Short, Gray,
   Bell, Cross, Marsh, Back, Head), generate one note in dev and read it before promoting.
5. **Rate-limiter 429s will masquerade as failures** during rapid verification. Pace at ≤8
   requests / 6 s.
6. **Deploying from `security/pre-azure-hardening`, not `main`.** That branch is 6 commits
   ahead of its remote and is *not* merged to `main`. Whether `main` should receive this is
   a separate decision.
7. **Backend repo working tree has 12 modified + 72 untracked unrelated files.** None are
   Sprint 1 and none are in the merge, but the zip is built from the **working tree** —
   `git checkout sprint1-security-v1.0` before building, and confirm
   `unzip -l deploy.zip | grep -c '\.env'` is **0**.
8. **Startup-command drift** (§1.1) — pre-existing, must not be "fixed" during this deploy.
9. **Config drift risk between dev and prod:** dev has **no Key Vault references at all**
   (6 plaintext secrets), so a dev deploy **does not exercise the SEC-01 guard**. A clean
   dev run is not evidence that prod's Key Vault path is healthy — only the §1.2 gate is.

---

## 5. What this release does NOT include

Unchanged and still open — see `SPRINT1_RELEASE_REPORT.md` §12:

- **Anthropic BAA** — not executed. DP-02 cannot close without it; clinical narrative still
  egresses.
- **Key rotation** — no key rotated. `SECRETS_MANAGEMENT.md` §4 has the checklist.
- `DATABASE_URL` plaintext in prod **and** dev; dev entirely un-vaulted.
- **No audit hash-chain** — tampering remains undetectable.
- `users.allowed_modules` unenforced server-side; no case-management role tiering.
- `AZURE_AD_CLIENT_SECRET` expiry is invisible to the SEC-01 guard (an expired secret still
  *resolves*).
